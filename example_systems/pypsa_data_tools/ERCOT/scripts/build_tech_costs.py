"""Technology costs from NLR ATB 2022 (via PUDL).

    python scripts/build_tech_costs.py

Writes outputs/atb_tech_costs_2022.csv, read by the notebook's assembly step. Technology
selection and cost recovery periods follow PyPSA-USA's ATB_TECH_MAPPER
(workflow/scripts/constants.py) and build_cost_data.py: one resource class per technology.

Settings:
  - Report year 2022: the costs as published in ATB 2022, not later editions' estimates for
    2022 (which differ substantially, e.g. 4-hour battery capex $2101/kW in ATB 2024 vs
    $1371/kW in ATB 2022).
  - Scenario Moderate, Market financials.
  - Cost recovery period (crp) 30 years, NLR's default; this is the financial period used
    to annualize capex, not the asset lifetime.
  - Technology names use ATB 2022 codes (CCAvgCF, CTAvgCF, newAvgCF2ndGen).
  - Nuclear is excluded.
"""
import pandas as pd
import numpy as np
from network_paths import OUTPUTS, SOURCE_DATA

RAW_OUT = str(OUTPUTS)
RAW_SRC = str(SOURCE_DATA)
RAW = RAW_OUT  # outputs of earlier steps

cost = pd.read_parquet(f"{RAW_SRC}/core_nrelatb__yearly_projected_cost_performance.parquet")
fin = pd.read_parquet(f"{RAW_SRC}/core_nrelatb__yearly_projected_financial_cases_by_scenario.parquet")

SCENARIO = "Moderate"
MODEL_CASE = "Market"
PROJECTION_YEAR = 2022  # near-term cost basis
REPORT_YEAR = 2022  # the report that published these costs, not a later retrospective

# (technology_description, technology_description_detail_1, cost_recovery_period_years)
TECH_MAP = {
    "Gas_CC": ("NaturalGas_FE", "CCAvgCF", 30),
    "Gas_CT": ("NaturalGas_FE", "CTAvgCF", 30),
    "Coal": ("Coal_FE", "newAvgCF2ndGen", 30),
    "onwind": ("LandbasedWind", "Class4", 30),
    "solar": ("UtilityPV", "Class5", 30),
    "battery": ("Utility-Scale Battery Storage", "4Hr Battery Storage", 20),
}

results = {}
for label, (tech, detail, crp) in TECH_MAP.items():
    sub = cost[
        (cost["report_year"] == REPORT_YEAR)
        & (cost["scenario_atb"] == SCENARIO)
        & (cost["model_case_nrelatb"] == MODEL_CASE)
        & (cost["projection_year"] == PROJECTION_YEAR)
        & (cost["technology_description"] == tech)
        & (cost["technology_description_detail_1"] == detail)
        & (cost["cost_recovery_period_years"] == crp)
    ]
    if sub.empty:
        print(f"WARNING: no cost rows found for {label} ({tech}, {detail}, crp={crp})")
        continue
    row = sub.iloc[0][["capex_per_kw", "opex_fixed_per_kw", "opex_variable_per_mwh",
                        "heat_rate_mmbtu_per_mwh", "fuel_cost_per_mwh"]].copy()

    fin_sub = fin[
        (fin["report_year"] == REPORT_YEAR)
        & (fin["scenario_atb"] == SCENARIO)
        & (fin["model_case_nrelatb"] == MODEL_CASE)
        & (fin["projection_year"] == PROJECTION_YEAR)
        & (fin["technology_description"] == tech)
        & (fin["cost_recovery_period_years"] == crp)
    ]
    if fin_sub.empty:
        # gas/coal at report_year=2022 have a SINGLE financial row with
        # cost_recovery_period_years = <NA> (not tied to any specific crp) rather than
        # one row per crp tier like wind/solar -- the exact-crp filter above misses this
        # entirely. Verified directly: NaturalGas_FE/Coal_FE at report_year=2022 each
        # have exactly one row, crp=<NA>, wacc_real=0.0394, no fixed_charge_rate. Fall
        # back to matching on technology alone when the exact-crp match is empty.
        fin_sub = fin[
            (fin["report_year"] == REPORT_YEAR) & (fin["scenario_atb"] == SCENARIO)
            & (fin["model_case_nrelatb"] == MODEL_CASE) & (fin["projection_year"] == PROJECTION_YEAR)
            & (fin["technology_description"] == tech)
        ]
    if not fin_sub.empty and fin_sub["fixed_charge_rate"].notna().any():
        annualization_rate = fin_sub["fixed_charge_rate"].median()
        rate_source = f"real ATB fixed_charge_rate at crp={crp}yr"
    elif not fin_sub.empty:
        wacc = fin_sub["wacc_real"].median()
        annualization_rate = wacc / (1 - (1 + wacc) ** (-crp))
        rate_source = f"computed CRF from real wacc_real={wacc:.4f} at crp={crp}yr (no fixed_charge_rate published)"
    elif label == "battery":
        # ATB has no financial-case rows for utility-scale batteries, so solar's real WACC
        # is used, with a 20-year cost recovery period.
        solar_fin = fin[
            (fin["report_year"] == REPORT_YEAR) & (fin["scenario_atb"] == SCENARIO)
            & (fin["model_case_nrelatb"] == MODEL_CASE) & (fin["projection_year"] == PROJECTION_YEAR)
            & (fin["technology_description"] == "UtilityPV") & (fin["cost_recovery_period_years"] == 30)
        ]
        wacc = solar_fin["wacc_real"].median()
        annualization_rate = wacc / (1 - (1 + wacc) ** (-crp))
        rate_source = f"no battery financial data in ATB; using solar's wacc_real={wacc:.4f} with crp=20yr"
    else:
        annualization_rate = None
        rate_source = "NOT FOUND"

    row["annualization_rate"] = annualization_rate
    row["crp_years"] = crp
    results[label] = row
    print(f"{label} ({tech}, {detail}, crp={crp}yr):")
    print(row)
    print(f"  source: {rate_source}")
    print()

