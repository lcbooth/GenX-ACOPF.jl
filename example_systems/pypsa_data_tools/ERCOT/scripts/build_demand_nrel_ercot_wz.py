"""Hourly demand per zone: ERCOT metered weather-zone load, split by NLR county shares.

    python scripts/build_demand_nrel_ercot_wz.py

Builds the demand used in our work and case 13. Writes
outputs/ercot_demand_20zone_nrel_ercot_wz.csv (Time_Index, local_time, z1..z20).

Method:

    zone_load[t, z] = sum over weather zones w of
                          ercot_metered[t, w] * (share of w's load in model zone z)

ERCOT's metered hourly load for each of its 8 weather zones sets magnitude and shape. The
shares come from NLR county load (Obika, Cole & Rivers, Sci Data 2026): each county's
share of its weather zone's annual energy, mapped to model zones by Texas7k Pd within the
county.

This method reproduces ERCOT's metered weather-zone energy and keeps differences between
zones. Below the weather-zone level the split is still population-based. It excludes
direct use and rooftop-PV self-consumption (about 18 TWh in 2022), which ERCOT does not
meter. See ../README.md, demand section.

LOAD_YEAR follows WEATHER_YEAR in era5_config.py.
"""
import numpy as np
import pandas as pd
import geopandas as gpd
import pypsa

from era5_config import WEATHER_YEAR
from network_paths import (
    CLUSTERED_NETWORK_NC,
    RAW_NETWORK_NC,
    BUSMAP_CSV,
    OUTPUTS,
    SOURCE_DATA,
    require,
)
from timezones import ERCOT_TZ, ercot_load_index, check_alignment

RAW = str(OUTPUTS)
RAW_SRC = str(SOURCE_DATA)

LOAD_YEAR = WEATHER_YEAR

COUNTY_HOURLY_H5 = f"{RAW_SRC}/NREL_Nature_Demand/historic_load_hourly_2016_2023_texas.h5"
COUNTY_SHP = f"{RAW_SRC}/pypsa_usa_data/counties/cb_2020_us_county_500k.shp"
ERCOT_LOAD_XLSX = f"{RAW_SRC}/ERCOT_Native_Load/Native_Load_{LOAD_YEAR}.xlsx"
# PyPSA-USA's balancing-area shapes. The 8 ERCO-* sub-BAs are ERCOT's 8 weather zones.
BA_SHP = (f"{RAW_SRC}/pypsa_usa_repo_data/workflow/repo_data/geospatial/"
          f"BA_shapes_new/Modified_BE_BA_Shapes.shp")
OUT = f"{RAW}/ercot_demand_20zone_nrel_ercot_wz.csv"

# ERCO-* sub-BA code -> ERCOT native-load workbook column.
SUBBA_TO_WZ = {
    "ERCO-C": "COAST", "ERCO-E": "EAST", "ERCO-FW": "FWEST", "ERCO-N": "NORTH",
    "ERCO-NC": "NCENT", "ERCO-S": "SOUTH", "ERCO-SC": "SCENT", "ERCO-W": "WEST",
}
WZ_COLS = list(SUBBA_TO_WZ.values())

# A county counts as belonging to a weather zone only if this much of it lies inside.
MIN_COUNTY_OVERLAP = 0.05

# --- ERCOT's metered hourly load, per weather zone ---
ercot = pd.read_excel(require(ERCOT_LOAD_XLSX, "ERCOT native load"))
assert len(ercot) == 8760, f"expected 8760 hours, got {len(ercot)}"
missing_wz = [c for c in WZ_COLS if c not in ercot.columns]
if missing_wz:
    raise ValueError(f"ERCOT workbook is missing weather-zone columns: {missing_wz}")

# Hour-ending local labels -> hour-beginning tz-aware instants (see timezones.py).
local_time = ercot_load_index(ercot["Hour Ending"], LOAD_YEAR)
ercot_wz = ercot[WZ_COLS].to_numpy(float)  # (8760, 8)
print(f"ERCOT metered {LOAD_YEAR}: {ercot_wz.shape[0]} h x {ercot_wz.shape[1]} weather zones")
print(f"  system energy {ercot_wz.sum() / 1e6:.1f} TWh, peak {ercot_wz.sum(axis=1).max():,.0f} MW")

