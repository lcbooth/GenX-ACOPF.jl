"""Download the ERA5 weather cutout used by build_vre_profiles.py and
build_bus_vre_features_for_hac.py.

    python scripts/build_era5_cutout.py --year 2022

Needs Copernicus Climate Data Store credentials in ~/.cdsapirc (free account; see
https://cds.climate.copernicus.eu/how-to-api). The download is about 5 GB and requests are
queued, so it can take hours. It only needs to run once: the cutout is saved as
source_data/usa_era5_<year>.nc.

The year must match the demand year (2022), because GenX pairs demand and renewable output
hour by hour. Grid: 0.3 degrees over the contiguous US.
"""
import argparse
from pathlib import Path

import atlite

RAW_SRC = Path(__file__).resolve().parents[1] / "source_data"

# CONUS at 0.3 deg -> extent [-126.15, -64.95, 22.95, 49.95], shape (90, 204). Far larger
# than ERCOT needs, but kept because build_bus_vre_features_for_hac.py snaps buses to this
# grid; changing it moves every bus's cell and so changes the clustering.
X_SLICE = slice(-126.0, -65.1)
Y_SLICE = slice(23.1, 49.8)
DX = DY = 0.3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2022,
                    help="Weather year. MUST match build_demand_nrel_ercot_wz.py's load year.")
    args = ap.parse_args()

    out = RAW_SRC / f"usa_era5_{args.year}.nc"
    if out.exists():
        print(f"{out} already exists -- nothing to do.")
        return

    cutout = atlite.Cutout(
        path=str(out),
        module="era5",
        x=X_SLICE,
        y=Y_SLICE,
        dx=DX,
        dy=DY,
        time=str(args.year),
    )
    print(f"Preparing {out} ({args.year}) -- ~5 GB, CDS queues can take hours...")
    cutout.prepare()
    print(f"Done: {out}")


if __name__ == "__main__":
    main()
