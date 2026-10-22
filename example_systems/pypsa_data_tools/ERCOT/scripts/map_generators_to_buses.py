"""Assign each existing generator to its nearest zone.

    python scripts/map_generators_to_buses.py

Reads outputs/ercot_generators_pudl_existing.csv and writes
outputs/ercot_generators_mapped.csv, using haversine distance from each generator to the
clustered network's zone buses.
"""
import pandas as pd
import numpy as np
import pypsa

from network_paths import CLUSTERED_NETWORK_NC, require
from network_paths import OUTPUTS, SOURCE_DATA

RAW_OUT = str(OUTPUTS)
RAW_SRC = str(SOURCE_DATA)
RAW = RAW_OUT  # outputs of earlier steps

n = pypsa.Network(str(require(CLUSTERED_NETWORK_NC, "clustered network")))
buses = n.buses[["x", "y"]].copy()  # x=lon, y=lat
print(f"Clustered network buses: {len(buses)}")

gens = pd.read_csv(f"{RAW}/ercot_generators_pudl_existing.csv")
print(f"Generators to map: {len(gens)}")


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 6378.137 * 2 * np.arcsin(np.sqrt(a))


bus_ids = buses.index.to_numpy()
bus_lon = buses["x"].to_numpy()
bus_lat = buses["y"].to_numpy()

nearest_bus = []
for lon, lat in zip(gens["longitude"], gens["latitude"]):
    dists = haversine_km(lon, lat, bus_lon, bus_lat)
    nearest_bus.append(bus_ids[np.argmin(dists)])

gens["bus"] = nearest_bus

print("\nGenerator capacity by assigned bus:")
print(gens.groupby("bus")["capacity_mw"].agg(["count", "sum"]).sort_values("sum", ascending=False))

gens.to_csv(f"{RAW}/ercot_generators_mapped.csv", index=False)
print(f"\nSaved to {RAW}/ercot_generators_mapped.csv")