# --- Counties -> weather zones ---
counties = gpd.read_file(require(COUNTY_SHP, "county shapefile"))
tx = counties.loc[counties["STATEFP"] == "48", ["GEOID", "NAME", "geometry"]].copy()
tx = tx.rename(columns={"GEOID": "fips"}).to_crs("EPSG:5070")  # equal-area for overlaps

ba = gpd.read_file(require(BA_SHP, "PyPSA-USA BA shapes")).to_crs("EPSG:5070")
erco = ba[ba["BA"].isin(SUBBA_TO_WZ)][["BA", "geometry"]]
if len(erco) != len(SUBBA_TO_WZ):
    raise ValueError(f"expected {len(SUBBA_TO_WZ)} ERCO sub-BAs in {BA_SHP}, found {len(erco)}")

# Assign each county to the weather zone covering most of it.
county_wz = {}
for fips, row in tx.set_index("fips").iterrows():
    best_area, best_ba = 0.0, None
    for _, sub in erco.iterrows():
        a = row.geometry.intersection(sub.geometry).area
        if a > best_area:
            best_area, best_ba = a, sub["BA"]
    if best_ba is not None and best_area / row.geometry.area >= MIN_COUNTY_OVERLAP:
        county_wz[fips] = SUBBA_TO_WZ[best_ba]
county_wz = pd.Series(county_wz, name="wz")
print(f"Counties assigned to a weather zone: {len(county_wz)} of {len(tx)}")

# --- NLR county annual energy: the within-weather-zone spatial detail ---
county_hourly = pd.read_hdf(require(COUNTY_HOURLY_H5, "NLR county hourly load"), key="load")
county_hourly.index = pd.to_datetime(county_hourly.index)
if county_hourly.index.tz is None:
    county_hourly.index = county_hourly.index.tz_localize("UTC")
# UTC hour-ending -> hour-beginning (NLR timestamps are in UTC).
county_hourly.index = county_hourly.index - pd.Timedelta(hours=1)
county_hourly.index = county_hourly.index.tz_convert(ERCOT_TZ)
county_hourly = county_hourly.loc[county_hourly.index.year == LOAD_YEAR]
county_hourly.columns = county_hourly.columns.astype(str).str.removeprefix("p").str.zfill(5)
county_energy = county_hourly.sum()  # annual MWh per county; only ratios within a zone are used

# --- Buses -> counties -> model zones, weighted by Texas7k Pd ---
n = pypsa.Network(str(require(RAW_NETWORK_NC, "raw Texas7k network")))
cn = pypsa.Network(str(require(CLUSTERED_NETWORK_NC, "clustered network")))
n.buses.index = n.buses.index.astype(str)
cn.buses.index = cn.buses.index.astype(str)

busmap = pd.read_csv(require(BUSMAP_CSV, "20-zone busmap"), index_col=0)["cluster"]
busmap.index = busmap.index.astype(str)
busmap = busmap.astype(str)

raw_buses = n.buses.loc[n.buses.index.intersection(busmap.index), ["x", "y", "Pd"]].copy()
raw_buses["cluster"] = raw_buses.index.map(busmap)
raw_buses["Pd"] = pd.to_numeric(raw_buses["Pd"], errors="coerce").fillna(0.0)
bus_gdf = gpd.GeoDataFrame(
    raw_buses, geometry=gpd.points_from_xy(raw_buses["x"], raw_buses["y"]), crs="EPSG:4326",
).to_crs("EPSG:5070")
bus_county = gpd.sjoin(
    bus_gdf, tx[["fips", "geometry"]], how="left", predicate="within"
).drop(columns="index_right")

county_pd_total = bus_county.groupby("fips")["Pd"].transform("sum")
county_bus_count = bus_county.groupby("fips")["cluster"].transform("size")
bus_county["w"] = np.where(
    county_pd_total > 0, bus_county["Pd"] / county_pd_total, 1.0 / county_bus_count
)
weights = bus_county[["fips", "cluster", "w"]].dropna(subset=["fips", "cluster"])
county_cluster = weights.groupby(["fips", "cluster"])["w"].sum().unstack(fill_value=0.0)

