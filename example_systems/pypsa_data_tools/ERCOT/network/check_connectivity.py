"""Report the connected components of the ERCOT network before clustering.

    python check_connectivity.py

Checks connectivity with lines and transformers as edges, and with lines only. Texas7k has
9 buses in 4 small islands, which cluster_texas7k.py drops.
"""
import pandas as pd
import pypsa
from scipy.sparse import csgraph
import numpy as np

n = pypsa.Network(str(RAW_NETWORK_NC))
buses_reeds = pd.read_csv(
    str(BUSES_WITH_REEDS_ZONE_CSV),
    index_col=0,
)
buses_reeds.index = buses_reeds.index.astype(str)
n.buses["reeds_zone"] = buses_reeds["reeds_zone"]

ercot_zones = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}
ercot_buses = n.buses[n.buses["reeds_zone"].isin(ercot_zones)].index
print(f"ERCOT-zone buses: {len(ercot_buses)} / {len(n.buses)}")

# subset network to ERCOT-only buses + only lines/transformers fully within that set
n_ercot = n.copy()
drop_buses = n.buses.index.difference(ercot_buses)
n_ercot.mremove("Bus", drop_buses)
drop_lines = n_ercot.lines.index[
    ~n_ercot.lines.bus0.isin(ercot_buses) | ~n_ercot.lines.bus1.isin(ercot_buses)
]
n_ercot.mremove("Line", drop_lines)
drop_xfmr = n_ercot.transformers.index[
    ~n_ercot.transformers.bus0.isin(ercot_buses) | ~n_ercot.transformers.bus1.isin(ercot_buses)
]
n_ercot.mremove("Transformer", drop_xfmr)
print(f"ERCOT-only network: {len(n_ercot.buses)} buses, {len(n_ercot.lines)} lines, {len(n_ercot.transformers)} transformers")

n_ercot.determine_network_topology()

# check connectivity treating Lines AND Transformers as edges (both are electrical connections)
import networkx as nx

from paths import (
    BUSES_WITH_REEDS_ZONE_CSV,
    RAW_NETWORK_NC,
)
G = nx.Graph()
G.add_nodes_from(n_ercot.buses.index)
G.add_edges_from(zip(n_ercot.lines.bus0, n_ercot.lines.bus1))
G.add_edges_from(zip(n_ercot.transformers.bus0, n_ercot.transformers.bus1))

components = list(nx.connected_components(G))
print(f"\nNumber of connected components (Lines + Transformers as edges): {len(components)}")
sizes = sorted([len(c) for c in components], reverse=True)
print(f"Component sizes: {sizes[:20]}{'...' if len(sizes) > 20 else ''}")

if len(components) > 1:
    print("\n*** NETWORK IS DISCONNECTED ***")
    for i, comp in enumerate(components):
        if len(comp) < 20:
            print(f"Small component {i} (size {len(comp)}): {sorted(comp)}")
else:
    print("\nNetwork is fully connected via Lines+Transformers.")

# Also check connectivity with lines only, since whether transformers count as edges
# depends on the PyPSA function used
G_lines_only = nx.Graph()
G_lines_only.add_nodes_from(n_ercot.buses.index)
G_lines_only.add_edges_from(zip(n_ercot.lines.bus0, n_ercot.lines.bus1))
components_lo = list(nx.connected_components(G_lines_only))
print(f"\nNumber of connected components (Lines ONLY, no Transformers): {len(components_lo)}")
sizes_lo = sorted([len(c) for c in components_lo], reverse=True)
print(f"Component sizes (lines only): {sizes_lo[:20]}{'...' if len(sizes_lo) > 20 else ''}")