cost_table = pd.DataFrame(results).T
cost_table.index.name = "technology"

# ---------------------------------------------------------------------------
# Fit battery costs across ATB duration tiers to separate power ($/kW) and energy ($/kWh)
# ---------------------------------------------------------------------------
batt_rows = cost[
    (cost["report_year"] == REPORT_YEAR)
    & (cost["scenario_atb"] == SCENARIO)
    & (cost["model_case_nrelatb"] == MODEL_CASE)
    & (cost["projection_year"] == PROJECTION_YEAR)
    & (cost["technology_description"] == "Utility-Scale Battery Storage")
    & (cost["cost_recovery_period_years"] == 20)
    & (cost["technology_description_detail_1"].str.contains("Hr Battery Storage", na=False))
].drop_duplicates(subset="technology_description_detail_1").copy()
batt_rows["duration_hr"] = batt_rows["technology_description_detail_1"].str.extract(r"(\d+)Hr").astype(int)
batt_rows = batt_rows.sort_values("duration_hr")

A = np.vstack([np.ones(len(batt_rows)), batt_rows["duration_hr"]]).T
power_cost_per_kw, energy_cost_per_kwh = np.linalg.lstsq(A, batt_rows["capex_per_kw"], rcond=None)[0]
residuals = batt_rows["capex_per_kw"].to_numpy() - (A @ [power_cost_per_kw, energy_cost_per_kwh])
print(f"Battery power/energy capex fit: power=${power_cost_per_kw:.2f}/kW, energy=${energy_cost_per_kwh:.2f}/kWh")
print(f"  duration tiers used: {batt_rows['duration_hr'].tolist()}, max residual: {np.abs(residuals).max():.4f}")

A2 = np.vstack([np.ones(len(batt_rows)), batt_rows["duration_hr"]]).T
power_fom_per_kw, energy_fom_per_kwh = np.linalg.lstsq(A2, batt_rows["opex_fixed_per_kw"], rcond=None)[0]
print(f"Battery power/energy fixed-OM fit: power=${power_fom_per_kw:.2f}/kW-yr, energy=${energy_fom_per_kwh:.2f}/kWh-yr")

cost_table.loc["battery", "power_cost_per_kw"] = power_cost_per_kw
cost_table.loc["battery", "energy_cost_per_kwh"] = energy_cost_per_kwh
cost_table.loc["battery", "power_fom_per_kw"] = power_fom_per_kw
cost_table.loc["battery", "energy_fom_per_kwh"] = energy_fom_per_kwh

cost_table.to_csv(f"{RAW}/atb_tech_costs_2022.csv")
print(f"\nSaved to {RAW}/atb_tech_costs_2022.csv")
print(cost_table)
