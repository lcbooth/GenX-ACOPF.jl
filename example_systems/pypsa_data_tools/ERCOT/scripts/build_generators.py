"""Existing ERCOT generators from PUDL (EIA-860).

    python scripts/build_generators.py

Reads source_data/out_eia__yearly_generators.parquet and writes
outputs/ercot_generators_pudl_existing.csv.

Generators are assigned to ERCOT by a spatial join of their coordinates with the ReEDS
balancing areas p60-p65 and p67, the same area used for the network. The iso_rto_code
column is incomplete for newer plants, and state == "TX" includes areas outside ERCOT.
"""
import pandas as pd
import geopandas as gpd

from network_paths import REEDS_SHAPES, require
from network_paths import OUTPUTS, SOURCE_DATA

RAW_OUT = str(OUTPUTS)
RAW_SRC = str(SOURCE_DATA)
RAW = RAW_OUT  # outputs of earlier steps
ERCOT_ZONES = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}

yg = pd.read_parquet(f"{RAW_SRC}/out_eia__yearly_generators.parquet")
yg["report_date"] = pd.to_datetime(yg["report_date"])

# most recent report per generator, existing/operating only, must have lat/lon
latest = (
    yg.sort_values("report_date")
    .groupby(["plant_id_eia", "generator_id"], as_index=False)
    .last()
)
existing = latest[
    (latest["operational_status"] == "existing")
    & latest["latitude"].notna()
    & latest["longitude"].notna()
].copy()
print(f"All US existing generators with lat/lon: {len(existing)}")

# restrict to a Texas bounding box first (cheap pre-filter before the real spatial join)
existing = existing[
    (existing["longitude"].between(-107, -93)) & (existing["latitude"].between(25, 37))
].copy()
print(f"Texas-bounding-box existing generators: {len(existing)}, total MW: {existing['capacity_mw'].sum():.1f}")

# spatial join against ReEDS zones (same footprint as the transmission network)
reeds_shapes = (
    gpd.read_file(require(REEDS_SHAPES, "ReEDS zone shapefile"))
    .to_crs(epsg=4326)
    .rename(columns={"rb": "reeds_zone"})
)
gen_gdf = gpd.GeoDataFrame(
    existing,
    geometry=gpd.points_from_xy(existing["longitude"], existing["latitude"]),
    crs="EPSG:4326",
)
joined = gpd.sjoin(gen_gdf, reeds_shapes[["reeds_zone", "geometry"]], how="left").drop(columns=["index_right"])
existing["reeds_zone"] = joined["reeds_zone"].values

ercot_gens = existing[existing["reeds_zone"].isin(ERCOT_ZONES)].copy()
print(f"\nERCOT-zone existing generators (real spatial join): {len(ercot_gens)}")
print(f"Total nameplate capacity (MW): {ercot_gens['capacity_mw'].sum():.1f}")
print()
print("By fuel type:")
print(ercot_gens.groupby("fuel_type_code_pudl", observed=True)["capacity_mw"].agg(["count", "sum"]).sort_values("sum", ascending=False))

ercot_gens.to_csv(f"{RAW}/ercot_generators_pudl_existing.csv", index=False)
print(f"\nSaved to {RAW}/ercot_generators_pudl_existing.csv")
