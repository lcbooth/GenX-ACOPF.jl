"""Hourly wind and solar capacity factors at each Texas7k bus, a clustering feature.

    python scripts/build_bus_vre_features_for_hac.py

Writes outputs/bus_vre_hac_feature.csv, read by cluster_texas7k.py. This follows PyPSA-USA's
default HAC feature ("solar+onwind-time"). It runs before clustering, on the unclustered
network.

The ERA5 grid (0.3 degrees, about 30 km) is much coarser than the bus spacing, so each bus
is assigned to its grid cell (554 cells cover the 6717 buses), and the capacity factor is
computed once per cell. No land-use exclusions are applied, since this is a similarity
feature rather than a buildable potential. Turbine and panel settings match
build_vre_profiles.py.

WEATHER_YEAR is set in era5_config.py.
"""
import numpy as np
import pandas as pd
import pypsa
import atlite

from era5_config import WEATHER_YEAR, cutout_path
from network_paths import RAW_NETWORK_NC, require
from network_paths import OUTPUTS, SOURCE_DATA

RAW_OUT = str(OUTPUTS)
RAW_SRC = str(SOURCE_DATA)
RAW = RAW_OUT  # outputs of earlier steps
OUT = f"{RAW}/bus_vre_hac_feature.csv"

n = pypsa.Network(str(require(RAW_NETWORK_NC, "raw Texas7k network")))
buses = n.buses[["x", "y"]].copy()
print(f"Buses: {len(buses)}")

cutout = atlite.Cutout(str(cutout_path()))
print(f"Cutout ({WEATHER_YEAR}): {cutout}")

# Snap each bus to its nearest ERA5 cell center (grid is regular, 0.3 deg spacing)
cutout_x = cutout.coords["x"].values
cutout_y = cutout.coords["y"].values
dx = float(cutout_x[1] - cutout_x[0])
dy = float(cutout_y[1] - cutout_y[0])
x0, y0 = float(cutout_x[0]), float(cutout_y[0])

cellx = np.clip(np.round((buses["x"].values - x0) / dx).astype(int), 0, len(cutout_x) - 1)
celly = np.clip(np.round((buses["y"].values - y0) / dy).astype(int), 0, len(cutout_y) - 1)
buses["cell_x"] = cutout_x[cellx]
buses["cell_y"] = cutout_y[celly]
unique_cells = buses[["cell_x", "cell_y"]].drop_duplicates()
print(f"Unique ERA5 cells touched by buses: {len(unique_cells)} (of {len(buses)} buses)")

# Wind (Vestas_V112_3MW) and solar (CSi, latitude-optimal) capacity factor at every
# cutout grid cell -- same turbine/panel spec as build_vre_profiles.py.
# aggregate_time=None returns the full per-cell (time, y, x) timeseries, unaggregated
# (per atlite's own docstring example for "per-cell capacity factor time series").
wind_cf = cutout.wind(turbine="Vestas_V112_3MW", aggregate_time=None)
solar_cf = cutout.pv(panel="CSi", orientation="latitude_optimal", aggregate_time=None)

print("Computing wind/solar timeseries...")
wind_cf = wind_cf.compute()
solar_cf = solar_cf.compute()
print("Done computing. Extracting per-cell timeseries...")

# For each unique cell actually touched by a bus, pull its timeseries.
records_wind = {}
records_solar = {}
for i, (_, row) in enumerate(unique_cells.iterrows()):
    key = (row["cell_x"], row["cell_y"])
    records_wind[key] = wind_cf.sel(x=row["cell_x"], y=row["cell_y"], method="nearest").values
    records_solar[key] = solar_cf.sel(x=row["cell_x"], y=row["cell_y"], method="nearest").values
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(unique_cells)} cells extracted")

time_index = pd.to_datetime(cutout.coords["time"].values)

# Broadcast cell profiles out to every bus (many buses share the same cell).
print("Broadcasting to buses...")
wind_out = pd.DataFrame(
    {bus: records_wind[(row["cell_x"], row["cell_y"])]
     for bus, row in buses.iterrows()},
    index=time_index,
)
solar_out = pd.DataFrame(
    {bus: records_solar[(row["cell_x"], row["cell_y"])]
     for bus, row in buses.iterrows()},
    index=time_index,
)
print("Broadcast done.")

# Combine into a single "solar+onwind-time" feature matrix (buses x [wind_hours, solar_hours]),
# matching get_feature_for_hac()'s "-time" branch: stack carrier timeseries as columns,
# buses as rows, after transposing from the per-carrier bus-column layout above.
feature = pd.concat([wind_out.T, solar_out.T], axis=1)
feature.columns = [f"wind_{i}" for i in range(wind_out.shape[0])] + [f"solar_{i}" for i in range(solar_out.shape[0])]
feature.index.name = "Bus"
feature.to_csv(OUT)
print(f"\nSaved real per-bus HAC feature matrix to {OUT}, shape {feature.shape}")
