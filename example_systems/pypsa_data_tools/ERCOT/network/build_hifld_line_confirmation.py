"""Per-bus HIFLD confirmation score, a clustering feature for cluster_texas7k.py.

    python build_hifld_line_confirmation.py

Texas7k is synthetic, and not all of its lines follow real corridors. For each >=138 kV
Texas7k line, the score is the fraction of its straight-line length that lies within
COORD_SNAP_DEG of a real in-service HIFLD line of >=138 kV. A bus's score is the
s_nom-weighted mean over its >=138 kV lines. Buses with only lower-voltage lines get the
network median.

The score is used as a clustering feature, not a hard constraint: buses with low scores
include important hubs, so cutting on it would separate real load centres.

Writes intermediates/texas7k_bus_hifld_confirmation.csv.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
from shapely.geometry import LineString

from paths import INTERMEDIATES, RAW_NETWORK_NC, SOURCE_DATA, require

HIFLD_GEOJSON = SOURCE_DATA / "HIFLD" / "electric_transmission_lines_tx_bbox.geojson"
OUT_CSV = INTERMEDIATES / "texas7k_bus_hifld_confirmation.csv"

COORD_SNAP_DEG = 0.03  # ~3.3 km
MIN_VOLTAGE = 138.0


def main():
    n = pypsa.Network(str(require(RAW_NETWORK_NC, "raw Texas7k network")))
    lines = n.lines[n.lines["v_nom"] >= MIN_VOLTAGE].copy()
    buses = n.buses[["x", "y"]]
    lines = lines.join(buses.add_suffix("0"), on="bus0").join(buses.add_suffix("1"), on="bus1")
    lines["geometry"] = [
        LineString([(r.x0, r.y0), (r.x1, r.y1)]) for r in lines.itertuples()
    ]
    t7k = gpd.GeoDataFrame(lines, geometry="geometry", crs="EPSG:4326")
    print(f"Texas7k >={MIN_VOLTAGE}kV lines: {len(t7k)}")

    hifld = gpd.read_file(HIFLD_GEOJSON)
    hifld = hifld[(hifld["STATUS"] == "IN SERVICE") & (hifld["VOLTAGE"] >= MIN_VOLTAGE)].copy()
    hifld_union = hifld.geometry.buffer(COORD_SNAP_DEG).union_all()

    t7k["frac_confirmed"] = t7k.geometry.apply(
        lambda geom: geom.intersection(hifld_union).length / geom.length if geom.length > 0 else 1.0
    )
    print(f"Mean frac_confirmed: {t7k['frac_confirmed'].mean():.3f} "
          f"(lines with <50%: {(t7k['frac_confirmed'] < 0.5).sum()} / {len(t7k)})")

    # Load(s_nom)-weighted mean confirmation per bus, over both line ends.
    long = pd.concat([
        t7k[["bus0", "s_nom", "frac_confirmed"]].rename(columns={"bus0": "bus"}),
        t7k[["bus1", "s_nom", "frac_confirmed"]].rename(columns={"bus1": "bus"}),
    ])
    weighted = long.groupby("bus").apply(
        lambda g: np.average(g["frac_confirmed"], weights=g["s_nom"]), include_groups=False
    )
    weighted.name = "hifld_confirmation"

    result = weighted.reindex(n.buses.index)
    n_missing = result.isna().sum()
    median_val = result.median()
    result = result.fillna(median_val)
    print(f"\nBuses with a >={MIN_VOLTAGE}kV line (scored): {len(weighted)}")
    print(f"Buses with none (filled with network median {median_val:.3f}): {n_missing}")

    result.to_frame().to_csv(OUT_CSV)
    print(f"Saved to {OUT_CSV}")


if __name__ == "__main__":
    main()
