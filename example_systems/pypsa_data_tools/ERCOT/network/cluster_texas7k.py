"""
Cluster the ERCOT part of the Texas7k network into 20 zones.

    python cluster_texas7k.py

Uses PyPSA's busmap_by_hac (hierarchical agglomerative clustering, Ward linkage) with the
network adjacency as a connectivity constraint, so buses are only merged along real lines
and every zone is connected.

Features per bus:
  - hourly wind and solar capacity factors (outputs/bus_vre_hac_feature.csv, from
    scripts/build_bus_vre_features_for_hac.py), so zones are homogeneous in renewable
    resource;
  - the HIFLD confirmation score (intermediates/texas7k_bus_hifld_confirmation.csv, from
    build_hifld_line_confirmation.py), weighted by HIFLD_FEATURE_WEIGHT.

Environment variables: N_CLUSTERS (default 20); LOAD_W (default 0) adds a standardized
per-bus load feature with that weight.

Writes intermediates/texas7k_clustered_20.nc (zones ordered by descending Texas7k Pd, so
zone 1 is the largest load centre) and intermediates/texas7k_busmap_20zone.csv (bus -> zone).
"""
import pandas as pd
import numpy as np
import pypsa

from paths import (
    BUSES_WITH_REEDS_ZONE_CSV,
    BUSMAP_CSV,
    CLUSTERED_NC,
    INTERMEDIATES,
    RAW_NETWORK_NC,
    require,
)
import networkx as nx
from pypsa.clustering.spatial import busmap_by_hac, get_clustering_from_busmap
from paths import VRE_HAC_FEATURE_CSV

n = pypsa.Network(RAW_NETWORK_NC)
buses_reeds = pd.read_csv(
    BUSES_WITH_REEDS_ZONE_CSV,
    index_col=0,
)
buses_reeds.index = buses_reeds.index.astype(str)
n.buses["reeds_zone"] = buses_reeds["reeds_zone"]

ercot_zones = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}
ercot_buses = n.buses[n.buses["reeds_zone"].isin(ercot_zones)].index

drop_buses = n.buses.index.difference(ercot_buses)
n.mremove("Bus", drop_buses)
drop_lines = n.lines.index[~n.lines.bus0.isin(ercot_buses) | ~n.lines.bus1.isin(ercot_buses)]
n.mremove("Line", drop_lines)
drop_xfmr = n.transformers.index[~n.transformers.bus0.isin(ercot_buses) | ~n.transformers.bus1.isin(ercot_buses)]
n.mremove("Transformer", drop_xfmr)

# Keep only the main connected component (drops 9 buses in 4 small islands)
G = nx.Graph()
G.add_nodes_from(n.buses.index)
G.add_edges_from(zip(n.lines.bus0, n.lines.bus1))
G.add_edges_from(zip(n.transformers.bus0, n.transformers.bus1))
components = list(nx.connected_components(G))
main_component = max(components, key=len)
islanded = set(n.buses.index) - main_component
print(f"Dropping {len(islanded)} islanded buses: {sorted(islanded)}")

n.mremove("Bus", list(islanded))
drop_lines2 = n.lines.index[~n.lines.bus0.isin(n.buses.index) | ~n.lines.bus1.isin(n.buses.index)]
n.mremove("Line", drop_lines2)
drop_xfmr2 = n.transformers.index[~n.transformers.bus0.isin(n.buses.index) | ~n.transformers.bus1.isin(n.buses.index)]
n.mremove("Transformer", drop_xfmr2)

print(f"Final pre-clustering network: {len(n.buses)} buses, {len(n.lines)} lines, {len(n.transformers)} transformers")

# busmap_by_hac builds its connectivity constraint from all branch components, so
# transformers count as connections as well as lines.

# Hourly wind and solar capacity factors per bus (8760 h x 2 columns).
vre_feature = pd.read_csv(
    str(VRE_HAC_FEATURE_CSV),
    index_col=0,
)
vre_feature.index = vre_feature.index.astype(str)
vre_feature = vre_feature.reindex(n.buses.index)
print(f"Real per-bus VRE HAC feature: {vre_feature.shape}, "
      f"{vre_feature.isna().any(axis=1).sum()} buses missing (filled with 0)")
vre_feature = vre_feature.fillna(0.0)

# HIFLD confirmation score per bus (build_hifld_line_confirmation.py): the load-weighted
# share of a bus's >=138 kV lines that lie along a real in-service HIFLD line. Used as a
# feature rather than a hard constraint, so that zones are not joined across corridors
# that exist only in the synthetic network.
HIFLD_CONFIRMATION_CSV = INTERMEDIATES / "texas7k_bus_hifld_confirmation.csv"
hifld_confirmation = pd.read_csv(str(require(HIFLD_CONFIRMATION_CSV, "HIFLD bus confirmation")), index_col=0)
hifld_confirmation.index = hifld_confirmation.index.astype(str)
hifld_confirmation = hifld_confirmation.reindex(n.buses.index)
n_missing_hifld = hifld_confirmation.isna().any(axis=1).sum()
hifld_confirmation = hifld_confirmation.fillna(hifld_confirmation.median())
print(f"HIFLD confirmation feature: {n_missing_hifld} buses missing (filled with median)")

