"""Validate the Kron reduction in kron_reduce_sparse.py.

    python validate_kron_borderbus.py

For several zone pairs, injects +1/-1 p.u. at the two zones and compares the voltage-angle
difference from a DC power flow on the full network (with each zone's border buses shorted
together) with the reduced 20-zone network. The reduction is exact, so the two should agree
to floating-point precision.

DC power flow: theta = B^-1 @ P, with B the imaginary part of Y without the slack row and
column.
"""
import numpy as np
import pandas as pd
import pypsa
import networkx as nx
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from paths import BUSES_WITH_REEDS_ZONE_CSV, BUSMAP_CSV, CLUSTERED_NC, RAW_NETWORK_NC

S_BASE_MVA = 100.0


def to_pu(r_ohm, x_ohm, v_nom_kv):
    base_z = (v_nom_kv ** 2) / S_BASE_MVA
    return r_ohm / base_z, x_ohm / base_z


# --- rebuild the exact same filtered, per-unit full network as the main script ----------
n = pypsa.Network(str(RAW_NETWORK_NC))
br = pd.read_csv(str(BUSES_WITH_REEDS_ZONE_CSV), index_col=0)
br.index = br.index.astype(str)
n.buses["reeds_zone"] = br["reeds_zone"]
ercot = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}
keep = n.buses[n.buses.reeds_zone.isin(ercot)].index
n.mremove("Bus", n.buses.index.difference(keep))
n.mremove("Line", n.lines.index[~n.lines.bus0.isin(keep) | ~n.lines.bus1.isin(keep)])
n.mremove("Transformer", n.transformers.index[~n.transformers.bus0.isin(keep) | ~n.transformers.bus1.isin(keep)])
G = nx.Graph(); G.add_nodes_from(n.buses.index)
G.add_edges_from(zip(n.lines.bus0, n.lines.bus1)); G.add_edges_from(zip(n.transformers.bus0, n.transformers.bus1))
main = max(nx.connected_components(G), key=len)
n.mremove("Bus", list(set(n.buses.index) - main))
n.mremove("Line", n.lines.index[~n.lines.bus0.isin(n.buses.index) | ~n.lines.bus1.isin(n.buses.index)])
n.mremove("Transformer", n.transformers.index[~n.transformers.bus0.isin(n.buses.index) | ~n.transformers.bus1.isin(n.buses.index)])
print(f"Full network: {len(n.buses)} buses")

busmap = pd.read_csv(str(BUSMAP_CSV), index_col=0)["cluster"].astype(str)
busmap.index = busmap.index.astype(str)
n_clustered = pypsa.Network(str(CLUSTERED_NC))
real_zone_order = n_clustered.buses.index.tolist()
zone_number = {cl: i + 1 for i, cl in enumerate(real_zone_order)}
zone_of_bus = busmap.reindex(n.buses.index)

all_buses = n.buses.index.tolist()
bus_idx = {b: i for i, b in enumerate(all_buses)}
n_buses = len(all_buses)

rows, cols, vals = [], [], []
def add_branch(bus0, bus1, r_pu, x_pu):
    y = 1.0 / (r_pu + 1j * x_pu)
    i0, i1 = bus_idx[bus0], bus_idx[bus1]
    rows.extend([i0, i1]); cols.extend([i1, i0]); vals.extend([-y, -y])
    rows.extend([i0, i1]); cols.extend([i0, i1]); vals.extend([y, y])

for _, row in n.lines.iterrows():
    r_pu, x_pu = to_pu(row.r, row.x, row.v_nom)
    add_branch(row.bus0, row.bus1, r_pu, x_pu)
for _, row in n.transformers.iterrows():
    r_pu, x_pu = to_pu(row.r, row.x, row.v_nom)
    add_branch(row.bus0, row.bus1, r_pu, x_pu)

Y_full = sp.coo_matrix((vals, (rows, cols)), shape=(n_buses, n_buses), dtype=complex).tocsr()
Y_full.sum_duplicates()
B_full = -Y_full.imag.tocsc()  # DC power flow susceptance matrix (standard sign convention)
print(f"Built full-network B matrix: {B_full.shape}")

# --- load the reduced 20-zone impedance from kron_reduce_sparse.py's CSV output ---------
from paths import KRON_CAPACITY_CSV
imp = pd.read_csv(str(KRON_CAPACITY_CSV))
n_zones = len(real_zone_order)

# Rebuild Y_zone (20x20) from the saved r_eq/x_eq (undo the ohms conversion back to p.u.)
V_KV = 345.0
base_z = (V_KV ** 2) / S_BASE_MVA
Y_zone = np.zeros((n_zones, n_zones), dtype=complex)
for _, r in imp.iterrows():
    i, j = int(r.zone_i) - 1, int(r.zone_j) - 1
    z_pu = complex(r.r_eq_pu, r.x_eq_pu)
    y_pu = 1.0 / z_pu
    Y_zone[i, j] += -y_pu
    Y_zone[j, i] += -y_pu
    Y_zone[i, i] += y_pu
    Y_zone[j, j] += y_pu
