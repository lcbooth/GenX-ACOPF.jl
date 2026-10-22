"""Equivalent impedance between zones by Kron reduction of the Texas7k network.

    python kron_reduce_sparse.py

Follows the border-bus-preserving reduction of Ploussard, Olmos & Ramos (IEEE Trans. Power
Systems 33(6), 2018):

  1. Build the network admittance matrix Y from all lines and transformers, in per unit on
     Texas7k's 100 MVA base.
  2. Keep the border buses (buses with a branch to another zone) and eliminate all others:
     Y_red = Y_RR - Y_RE @ Y_EE^-1 @ Y_ER, using a sparse LU factorization of Y_EE.
  3. Merge each zone's border buses into one node: Y_zone = M @ Y_red @ M.T.
  4. Read each zone pair's equivalent impedance from the off-diagonal entry,
     z_ij = -1 / Y_zone[i, j], and convert it to ohms at 345 kV.

The result is exact for the DC power flow (see validate_kron_borderbus.py). Line capacities
are not derived from it; they come from lpe_all_lines.py.

Writes outputs/kron_reduced_capacity_20zone.csv (zone_i, zone_j, r_eq_pu, x_eq_pu,
r_eq_ohm, x_eq_ohm) and the border-bus admittance matrix outputs/kron_reduced_Y.csv.
"""
import numpy as np
import pandas as pd
import pypsa
import networkx as nx
import scipy.sparse as sp
from scipy.sparse.linalg import splu
from sklearn.cluster import KMeans

from paths import (
    BUSES_WITH_REEDS_ZONE_CSV,
    BUSMAP_CSV,
    CLUSTERED_NC,
    KRON_CAPACITY_CSV,
    KRON_Y_CSV,
    RAW_NETWORK_NC,
)

NETWORK_RAW_NC = str(RAW_NETWORK_NC)
CLUSTERED_NC = str(CLUSTERED_NC)