# The score is on the same 0-1 scale as the capacity factors. As a single column among
# ~17,500, it is weighted up so that it has a noticeable effect on Ward's variance.
HIFLD_FEATURE_WEIGHT = 5.0
vre_feature["hifld_confirmation"] = hifld_confirmation["hifld_confirmation"] * HIFLD_FEATURE_WEIGHT

import os as _os
# Optional load feature (LOAD_W > 0): one standardized per-bus load column. Off by default.
LOAD_W = float(_os.environ.get("LOAD_W", "0"))
if LOAD_W > 0:
    _ls = n.buses["Pd"].reindex(n.buses.index).fillna(0.0)
    _lz = (_ls - _ls.mean()) / _ls.std()
    vre_feature["load_signal"] = _lz * LOAD_W
    print(f"Load nudge added (LOAD_W={LOAD_W}, standardized)")

N_CLUSTERS = int(_os.environ.get("N_CLUSTERS", "20"))
busmap = busmap_by_hac(n, N_CLUSTERS, buses_i=n.buses.index, feature=vre_feature, linkage="ward")
print(f"\nBusmap produced {busmap.nunique()} clusters")
print(busmap.value_counts())

# Assign each cluster the most common ReEDS zone of its buses
cluster_reeds_zone = n.buses.groupby(busmap)["reeds_zone"].agg(lambda x: x.mode()[0])
print("\nCluster -> ReEDS zone (mode) assignment:")
print(cluster_reeds_zone)

# Drop reeds_zone before aggregation (it can differ within a cluster); it is reattached
# from the cluster-level assignment above
n.buses = n.buses.drop(columns=["reeds_zone"])

# Rescale every line to 345 kV before clustering, as in PyPSA-USA's
# convert_to_voltage_level(): r/x/b are converted to per unit on the line's own base and
# expressed on the 345 kV base. Capacities (s_nom) are unchanged.
NEW_VOLTAGE = 345.0
base_impedance = n.lines["v_nom"] ** 2 / n.lines["s_nom"]
base_susceptance = 1 / base_impedance
r_pu = n.lines["r"] / base_impedance
x_pu = n.lines["x"] / base_impedance
b_pu = n.lines["b"] / base_susceptance

new_base_impedance = NEW_VOLTAGE**2 / n.lines["s_nom"]
n.lines["r"] = r_pu * new_base_impedance
n.lines["x"] = x_pu * new_base_impedance
n.lines["b"] = b_pu / new_base_impedance
n.lines["v_nom"] = NEW_VOLTAGE
n.buses["v_nom"] = NEW_VOLTAGE

# Drop v_nom; PyPSA recomputes it after clustering (all lines are now 345 kV)
n.lines = n.lines.drop(columns=["v_nom"])

# line_length_factor = 1.0: line lengths are straight-line distances. Impedances come
# from the Kron reduction (kron_reduce_sparse.py) or the conductor fallback, not from
# PyPSA line types.
clustering = get_clustering_from_busmap(
    n,
    busmap,
    line_length_factor=1.0,
    bus_strategies={"Pd": "sum"},
    aggregate_generators_weighted=False,
)
cn = clustering.network
cn.buses["reeds_zone"] = cn.buses.index.map(cluster_reeds_zone)

# Order zones by descending Texas7k Pd, so zone 1 (the ACOPF reference zone) is the
# largest load centre. Later scripts number zones by this bus order.
cn.buses = cn.buses.sort_values("Pd", ascending=False)
cn.lines["bus0"] = cn.lines["bus0"].astype(str)
cn.lines["bus1"] = cn.lines["bus1"].astype(str)
print(f"\nReordered zones by descending real Pd -- zone 1 is now bus {cn.buses.index[0]} "
      f"(Pd={cn.buses['Pd'].iloc[0]:.1f} MW, the real highest-demand cluster)")

print(f"\nClustered network: {len(cn.buses)} buses, {len(cn.lines)} lines")
print(cn.lines[["bus0", "bus1", "s_nom"]].assign(
    z0=cn.lines.bus0.map(cn.buses.reeds_zone),
    z1=cn.lines.bus1.map(cn.buses.reeds_zone),
))

cn.export_to_netcdf(CLUSTERED_NC)
print("\nSaved clustered network.")

# Save the bus -> zone map. Later scripts (e.g. kron_reduce_sparse.py) load it rather than
# re-clustering, which could assign different labels.
busmap.to_csv(
    BUSMAP_CSV,
    header=["cluster"],
)
print("Saved real busmap to texas7k_busmap_20zone.csv (authoritative, do not reconstruct)")
