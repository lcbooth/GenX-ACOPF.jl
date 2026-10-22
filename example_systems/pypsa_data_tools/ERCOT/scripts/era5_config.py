"""Weather year for every script that uses the ERA5 cutout.

Set in one place so that the weather year always matches the demand year.
"""
from pathlib import Path

RAW_SRC = Path(__file__).resolve().parents[1] / "source_data"

# Must equal build_demand_nrel_ercot_wz.py's ERCOT native-load year and the Texas7k case vintage (2022).
WEATHER_YEAR = 2022


def cutout_path(year=WEATHER_YEAR):
    p = RAW_SRC / f"usa_era5_{year}.nc"
    if not p.exists():
        raise FileNotFoundError(
            f"Missing ERA5 cutout: {p}\n"
            f"Build it first (needs CDS credentials, ~5 GB, hours):\n"
            f"  python RAW/scripts/build_era5_cutout.py --year {year}"
        )
    return p