# ---------------------------------------------------------------------------
# 1. Load real raw ERCOT-only, connected network (same filtering as cluster_texas7k.py)
# ---------------------------------------------------------------------------
n = pypsa.Network(NETWORK_RAW_NC)
buses_reeds = pd.read_csv(
    str(BUSES_WITH_REEDS_ZONE_CSV),
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

G = nx.Graph()
G.add_nodes_from(n.buses.index)
G.add_edges_from(zip(n.lines.bus0, n.lines.bus1))
G.add_edges_from(zip(n.transformers.bus0, n.transformers.bus1))
main_component = max(nx.connected_components(G), key=len)
islanded = set(n.buses.index) - main_component
n.mremove("Bus", list(islanded))
drop_lines2 = n.lines.index[~n.lines.bus0.isin(n.buses.index) | ~n.lines.bus1.isin(n.buses.index)]
n.mremove("Line", drop_lines2)
drop_xfmr2 = n.transformers.index[~n.transformers.bus0.isin(n.buses.index) | ~n.transformers.bus1.isin(n.buses.index)]
n.mremove("Transformer", drop_xfmr2)

print(f"Real connected ERCOT-only network: {len(n.buses)} buses, {len(n.lines)} lines, {len(n.transformers)} transformers")

# ---------------------------------------------------------------------------
# 2. Load the bus -> zone map saved by cluster_texas7k.py. It is loaded rather than
#    recomputed, because re-running the clustering can assign different cluster labels.
# ---------------------------------------------------------------------------
busmap = pd.read_csv(
    str(BUSMAP_CSV),
    index_col=0,
)["cluster"]
busmap.index = busmap.index.astype(str)
busmap = busmap.astype(str)
print(f"Loaded real busmap: {busmap.nunique()} clusters")

# ---------------------------------------------------------------------------
# 3. Kept nodes: all border buses, i.e. buses with a line or transformer to another zone.
#    Zone pairs typically have several parallel branches (median 6, up to 36), and keeping
#    every border bus represents all of them.
#
#    Zone numbers follow the clustered network's bus order (zone = position + 1), the
#    convention used by every other step.
n_clustered_for_order = pypsa.Network(CLUSTERED_NC)
real_zone_order = n_clustered_for_order.buses.index.tolist()  # e.g. ['0','1','10','11',...]
zone_number = {cl: i + 1 for i, cl in enumerate(real_zone_order)}
print(f"Zone order: {real_zone_order}")

zone_of_bus = busmap.reindex(n.buses.index)
border_buses = set()
for br_row in list(n.lines.itertuples()) + list(n.transformers.itertuples()):
    b0, b1 = str(br_row.bus0), str(br_row.bus1)
    z0, z1 = zone_of_bus.get(b0), zone_of_bus.get(b1)
    if z0 is not None and z1 is not None and z0 != z1:
        border_buses.add(b0)
        border_buses.add(b1)

rep_bus_list = sorted(border_buses)  # order does not matter; zones are assembled in step 8b
print(f"Representative (border) buses kept: {len(rep_bus_list)} "
      f"(eliminating {len(n.buses) - len(rep_bus_list)} true-interior buses)")

# ---------------------------------------------------------------------------
# 4. Sparse admittance matrix Y from lines and transformers, in per unit.
#
#    build_and_cluster_texas7k.py stores r/x in ohms at each branch's own voltage
#    (r_ohm = r_pu * v_nom^2 / 100), so branches are on ten different voltage bases. They
#    are converted back to per unit on the common 100 MVA base before building Y. For
#    transformers, v_nom is the bus0 (primary) voltage, as in that conversion.
S_BASE_MVA = 100.0


def to_pu(r_ohm, x_ohm, v_nom_kv):
    base_z = (v_nom_kv ** 2) / S_BASE_MVA
    return r_ohm / base_z, x_ohm / base_z


all_buses = n.buses.index.tolist()
bus_idx = {b: i for i, b in enumerate(all_buses)}
n_buses = len(all_buses)

rows, cols, vals = [], [], []


def add_branch_admittances(bus0, bus1, r_pu, x_pu):
    y = 1.0 / (r_pu + 1j * x_pu)
    i0, i1 = bus_idx[bus0], bus_idx[bus1]
    # off-diagonal
    rows.extend([i0, i1]); cols.extend([i1, i0]); vals.extend([-y, -y])
    # diagonal contributions
    rows.extend([i0, i1]); cols.extend([i0, i1]); vals.extend([y, y])


for _, row in n.lines.iterrows():
    r_pu, x_pu = to_pu(row.r, row.x, row.v_nom)
    add_branch_admittances(row.bus0, row.bus1, r_pu, x_pu)
for _, row in n.transformers.iterrows():
    r_pu, x_pu = to_pu(row.r, row.x, row.v_nom)
    add_branch_admittances(row.bus0, row.bus1, r_pu, x_pu)

Y = sp.coo_matrix((vals, (rows, cols)), shape=(n_buses, n_buses), dtype=complex).tocsr()
Y.sum_duplicates()
print(f"Built sparse Y: {Y.shape}, {Y.nnz} nonzeros ({Y.nnz / (n_buses**2):.6%} dense)")

# ---------------------------------------------------------------------------
# 5. Partition into kept (R) and eliminated (E) nodes, and reorder Y
# ---------------------------------------------------------------------------
rep_idx = sorted(bus_idx[b] for b in rep_bus_list)
elim_idx = sorted(set(range(n_buses)) - set(rep_idx))
print(f"Representative nodes: {len(rep_idx)}, eliminated nodes: {len(elim_idx)}")

perm = elim_idx + rep_idx  # eliminated first, representative last
Y_perm = Y[perm, :][:, perm].tocsc()
n_e = len(elim_idx)

Y_EE = Y_perm[:n_e, :n_e]
Y_ER = Y_perm[:n_e, n_e:]
Y_RE = Y_perm[n_e:, :n_e]
Y_RR = Y_perm[n_e:, n_e:]

# ---------------------------------------------------------------------------
# 6. Sparse Kron reduction: Y_reduced = Y_RR - Y_RE @ (Y_EE^-1 @ Y_ER), computed via
#    sparse LU factorization + solve (never forms Y_EE^-1 explicitly)
# ---------------------------------------------------------------------------
print("Factorizing Y_EE (sparse LU)...")
lu = splu(Y_EE.tocsc())
print("Solving Y_EE @ X = Y_ER for X (sparse triangular solves)...")
X = lu.solve(Y_ER.toarray())  # Sparse LU factors with a dense right-hand side
Y_reduced = Y_RR.toarray() - Y_RE.toarray() @ X

print(f"\nY_reduced shape: {Y_reduced.shape} (real Kron-reduced admittance among the "
      f"{len(rep_bus_list)} border-bus representative nodes)")

# ---------------------------------------------------------------------------
# 7. Save real reduced admittance matrix (bus-level, not yet aggregated to zones)
# ---------------------------------------------------------------------------
rep_bus_order = rep_bus_list
Y_reduced_df = pd.DataFrame(Y_reduced, index=rep_bus_order, columns=rep_bus_order)
OUT = str(KRON_Y_CSV)
Y_reduced_df.map(lambda z: f"{z.real:.6e}{'+' if z.imag>=0 else ''}{z.imag:.6e}j").to_csv(OUT)
print(f"Saved real Kron-reduced bus-level admittance matrix ({len(rep_bus_order)}x"
      f"{len(rep_bus_order)}) to {OUT}")

# ---------------------------------------------------------------------------
# 8. Equivalent impedance per zone pair, in ohms at 345 kV.
#
#    With the standard Y-bus convention, the off-diagonal entry is the negative of the
#    branch admittance, Y_ij = -1/z_ij, so z_ij = -1/Y_ij = r_eq + j*x_eq. The check below
#    confirms the formula on a single branch.
# ---------------------------------------------------------------------------
V_KV = 345.0

# Check the impedance formula on a single branch: r = 1, x = 5 must be recovered exactly.
_test_r, _test_x = 1.0, 5.0
_test_y = 1.0 / (_test_r + 1j * _test_x)
_test_Yij = -_test_y  # off-diagonal Y-bus entry for this single branch
_test_z = -1.0 / _test_Yij
assert abs(_test_z.real - _test_r) < 1e-9 and abs(_test_z.imag - _test_x) < 1e-9, (
    f"z=-1/Y_ij sign/formula check FAILED: recovered {_test_z}, expected {_test_r}+{_test_x}j")
print(f"Synthetic branch check OK: r=1,x=5 -> Y_ij={_test_Yij:.4f} -> recovered z={_test_z:.4f}")

# ---------------------------------------------------------------------------
# 8b. Merge each zone's border buses into one node. Summing a zone's rows and columns
#     shorts its border buses together; the branches between them become self-loops and
#     drop out. No further elimination is needed, so the result stays exact.
# ---------------------------------------------------------------------------
bus_to_zone_num = {b: zone_number[zone_of_bus[b]] for b in rep_bus_order}
zone_groups = {}
for b in rep_bus_order:
    zone_groups.setdefault(bus_to_zone_num[b], []).append(b)

# Merge (short) each zone's border buses into one row/column by summing them.
n_zones_final = len(zone_groups)
zone_order_nums = sorted(zone_groups)  # 1..20
idx_of_bus = {b: i for i, b in enumerate(rep_bus_order)}
merge_matrix = np.zeros((n_zones_final, len(rep_bus_order)), dtype=complex)
for zi, znum in enumerate(zone_order_nums):
    for b in zone_groups[znum]:
        merge_matrix[zi, idx_of_bus[b]] = 1.0

# Y_zone = M @ Y_reduced @ M.T, where M is the 0/1 bus -> zone membership matrix.
Y_zone = merge_matrix @ Y_reduced @ merge_matrix.T
print(f"\nMerged {len(rep_bus_order)} border buses -> {n_zones_final} zone-equivalent nodes "
      f"(shorting each zone's border buses together, exact -- no further elimination).")

print(f"\nReal Kron-reduced equivalent inter-zone impedance (p.u. on {S_BASE_MVA:.0f} MVA base, "
      f"converted to real ohms at {V_KV:.0f} kV):")
impedance_rows = []
for i in range(n_zones_final):
    for j in range(i + 1, n_zones_final):
        yij = Y_zone[i, j]
        if abs(yij) < 1e-9:
            continue
        z_eq_pu = -1.0 / yij  # = r_eq_pu + j*x_eq_pu
        r_eq_pu, x_eq_pu = z_eq_pu.real, z_eq_pu.imag
        if x_eq_pu <= 0:
            continue
        base_z = (V_KV ** 2) / S_BASE_MVA
        r_eq_ohm, x_eq_ohm = r_eq_pu * base_z, x_eq_pu * base_z
        zi, zj = zone_order_nums[i], zone_order_nums[j]
        impedance_rows.append({
            "zone_i": zi, "zone_j": zj,
            "r_eq_pu": r_eq_pu, "x_eq_pu": x_eq_pu,
            "r_eq_ohm": r_eq_ohm, "x_eq_ohm": x_eq_ohm,
        })
        print(f"  zone {zi} <-> zone {zj}: R_eq={r_eq_ohm:.4f} ohm, X_eq={x_eq_ohm:.4f} ohm "
              f"({x_eq_pu:.6f} p.u.)")

imp_df = pd.DataFrame(impedance_rows).sort_values(["zone_i", "zone_j"])
CAP_OUT = str(KRON_CAPACITY_CSV)
imp_df.to_csv(CAP_OUT, index=False)
print(f"\nSaved real Kron-reduced equivalent impedance (all {len(imp_df)} zone pairs) to {CAP_OUT}")
print(f"X_eq ohm stats: min={imp_df.x_eq_ohm.min():.2f}, median={imp_df.x_eq_ohm.median():.2f}, "
      f"max={imp_df.x_eq_ohm.max():.2f}")
