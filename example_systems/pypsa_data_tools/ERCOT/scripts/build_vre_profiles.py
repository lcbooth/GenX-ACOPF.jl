"""Per-zone hourly wind and solar capacity factors and buildable potential (atlite + ERA5).

    python scripts/build_vre_profiles.py

Writes outputs/ercot_{onwind,solar}_{profile,potential}_20zone.csv. Requires the ERA5 cutout
(build_era5_cutout.py) and the zone regions (build_bus_regions.py).

Settings follow PyPSA-USA (config.common.yaml, build_renewable_profiles.py), except:
  - wind uses NLR's 2020 ATB 4 MW reference turbine (110 m hub height);
  - solar uses horizontal single-axis tracking.
Both match the technologies priced by NLR ATB (build_tech_costs.py). Land-use exclusions
use the Natura and Copernicus land-cover rasters; densities are 3 MW/km^2 (wind) and
4.6 MW/km^2 (solar).

WEATHER_YEAR (era5_config.py) must equal the demand year, because GenX pairs demand and
renewable output hour by hour.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
import atlite

from era5_config import WEATHER_YEAR, cutout_path
from network_paths import CLUSTERED_NETWORK_NC, ZONE_REGIONS_GEOJSON, require
from timezones import ERCOT_TZ, era5_to_ercot_local, check_alignment
from network_paths import OUTPUTS, SOURCE_DATA
from demand_source import demand_csv

RAW_OUT = str(OUTPUTS)
RAW_SRC = str(SOURCE_DATA)
RAW = RAW_OUT  # outputs of earlier steps
PYPSA_USA_DATA = f"{RAW_SRC}/pypsa_usa_data"
NETWORK_NC = str(CLUSTERED_NETWORK_NC)

# ---------------------------------------------------------------------------
# 1. Zone regions
# ---------------------------------------------------------------------------
n = pypsa.Network(NETWORK_NC)
buses = n.buses[["x", "y"]].copy()
print(f"Buses: {len(buses)}")

# Per-county Voronoi over substations, dissolved to zones -- see build_bus_regions.py.
# Its `zone` is 1-based over n.buses.index order, the same convention used everywhere else,
# so row i here is bus i there. Indexed by zone number so the export can name columns z1..zN
# directly.
regions = gpd.read_file(require(ZONE_REGIONS_GEOJSON, "zone regions")).set_index("zone")
regions = regions.sort_index().rename_axis("bus")
if len(regions) != len(n.buses):
    raise RuntimeError(
        f"{len(regions)} zone regions but {len(n.buses)} clustered buses -- "
        f"re-run build_bus_regions.py against this clustering."
    )
regions["x"] = n.buses["x"].to_numpy()
regions["y"] = n.buses["y"].to_numpy()
print(f"Zone regions: {len(regions)} "
      f"({(~regions.geometry.is_valid | regions.geometry.is_empty).sum()} empty/invalid)")

# ---------------------------------------------------------------------------
# 2. Open the ERA5 cutout
# ---------------------------------------------------------------------------
cutout = atlite.Cutout(str(cutout_path()))
print(f"\nCutout ({WEATHER_YEAR}): {cutout}")

# ---------------------------------------------------------------------------
# 3. Build exclusion containers (real rasters, real config parameters)
# ---------------------------------------------------------------------------
COPERNICUS = f"{PYPSA_USA_DATA}/copernicus/PROBAV_LC100_global_v3.0.1_2019-nrt_Discrete-Classification-map_USA_EPSG-4326.tif"
NATURA = f"{PYPSA_USA_DATA}/natura.tiff"
CEC_WIND = f"{PYPSA_USA_DATA}/CEC_GIS/CEC_Wind_BaseScreen_epsg3310.tif" if False else None  # CEC is CA-specific, not used for TX

resource_configs = {
    "onwind": {
        # NLR ATB reference turbine (110 m hub), matching the ATB wind costs and the
        # 91-126 m towers installed in Texas.
        "turbine": "NREL_ReferenceTurbine_2020ATB_4MW",
        "capacity_per_sqkm": 3,
        "corine_codes": [20, 30, 40, 60, 100, 111, 112, 113, 114, 115],
        "corine_distance": 10,
        "corine_distance_codes": [50],
    },
    "solar": {
        "panel": "CSi",
        # Horizontal single-axis tracking, as used by almost all utility-scale PV in
        # ERCOT and priced by ATB.
        "tracking": "horizontal",
        "capacity_per_sqkm": 4.6,
        "corine_codes": [20, 30, 60, 90, 100],
    },
}

results = {}
for tech, cfg in resource_configs.items():
    print(f"\n=== {tech} ===")
    excluder = atlite.ExclusionContainer(crs=3857, res=200)
    excluder.add_raster(NATURA, nodata=0, allow_no_overlap=True)
    excluder.add_raster(COPERNICUS, codes=cfg["corine_codes"], invert=True)
    if "corine_distance" in cfg:
        excluder.add_raster(
            COPERNICUS, codes=cfg["corine_distance_codes"], buffer=cfg["corine_distance"],
        )

    print("Computing land-use availability matrix...")
    availability = cutout.availabilitymatrix(regions, excluder)

    area = cutout.grid.to_crs("EPSG:5070").area / 1e6
    area = area.values.reshape(cutout.shape)
    import xarray as xr
    area = xr.DataArray(area, [cutout.coords["y"], cutout.coords["x"]])

    capacity_per_sqkm = cfg["capacity_per_sqkm"]
    potential = capacity_per_sqkm * availability.sum("bus") * area

    if tech == "onwind":
        capacity_factor = cutout.wind(turbine=cfg["turbine"], capacity_factor=True)
        layout = capacity_factor * area * capacity_per_sqkm
        profile, capacities = cutout.wind(
            turbine=cfg["turbine"],
            matrix=availability.stack(spatial=["y", "x"]),
            layout=layout,
            index=regions.index,
            per_unit=True,
            return_capacity=True,
        )
    else:
        pv_kwargs = dict(panel=cfg["panel"], orientation="latitude_optimal",
                         tracking=cfg.get("tracking"))
        capacity_factor = cutout.pv(capacity_factor=True, **pv_kwargs)
        layout = capacity_factor * area * capacity_per_sqkm
        profile, capacities = cutout.pv(
            matrix=availability.stack(spatial=["y", "x"]),
            layout=layout,
            index=regions.index,
            per_unit=True,
            return_capacity=True,
            **pv_kwargs,
        )

    # Buildable potential uses PyPSA-USA's "conservative" method (its config default for
    # both onwind and solar), not the "simple" capacity_per_sqkm * availability * area:
    #   p_nom_max = capacities / max capacity factor over available land
    # Dividing by a capacity factor (<1) makes this materially larger than a flat
    # area*density figure.
    max_cap_factor = capacity_factor.where(availability.sum("bus") != 0).max(["x", "y"])
    p_nom_max_conservative = capacities / max_cap_factor

    results[tech] = {"profile": profile, "capacities": capacities, "p_nom_max": p_nom_max_conservative}
    print(f"{tech} capacities per zone (MW, layout-weighted, NOT the buildable potential):")
    print(capacities.to_pandas().sort_values(ascending=False))
    print(f"{tech} p_nom_max per zone (MW, real 'conservative' method):")
    print(p_nom_max_conservative.to_pandas().sort_values(ascending=False))

# ---------------------------------------------------------------------------
# 4. Export
# ---------------------------------------------------------------------------
# ERCOT's local hours, from the demand build -- the profiles are reindexed onto these so
# row i means the same wall-clock hour in both files. The demand output carries local_time.
demand_path = demand_csv()
print(f"Aligning profiles to demand: {demand_path.name}")
demand_df = pd.read_csv(demand_path)
if "local_time" not in demand_df.columns:
    raise RuntimeError(
        f"{demand_path.name} has no 'local_time' column -- re-run its build script. "
        "Without it the profiles cannot be timezone-aligned to demand."
    )
target_local = pd.DatetimeIndex(pd.to_datetime(demand_df["local_time"], utc=True)).tz_convert(ERCOT_TZ)

for tech in resource_configs:
    df = results[tech]["profile"].to_pandas()
    df.columns = [f"z{int(c)}" for c in df.columns]  # regions are indexed by zone number

    # atlite stamps profiles in naive UTC; demand is ERCOT local clock time. Reindex
    # rather than shift: the offset changes with DST, so no fixed roll is correct.
    aligned, n_filled = era5_to_ercot_local(df, df.index, target_local)
    if n_filled:
        print(f"  {tech}: {n_filled} hour(s) had no exact ERA5 match (year boundary), "
              f"filled from nearest")
    aligned.to_csv(f"{RAW}/ercot_{tech}_profile_20zone.csv")
    print(f"\nSaved {tech} profile to {RAW}/ercot_{tech}_profile_20zone.csv, shape {aligned.shape}")

    # Buildable potential is p_nom_max (the "conservative" method above), not
    # "capacities", which is only the layout-weighted capacity.
    cap_df = results[tech]["p_nom_max"].to_pandas()
    cap_df.to_csv(f"{RAW}/ercot_{tech}_potential_20zone.csv")
    print(f"Saved {tech} potential (p_nom_max, MW per zone) to {RAW}/ercot_{tech}_potential_20zone.csv")

# Smoke test: solar must peak in the local early afternoon, not the evening.
solar_mean = pd.read_csv(f"{RAW}/ercot_solar_profile_20zone.csv", index_col=0).mean(axis=1)
demand_total = demand_df[[c for c in demand_df.columns if c.startswith("z")]].sum(axis=1)
check_alignment(solar_mean.to_numpy(), demand_total.to_numpy(), label="solar vs demand")