# Keep counties that have model-zone weight, NLR energy, and a weather zone.
usable = county_cluster.index.intersection(county_energy.index).intersection(county_wz.index)
county_cluster = county_cluster.loc[usable]
print(f"Counties usable (in ERCOT network, NLR data, and a weather zone): {len(usable)}")

wsum = county_cluster.sum(axis=1)
if not np.allclose(wsum, 1.0, atol=1e-6):
    bad = wsum[~np.isclose(wsum, 1.0, atol=1e-6)]
    raise ValueError(f"county->cluster weights do not sum to 1 for {len(bad)} counties")

# --- Weather zone -> model zone allocation matrix ---
# Row w, column z: the fraction of weather zone w's load that belongs to model zone z,
# from each county's share of w's NLR annual energy times its county->zone split.
zone_labels = [str(c) for c in cn.buses.index]
alloc = pd.DataFrame(0.0, index=WZ_COLS, columns=zone_labels)
for wz in WZ_COLS:
    members = [f for f in usable if county_wz[f] == wz]
    if not members:
        raise ValueError(f"weather zone {wz} has no usable counties -- check the BA shapes")
    e = county_energy[members]
    if e.sum() <= 0:
        raise ValueError(f"weather zone {wz} has zero NLR energy across its counties")
    share = e / e.sum()
    alloc.loc[wz] = county_cluster.loc[members].mul(share, axis=0).sum(axis=0).reindex(
        zone_labels, fill_value=0.0
    ).to_numpy()

row_sums = alloc.sum(axis=1)
if not np.allclose(row_sums, 1.0, atol=1e-6):
    raise ValueError(f"weather-zone allocation rows do not sum to 1:\n{row_sums}")

# --- Apply ERCOT's metered hourly load through that allocation ---
zone_hourly = pd.DataFrame(ercot_wz @ alloc.to_numpy(), columns=zone_labels)

max_diff = np.abs(zone_hourly.sum(axis=1).to_numpy() - ercot_wz.sum(axis=1)).max()
if max_diff > 1e-6:
    raise ValueError(f"allocation lost load: max hourly diff {max_diff:.6f} MW")
print(f"Allocation conserves ERCOT's metered load (max hourly diff {max_diff:.2e} MW)")

# Cluster labels -> z1..z20, in the clustered network's own bus order.
zone_hourly.columns = [f"z{i}" for i in range(1, len(zone_labels) + 1)]
zone_order = list(zone_hourly.columns)

demand_df = zone_hourly.reset_index(drop=True)
demand_df.insert(0, "Time_Index", np.arange(1, len(demand_df) + 1))
demand_df.insert(1, "local_time", local_time)

total = demand_df[zone_order].sum(axis=1)
print(f"\nDemand matrix: {demand_df.shape}")
print(f"System peak: {total.max():,.1f} MW | average: {total.mean():,.1f} MW | "
      f"energy: {total.sum() / 1e6:.1f} TWh")

corr = demand_df[zone_order].corr().to_numpy()
off_diag = corr[~np.eye(len(zone_order), dtype=bool)]
print(f"Inter-zone load correlation: min {off_diag.min():.3f}, mean {off_diag.mean():.3f} "
      f"(a single system-wide load shape would give 1.000)")

check_alignment(np.zeros(len(total)), total.to_numpy(), label="demand-only")
peak_hour = int(local_time[int(total.to_numpy().argmax())].hour)
print(f"Annual peak at local hour {peak_hour} (ERCOT's real {LOAD_YEAR} peak was hour 16-17)")

# Weather-zone shares are ERCOT's own by construction; print them as a provenance record.
print("\nWeather-zone energy (ERCOT metered, TWh) and its spread across model zones:")
for wz in WZ_COLS:
    e = ercot[wz].sum() / 1e6
    top = alloc.loc[wz].sort_values(ascending=False).head(3)
    top_s = ", ".join(f"z{zone_labels.index(c) + 1}:{v:.0%}" for c, v in top.items() if v > 0.01)
    print(f"  {wz:6s} {e:6.1f} TWh -> {top_s}")

demand_df.to_csv(OUT, index=False)
print(f"\nSaved to {OUT}")
