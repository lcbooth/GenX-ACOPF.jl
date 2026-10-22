"""Remove inter-zone lines that have no real 345 kV corridor between the two zones.

    python verify_345_lines.py

Works on the clustered network (intermediates/texas7k_clustered_20.nc) and the bus -> zone
map. A line is kept if a real 345 kV branch connects the two zones, or if either zone has no
345 kV connection at all (so zones served only at lower voltage keep their ties). A line is
only removed if the zones stay connected. Updates the clustered network in place; the bus ->
zone map is unchanged.
"""
import pandas as pd
import pypsa
import networkx as nx

from paths import (
    BUSMAP_CSV, CLUSTERED_NC, BUSES_WITH_REEDS_ZONE_CSV, RAW_NETWORK_NC, require,
)

busmap = pd.read_csv(require(BUSMAP_CSV, "busmap"), index_col=0)["cluster"].astype(str)
busmap.index = busmap.index.astype(str)
cn = pypsa.Network(str(require(CLUSTERED_NC, "clustered network")))
cl_to_zone = {cl: i + 1 for i, cl in enumerate(cn.buses.index)}
zone_of_raw = busmap.map(cl_to_zone)

# real 345 kV lines in the ERCOT-filtered raw network
raw = pypsa.Network(str(require(RAW_NETWORK_NC, "raw net")))
br = pd.read_csv(BUSES_WITH_REEDS_ZONE_CSV, index_col=0); br.index = br.index.astype(str)
raw.buses["reeds_zone"] = br["reeds_zone"]
ercot = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}
keep = raw.buses[raw.buses.reeds_zone.isin(ercot)].index
l345 = raw.lines[(raw.lines.v_nom >= 345) & raw.lines.bus0.isin(keep) & raw.lines.bus1.isin(keep)]

# zone-pairs with >=1 real 345 kV line between their member buses
pairs_345 = set()
for _, l in l345.iterrows():
    z0, z1 = zone_of_raw.get(str(l.bus0)), zone_of_raw.get(str(l.bus1))
    if z0 and z1 and z0 != z1:
        pairs_345.add((min(z0, z1), max(z0, z1)))

# Zones with at least one real 345 kV corridor. Zones without any (served only at 138 kV,
# e.g. the far south-west around Del Rio) keep their lower-voltage ties: a line is kept if it
# is a 345 kV corridor or if either of its zones has no 345 kV corridor.
zones_with_345 = set()
for a, b in pairs_345:
    zones_with_345.add(a); zones_with_345.add(b)

keep_graph = nx.Graph()
keep_graph.add_nodes_from(cn.buses.index)
line_pair = {}
for lid, l in cn.lines.iterrows():
    z0, z1 = cl_to_zone[str(l.bus0)], cl_to_zone[str(l.bus1)]
    line_pair[lid] = (min(z0, z1), max(z0, z1))
    keep_graph.add_edge(str(l.bus0), str(l.bus1))

def is_periphery(pair):
    return pair[0] not in zones_with_345 or pair[1] not in zones_with_345

drop_candidates = [lid for lid, p in line_pair.items()
                   if p not in pairs_345 and not is_periphery(p)]
dropped = []
for lid in drop_candidates:
    l = cn.lines.loc[lid]
    g2 = keep_graph.copy()
    if g2.has_edge(str(l.bus0), str(l.bus1)):
        g2.remove_edge(str(l.bus0), str(l.bus1))
    if nx.is_connected(g2):
        keep_graph = g2
        dropped.append(lid)

cn.mremove("Line", dropped)
print(f"345 kV line verify: dropped {len(dropped)}/{len(drop_candidates)} non-345kV "
      f"inter-zone lines (kept connected); {len(cn.lines)} lines remain")
cn.export_to_netcdf(str(CLUSTERED_NC))
print("Saved verified CLUSTERED_NC (busmap unchanged).")
