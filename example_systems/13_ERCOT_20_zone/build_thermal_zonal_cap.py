"""Build the per-zone thermal capacity cap used by the case settings.

    python build_thermal_zonal_cap.py                          # 5000 MW per zone
    python build_thermal_zonal_cap.py 3000                     # a different cap, in MW
    python build_thermal_zonal_cap.py 5000 results/x/capacity.csv   # also report which zones
                                                               # exceed the cap in a run

Writes, next to this script:

    resources_thermal_zonal_cap[_<N>GW]/   copy of resources/ with Max_Cap_<z> tag
                                           columns added to Thermal.csv
    policies_thermal_zonal_cap[_<N>GW]/    Maximum_capacity_requirement.csv, one row
                                           per zone

The 5 GW cap uses the unsuffixed folder names, which settings/genx_settings.yml points to;
other caps get a suffix such as _3GW. To use one, set ResourcesFolder and PoliciesFolder in
settings/genx_settings.yml accordingly.

The cap is a GenX MaxCapReq policy rather than Max_Cap_MW, because each zone has two or
three thermal resources (CCGT, OCGT and in some zones coal). For each requirement p:

    sum over resources y tagged Max_Cap_p of eTotalCap[y]  <=  Max_MW[p]

Requirement p tags exactly the thermal resources in zone p.
"""
import shutil
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CAP_MW = float(sys.argv[1]) if len(sys.argv) > 1 else 5000.0

# 5 GW uses the unsuffixed names; other caps are suffixed in GW (e.g. _3GW, _2p5GW).
cap_gw = CAP_MW / 1000.0
gw_str = f"{cap_gw:g}".replace(".", "p")
SUFFIX = "" if CAP_MW == 5000.0 else f"_{gw_str}GW"

SRC_RES = HERE / "resources"
DST_RES = HERE / f"resources_thermal_zonal_cap{SUFFIX}"
DST_POL = HERE / f"policies_thermal_zonal_cap{SUFFIX}"

if not SRC_RES.exists():
    raise SystemExit(f"resources/ not found at {SRC_RES}")

# --- resources: copy, then tag Thermal.csv -------------------------------------------
if DST_RES.exists():
    shutil.rmtree(DST_RES)
shutil.copytree(SRC_RES, DST_RES)

thermal = pd.read_csv(DST_RES / "Thermal.csv")
zones = sorted(thermal.Zone.astype(int).unique())
print(f"Thermal resources: {len(thermal)} across {len(zones)} zones")
print(thermal.assign(tech=thermal.Resource.str.replace(r"^ERCOT\d+_", "", regex=True))
      .groupby("Zone").size().rename("resources_per_zone").describe()[["min", "max"]]
      .to_string())

# One tag column per zone: Max_Cap_<i> = 1 for that zone's thermal resources, else 0.
# Tag index must match the MaxCapReqConstraint number in the policy file below.
for i, z in enumerate(zones, start=1):
    thermal[f"Max_Cap_{i}"] = (thermal.Zone.astype(int) == z).astype(int)

tagged = sum(int(thermal[f"Max_Cap_{i}"].sum()) for i in range(1, len(zones) + 1))
if tagged != len(thermal):
    raise SystemExit(f"tagging error: {tagged} tags for {len(thermal)} resources "
                     f"-- every resource must be tagged exactly once")

thermal.to_csv(DST_RES / "Thermal.csv", index=False)
print(f"\nWrote {DST_RES.name}/Thermal.csv with {len(zones)} Max_Cap_* tag columns")

# --- policies: one requirement per zone ----------------------------------------------
DST_POL.mkdir(exist_ok=True)
req = pd.DataFrame({
    "MaxCapReqConstraint": range(1, len(zones) + 1),
    "ConstraintDescription": [f"zone{z}_thermal_total" for z in zones],
    "Max_MW": [CAP_MW] * len(zones),
})
req.to_csv(DST_POL / "Maximum_capacity_requirement.csv", index=False)
print(f"Wrote {DST_POL.name}/Maximum_capacity_requirement.csv: "
      f"{len(zones)} requirements at {CAP_MW:,.0f} MW each")

# --- optional: which zones exceed the cap in a given run (e.g. one without the cap) -----
ref = Path(sys.argv[2]) if len(sys.argv) > 2 else None
if ref is not None and not ref.is_absolute():
    ref = HERE / ref
if ref is not None and ref.exists():
    cap = pd.read_csv(ref)
    cap = cap[cap.Resource.astype(str) != "Total"].copy()
    cap["tech"] = cap.Resource.astype(str).str.replace(r"^ERCOT\d+_", "", regex=True)
    th = cap[cap.tech.isin(["CCGT", "OCGT", "Coal"])]
    by_zone = th.groupby("Zone").EndCap.sum().sort_values(ascending=False)
    over = by_zone[by_zone > CAP_MW]
    print(f"\nIn {ref}, {len(over)} of {len(by_zone)} zones exceed {CAP_MW:,.0f} MW of thermal:")
    for z, v in over.items():
        print(f"  zone {int(z):2d}: {v:9,.0f} MW  ({v / CAP_MW:.1f}x the cap)")
    print(f"  total thermal above the cap: {(over - CAP_MW).sum():,.0f} MW")
elif ref is not None:
    print(f"\n{ref} not found; skipping the comparison")
