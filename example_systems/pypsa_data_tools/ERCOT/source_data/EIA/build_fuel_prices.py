"""Monthly gas and coal prices for Texas from the EIA API.

    EIA_API_KEY=<key> python build_fuel_prices.py

Writes gas_price_tx_monthly.csv and coal_price_tx_quarterly.csv next to this script; the
notebook's assembly step builds Fuels_data.csv from them. A free API key is available at
https://www.eia.gov/opendata/register.php. Responses are cached as JSON next to this
script, with the key removed, so later runs need no key.

Gas uses the monthly shape of FUEL_YEAR (2022) scaled to the mean level of LEVEL_YEARS
(2022-2025, $3.45/MMBtu). 2022 alone was unusually expensive ($6.11/MMBtu, against $2.54,
$2.10 and $3.06 in 2023-2025). Scaling the 2022 shape keeps its seasonal pattern (a 1.9x
swing), which averaging months across years would flatten. 2021 (Winter Storm Uri,
$9.18/MMBtu) is excluded.

Coal uses the LEVEL_YEARS mean.

Series:
  - Gas: natural-gas/pri/sum, process PEU (price to electric power consumers), Texas, $/MCF.
  - Coal: coal/shipments/receipts, receipts at Texas plants, $/ton.
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests

EIA_DIR = Path(__file__).resolve().parent

# Model year -- supplies the seasonal shape, and must match era5_config.WEATHER_YEAR.
FUEL_YEAR = 2022
# Years averaged for the price LEVEL. 2021 excluded (Uri).
LEVEL_YEARS = (2022, 2023, 2024, 2025)

API_KEY = os.environ.get("EIA_API_KEY")
BASE = "https://api.eia.gov/v2"

# 1 MCF ~= 1.037 MMBtu (EIA standard heat content for pipeline gas).
MCF_PER_MMBTU = 1.037

# ERCOT coal plants burn a mix of PRB subbituminous (~17 MMBtu/ton) and Texas lignite
# (~13 MMBtu/ton). EIA's general ~19 MMBtu/ton average for utility coal receipts is too
# high for this mix; 17 is a PRB-weighted middle estimate, PRB dominating ERCOT receipts
# per EIA-923.
COAL_MMBTU_PER_TON = 17.0


def fetch(path, params, cache_name):
    """GET an EIA v2 endpoint, caching the raw JSON so re-runs need no key."""
    cache = EIA_DIR / cache_name
    if cache.exists():
        print(f"using cached {cache.name}")
        return json.loads(cache.read_text())
    if not API_KEY:
        sys.exit(f"{cache.name} not cached and $EIA_API_KEY is unset -- "
                 f"register at https://www.eia.gov/opendata/register.php")
    r = requests.get(f"{BASE}/{path}", params={**params, "api_key": API_KEY}, timeout=60)
    r.raise_for_status()
    payload = r.json()
    # EIA echoes the request back, api_key included; strip it before the cache hits disk.
    try:
        payload["request"]["params"]["api_key"] = "<redacted>"
    except (KeyError, TypeError):
        pass
    cache.write_text(json.dumps(payload))
    print(f"fetched -> {cache.name}")
    return payload


_lo, _hi = min(LEVEL_YEARS), max(LEVEL_YEARS)
gas_raw = fetch(
    "natural-gas/pri/sum/data/",
    {
        "frequency": "monthly",
        "data[]": "value",
        "facets[process][]": "PEU",
        "start": f"{_lo}-01",
        "end": f"{_hi}-12",
        "length": "5000",
    },
    f"gasprices_{_lo}_{_hi}.json",
)
gas_all = pd.DataFrame(gas_raw["response"]["data"])
gas_all = gas_all[gas_all["duoarea"] == "STX"].copy()
gas_all["value"] = pd.to_numeric(gas_all["value"], errors="coerce")
gas_all["period"] = pd.to_datetime(gas_all["period"], format="%Y-%m")
gas_all["usd_per_mmbtu"] = gas_all["value"] / MCF_PER_MMBTU
gas_all = gas_all.dropna(subset=["usd_per_mmbtu"]).sort_values("period")
gas_all["year"] = gas_all["period"].dt.year

# Level: mean across LEVEL_YEARS. Shape: FUEL_YEAR's own monthly curve.
level_mean = gas_all[gas_all.year.isin(LEVEL_YEARS)]["usd_per_mmbtu"].mean()
gas_tx = gas_all[gas_all.year == FUEL_YEAR][["period", "usd_per_mmbtu"]].reset_index(drop=True)
shape_mean = gas_tx["usd_per_mmbtu"].mean()
gas_tx["usd_per_mmbtu"] = gas_tx["usd_per_mmbtu"] * (level_mean / shape_mean)
print(f"\nGas level: {LEVEL_YEARS} mean ${level_mean:.2f}/MMBtu "
      f"(annual: {', '.join(f'{y}:${v:.2f}' for y, v in gas_all[gas_all.year.isin(LEVEL_YEARS)].groupby('year').usd_per_mmbtu.mean().items())})")
print(f"Gas shape: {FUEL_YEAR} monthly curve, rescaled by {level_mean / shape_mean:.3f} "
      f"(its own mean was ${shape_mean:.2f})")

if len(gas_tx) != 12:
    raise RuntimeError(
        f"expected 12 monthly Texas gas prices for {FUEL_YEAR}, got {len(gas_tx)}. "
        f"Fuels_data.csv needs one value per calendar month of the model year."
    )
print(f"\nGas ({FUEL_YEAR}, Texas, $/MMBtu): mean {gas_tx.usd_per_mmbtu.mean():.3f}, "
      f"min {gas_tx.usd_per_mmbtu.min():.3f}, max {gas_tx.usd_per_mmbtu.max():.3f}")

# Coal: mean over LEVEL_YEARS (prices vary little within the year)
coal_raw = fetch(
    "coal/shipments/receipts/data/",
    {
        "frequency": "quarterly",
        "data[]": "price",
        "start": f"{_lo}-Q1",
        "end": f"{_hi}-Q4",
        "length": "5000",
    },
    f"coalquarterly_{_lo}_{_hi}.json",
)
coal_df = pd.DataFrame(coal_raw["response"]["data"])
coal_tx = coal_df[coal_df["plantStateId"] == "TX"].copy()
coal_tx = coal_tx[coal_tx["price"] != "w"]  # 'w' = withheld
coal_tx["price"] = pd.to_numeric(coal_tx["price"], errors="coerce")
coal_tx = coal_tx.dropna(subset=["price"])
coal_tx["year"] = coal_tx["period"].str[:4].astype(int)
coal_tx = coal_tx[coal_tx.year.isin(LEVEL_YEARS)]

coal_quarterly = coal_tx.groupby("period")["price"].mean().reset_index()
coal_quarterly["usd_per_mmbtu"] = coal_quarterly["price"] / COAL_MMBTU_PER_TON
if coal_quarterly.empty:
    raise RuntimeError(f"no Texas coal receipts found for {LEVEL_YEARS}")
print(f"Coal ({LEVEL_YEARS} mean, Texas, $/MMBtu): {coal_quarterly.usd_per_mmbtu.mean():.3f} "
      f"(range {coal_quarterly.usd_per_mmbtu.min():.3f}-{coal_quarterly.usd_per_mmbtu.max():.3f} "
      f"over {len(coal_quarterly)} quarters)")

gas_tx.to_csv(EIA_DIR / "gas_price_tx_monthly.csv", index=False)
coal_quarterly.to_csv(EIA_DIR / "coal_price_tx_quarterly.csv", index=False)
print(f"\nSaved to {EIA_DIR}/gas_price_tx_monthly.csv and coal_price_tx_quarterly.csv")