B_zone = -Y_zone.imag
print(f"Rebuilt B_zone from saved impedance: {B_zone.shape}")

# --- identify each zone's FULL border-bus set (not one representative bus) --------------
# The reduced network treats each zone's border buses as shorted together, so the full
# network is compared in the same state: with all of a zone's border buses shorted.
border_buses_by_zone = {}
for l in list(n.lines.itertuples()) + list(n.transformers.itertuples()):
    b0, b1 = str(l.bus0), str(l.bus1)
    z0, z1 = zone_of_bus.get(b0), zone_of_bus.get(b1)
    if z0 is not None and z1 is not None and z0 != z1:
        border_buses_by_zone.setdefault(zone_number[z0], set()).add(b0)
        border_buses_by_zone.setdefault(zone_number[z1], set()).add(b1)
print(f"Border buses per zone (sample): "
      f"{[(k, len(v)) for k, v in list(border_buses_by_zone.items())[:5]]}")

# --- physically SHORT each zone's border buses together in the full network -------------
# A near-zero-impedance tie from each border bus to one hub bus per zone makes the zone's
# border buses equipotential, matching the merge step in kron_reduce_sparse.py.
Y_full_lil = Y_full.tolil()
TIE_Y = 1e9  # near-zero impedance (huge admittance) -- shorts buses without singularizing B
zone_hub_bus = {}
for znum, members in border_buses_by_zone.items():
    members = sorted(members)
    hub = members[0]
    zone_hub_bus[znum] = hub
    for b in members[1:]:
        i0, i1 = bus_idx[hub], bus_idx[b]
        y = 1j * TIE_Y  # inductive tie so it only adds to B (imaginary part), not R
        Y_full_lil[i0, i1] -= y; Y_full_lil[i1, i0] -= y
        Y_full_lil[i0, i0] += y; Y_full_lil[i1, i1] += y
Y_full_shorted = Y_full_lil.tocsr()
B_full_shorted = -Y_full_shorted.imag.tocsc()
print(f"Shorted {sum(len(v) for v in border_buses_by_zone.values())} border buses into "
      f"{len(zone_hub_bus)} zone hub buses via near-zero-impedance ties.")

# --- run several test injections between real, Kron-connected zone pairs ----------------
imp["pair"] = list(zip(imp.zone_i.astype(int), imp.zone_j.astype(int)))
test_pairs = imp.sample(min(8, len(imp)), random_state=0)[["zone_i", "zone_j"]].values.tolist()

slack_zone = 1

print(f"\nTesting {len(test_pairs)} zone-pair injections (angle diff: full network with "
      f"border buses shorted, vs. Kron-reduced):")
max_err = 0.0
for zi, zj in test_pairs:
    zi, zj = int(zi), int(zj)
    if zi not in zone_hub_bus or zj not in zone_hub_bus:
        continue

    # --- full (border-shorted) network DC power flow ---
    P_full = np.zeros(n_buses)
    P_full[bus_idx[zone_hub_bus[zi]]] = 1.0
    P_full[bus_idx[zone_hub_bus[zj]]] = -1.0
    keep_idx = [k for k in range(n_buses) if k != bus_idx[zone_hub_bus[slack_zone]]]
    B_red = B_full_shorted[keep_idx, :][:, keep_idx].tocsc()
    P_red = P_full[keep_idx]
    theta_red = spsolve(B_red, P_red)
    theta_full = np.zeros(n_buses)
    theta_full[keep_idx] = theta_red
    angle_diff_full = theta_full[bus_idx[zone_hub_bus[zi]]] - theta_full[bus_idx[zone_hub_bus[zj]]]

    # --- reduced 20-zone DC power flow ---
    P_zone = np.zeros(n_zones)
    P_zone[zi - 1] = 1.0
    P_zone[zj - 1] = -1.0
    keep_z = [k for k in range(n_zones) if k != (slack_zone - 1)]
    B_zone_red = B_zone[np.ix_(keep_z, keep_z)]
    P_zone_red = P_zone[keep_z]
    theta_zone_red = np.linalg.solve(B_zone_red, P_zone_red)
    theta_zone = np.zeros(n_zones)
    theta_zone[keep_z] = theta_zone_red
    angle_diff_zone = theta_zone[zi - 1] - theta_zone[zj - 1]

    err = abs(angle_diff_full - angle_diff_zone)
    max_err = max(max_err, err)
    print(f"  zone {zi} <-> zone {zj}: full={angle_diff_full:.8f} rad, "
          f"reduced={angle_diff_zone:.8f} rad, abs_err={err:.2e}")

print(f"\nWorst absolute angle error across tested pairs: {max_err:.2e}")
print("(The reduction is exact, so this should be at floating-point precision, ~1e-10 or below.)")
