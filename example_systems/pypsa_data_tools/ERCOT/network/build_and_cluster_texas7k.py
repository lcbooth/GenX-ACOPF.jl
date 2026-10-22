"""
Build a PyPSA network from the converted Texas7k CSVs and assign each bus to a ReEDS zone.

    python build_and_cluster_texas7k.py

A reduced version of PyPSA-USA's build_base_network.py for the ERCOT case: offshore buses
and other region assignments are not needed. Each bus is assigned to a ReEDS balancing area
using PyPSA-USA's Reeds_Shapes/rb_and_ba_areas.shp; this is later used to keep the ERCOT
buses (p60-p65, p67). Clustering is done separately by cluster_texas7k.py.

Writes intermediates/texas7k_raw_network.nc and
intermediates/texas7k_buses_with_reeds_zone.csv.
"""
import pandas as pd
import geopandas as gpd
import numpy as np
import pypsa

from paths import (
    TEXAS7K_CSV_DIR,
    REEDS_SHAPES,
    BUSES_WITH_REEDS_ZONE_CSV,
    RAW_NETWORK_NC,
    require,
)

DATA_DIR = str(TEXAS7K_CSV_DIR)

# ---------------------------------------------------------------------------
# 1. Load the converted Texas7k CSVs and build a PyPSA network
# ---------------------------------------------------------------------------
buses = pd.read_csv(f"{DATA_DIR}/buses.csv", index_col=0)
bus2sub = pd.read_csv(f"{DATA_DIR}/bus2sub.csv", index_col=0)
sub = pd.read_csv(f"{DATA_DIR}/sub.csv", index_col=0)
branches = pd.read_csv(f"{DATA_DIR}/branches.csv", index_col=0)

buslocs = pd.merge(bus2sub, sub, left_on="sub_id", right_index=True)
gdf_bus = pd.merge(buses, buslocs[["lat", "lon"]], left_index=True, right_index=True, how="left")
gdf_bus.index = gdf_bus.index.astype(str)

n = pypsa.Network()
n.name = "Texas7k-2022"

n.madd(
    "Bus",
    gdf_bus.index,
    v_nom=gdf_bus["baseKV"],
    x=gdf_bus["lon"],
    y=gdf_bus["lat"],
    Pd=gdf_bus["Pd"],
    interconnect=gdf_bus["interconnect"],
)
print(f"Added {len(n.buses)} buses")

branches = branches.copy()
branches.index = branches.index.astype(str)
branches["from_bus_id"] = branches["from_bus_id"].astype(str)
branches["to_bus_id"] = branches["to_bus_id"].astype(str)
branches = branches[branches["rateA"] > 0]  # match build_base_network's 0.01 floor behavior (drop true zeros here for simplicity)

# Texas7k ships r/x/b per-unit on a 100 MVA base; PyPSA wants ohms/siemens. With
# Z_base = v_nom^2 / S_base:  z_ohm = z_pu * v_nom^2 / 100  and  b_S = b_pu * 100 / v_nom^2.
# (b is a susceptance, so it takes S_base in the numerator, not the denominator.)
S_BASE_MVA = 100.0
for tech in ["Line", "Transformer"]:
    tb = branches.query("branch_device_type == @tech")
    from_vnom = tb["from_bus_id"].map(n.buses["v_nom"])
    z_base = (from_vnom.values ** 2) / S_BASE_MVA
    n.madd(
        tech,
        tb.index,
        bus0=tb["from_bus_id"],
        bus1=tb["to_bus_id"],
        r=tb["r"] * z_base,
        x=tb["x"] * z_base,
        b=tb["b"] / z_base if tech == "Line" else 0.0,
        s_nom=tb["rateA"],
        v_nom=from_vnom.values,
        carrier="AC",
    )
print(f"Added {len(n.lines)} lines, {len(n.transformers)} transformers")

# add PyPSA's custom line type (needed for downstream line-length/type assignment,
# not otherwise used: impedances come from kron_reduce_sparse.py)
n.line_types.loc["temp"] = pd.Series(
    [60, 0.0683, 0.335, 15, 1.01],
    index=["f_nom", "r_per_length", "x_per_length", "c_per_length", "i_nom"],
)
n.lines["type"] = "temp"

# ---------------------------------------------------------------------------
# 2. Spatially assign each bus to a real ReEDS zone (the load-bearing shapefile join)
# ---------------------------------------------------------------------------
reeds_shapes = (
    gpd.read_file(require(REEDS_SHAPES, "PyPSA-USA ReEDS zone shapefile"))
    .to_crs(epsg=4326)
    .rename(columns={"rb": "reeds_zone"})
)

bus_gdf = gpd.GeoDataFrame(
    n.buses,
    geometry=gpd.points_from_xy(n.buses["x"], n.buses["y"]),
    crs="EPSG:4326",
)
joined = gpd.sjoin(bus_gdf, reeds_shapes[["reeds_zone", "geometry"]], how="left").drop(columns=["index_right"])
n.buses["reeds_zone"] = joined["reeds_zone"]

n_missing = n.buses["reeds_zone"].isna().sum()
print(f"Buses with no ReEDS zone match: {n_missing} / {len(n.buses)}")
print("ReEDS zone distribution:")
print(n.buses["reeds_zone"].value_counts())

n.buses.to_csv(BUSES_WITH_REEDS_ZONE_CSV)
n.export_to_netcdf(RAW_NETWORK_NC)
print("\nSaved raw network with ReEDS zones assigned.")
