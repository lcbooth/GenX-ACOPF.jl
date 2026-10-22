"""Line-limit-preserving equivalent (LPE) transfer capacity between zones.

Implements Jang, Mohapatra, Overbye & Zhu (IEEE PECI 2013). For a unit transfer between
two zones, every original branch's DC PTDF is computed, and the transfer is limited by the
most constraining branch:

    TTC = min_k ( s_nom_k / |PTDF_k| )

This carries the real branch limits of Texas7k through the clustering. lpe_all_lines.py
applies it to every inter-zone line.

Conventions:
  - Lossless DC: B = 1/X; resistance is dropped. (With full complex admittance, the 206
    near-zero-resistance transformers would dominate the solve.)
  - Injection: a point-to-point unit transfer between each zone's peak-demand bus (see
    peak_demand_buses()). Spreading the injection over all buses in a zone dilutes the
    PTDF at bottleneck branches and gives higher capacities.
  - Network: PTDFs are computed on the original, unreduced network.
"""
import numpy as np
import pandas as pd
import pypsa
import networkx as nx
import scipy.sparse as sp
from scipy.sparse.linalg import splu

ERCOT_REEDS_ZONES = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}


def load_ercot_connected_network(raw_network_nc, buses_reeds_csv):
    """Raw Texas7k network, filtered to ERCOT zones with islanded buses dropped.

    Filtering matches cluster_texas7k.py, so the bus set matches the busmap's.
    """
    n = pypsa.Network(str(raw_network_nc))
    buses_reeds = pd.read_csv(buses_reeds_csv, index_col=0)
    buses_reeds.index = buses_reeds.index.astype(str)
    n.buses["reeds_zone"] = buses_reeds["reeds_zone"]

    ercot_buses = n.buses[n.buses["reeds_zone"].isin(ERCOT_REEDS_ZONES)].index
    n.mremove("Bus", n.buses.index.difference(ercot_buses))
    n.mremove("Line", n.lines.index[~n.lines.bus0.isin(ercot_buses) | ~n.lines.bus1.isin(ercot_buses)])
    n.mremove("Transformer", n.transformers.index[~n.transformers.bus0.isin(ercot_buses) | ~n.transformers.bus1.isin(ercot_buses)])

    G = nx.Graph()
    G.add_nodes_from(n.buses.index)
    G.add_edges_from(zip(n.lines.bus0, n.lines.bus1))
    G.add_edges_from(zip(n.transformers.bus0, n.transformers.bus1))
    islanded = set(n.buses.index) - max(nx.connected_components(G), key=len)
    n.mremove("Bus", list(islanded))
    n.mremove("Line", n.lines.index[~n.lines.bus0.isin(n.buses.index) | ~n.lines.bus1.isin(n.buses.index)])
    n.mremove("Transformer", n.transformers.index[~n.transformers.bus0.isin(n.buses.index) | ~n.transformers.bus1.isin(n.buses.index)])
    return n


def representative_buses(n, busmap):
    """Each cluster's most central bus. Used for reporting only."""
    reps = {}
    for cl in busmap.unique():
        members = busmap[busmap == cl].index
        xy = n.buses.loc[members, ["x", "y"]]
        c = xy.mean()
        reps[cl] = np.hypot(xy["x"] - c["x"], xy["y"] - c["y"]).idxmin()
    return reps


def load_weights(n, busmap, clusters):
    """Per-bus load weights for distributed injection within each cluster.

    Buses without load get a small floor weight so that every bus participates. Used only
    for comparison; compute_lpe_capacities() uses peak-demand-bus injection.
    """
    weights = {}
    for cl in clusters:
        members = busmap[busmap == cl].index
        pd_vals = n.buses.loc[members, "Pd"].clip(lower=0.0)
        if pd_vals.sum() <= 0:
            pd_vals = pd.Series(1.0, index=members)
        else:
            pd_vals = pd_vals + pd_vals[pd_vals > 0].mean() * 0.01
        weights[cl] = pd_vals.to_dict()
    return weights


def peak_demand_buses(n, busmap, clusters):
    """Each cluster's largest-load bus, used as the injection and withdrawal point."""
    peak = {}
    for cl in clusters:
        members = busmap[busmap == cl].index
        pd_vals = n.buses.loc[members, "Pd"]
        peak[cl] = pd_vals.idxmax()
    return peak


class DCNetwork:
    """DC B-matrix (B = 1/X) with a standard slack-referenced PTDF solve.

    Full bus set, one grounded slack, solve B_red @ theta = inj over non-slack buses, then
    branch flow = b_ij*(theta_i - theta_j). Power is conserved by construction; PTDFs are
    slack-invariant for a balanced transfer. check_conservation() enforces this.
    """

    def __init__(self, n, rep_bus_list):
        self.all_buses = n.buses.index.tolist()
        self.bus_idx = {b: i for i, b in enumerate(self.all_buses)}
        n_buses = len(self.all_buses)

        rows, cols, vals = [], [], []
        self.branch_records = []

        def add(bus0, bus1, x, s_nom, v_nom):
            b = 1.0 / x
            i0, i1 = self.bus_idx[bus0], self.bus_idx[bus1]
            rows.extend([i0, i1]); cols.extend([i1, i0]); vals.extend([-b, -b])
            rows.extend([i0, i1]); cols.extend([i0, i1]); vals.extend([b, b])
            self.branch_records.append((bus0, bus1, b, s_nom, v_nom))

        for _, r in n.lines.iterrows():
            add(r.bus0, r.bus1, r.x, r.s_nom, r.v_nom)
        for _, r in n.transformers.iterrows():
            add(r.bus0, r.bus1, r.x, r.s_nom, r.v_nom)

        B = sp.coo_matrix((vals, (rows, cols)), shape=(n_buses, n_buses), dtype=float).tocsr()
        B.sum_duplicates()

        self.rep_set = set(rep_bus_list)
        # Slack at the highest-degree bus (numerically stablest); PTDFs for a balanced
        # transfer are slack-invariant regardless.
        degree = np.asarray((B != 0).sum(axis=1)).ravel()
        self.slack_idx = int(np.argmax(degree))
        self.keep = [i for i in range(n_buses) if i != self.slack_idx]
        self.red_pos = {self.all_buses[i]: p for p, i in enumerate(self.keep)}

        self.lu = splu(B[self.keep, :][:, self.keep].tocsc())

        # Every branch is a TTC candidate -- a limit anywhere constrains the transfer.
        self.elim_branches = list(self.branch_records)

    def _solve(self, inj_map):
        """inj_map: {bus: injection}, summing to ~0. Returns {(bus0, bus1): flow}."""
        inj = np.zeros(len(self.keep), dtype=float)
        for bus, p in inj_map.items():
            pos = self.red_pos.get(bus)
            if pos is not None:  # injection at the slack is absorbed by the reference
                inj[pos] += p
        theta_red = self.lu.solve(inj)

        def angle(bus):
            p = self.red_pos.get(bus)
            return 0.0 if p is None else theta_red[p]

        return {(b0, b1): b * (angle(b0) - angle(b1))
                for (b0, b1, b, s_nom, v_nom) in self.elim_branches}

    def ptdf(self, src_bus, sink_bus):
        """Point-to-point unit transaction. Diagnostics only; see ptdf_distributed."""
        if src_bus == sink_bus:
            return {}
        return self._solve({src_bus: 1.0, sink_bus: -1.0})

    def ptdf_distributed(self, src_weights, sink_weights):
        """Unit zone-to-zone transfer, injection spread over each zone's buses.

        src_weights/sink_weights: {bus: weight}, each normalized to sum to 1 here.
        """
        s_tot = sum(src_weights.values())
        k_tot = sum(sink_weights.values())
        if s_tot <= 0 or k_tot <= 0:
            return {}
        inj = {}
        for b, w in src_weights.items():
            inj[b] = inj.get(b, 0.0) + w / s_tot
        for b, w in sink_weights.items():
            inj[b] = inj.get(b, 0.0) - w / k_tot
        return self._solve(inj)

    def check_conservation(self, inj_map, tol=1e-6):
        """Net flow out of the source buses must equal the injected power.

        Catches leaking-injection bugs, which yield plausible-looking numbers while losing
        most of the transfer.
        """
        ptdfs = self._solve(inj_map)
        sources = {b for b, p in inj_map.items() if p > 0}
        injected = sum(p for p in inj_map.values() if p > 0)
        net = 0.0
        for (b0, b1, b, s_nom, v_nom) in self.elim_branches:
            f = ptdfs[(b0, b1)]
            if b0 in sources:
                net += f
            if b1 in sources:
                net -= f
        return net, abs(net - injected) < tol


def compute_lpe_capacities(n, busmap, cluster_pairs, cluster_to_zone, rep_buses, dc=None,
                           verbose=True):
    """TTC for each (cluster0, cluster1) pair.

    Returns a DataFrame with zone_i, zone_j, F_mw, n_binding, peak_ptdf, binding_s_nom and
    binding_v_nom. The last two describe the branch that sets F_mw; it is used for
    Single_Line_MW when the GenX case is assembled.

    Only branches connecting two different clusters are considered. A branch inside a
    cluster can have a small PTDF for an unrelated transfer, and s_nom/|PTDF| for it is not
    a meaningful inter-zone limit.

    Injection and withdrawal are at each zone's peak-demand bus (peak_demand_buses()).
    """
    if dc is None:
        dc = DCNetwork(n, [rep_buses[cl] for cl in cluster_to_zone])
    peak_bus = peak_demand_buses(n, busmap, cluster_to_zone)

    # Precompute each branch's cluster membership once (not per-pair) for speed.
    branch_is_inter_zone = {}
    for (b0, b1, b, s_nom, v_nom) in dc.elim_branches:
        c0, c1 = busmap.get(b0), busmap.get(b1)
        branch_is_inter_zone[(b0, b1)] = (c0 is not None and c1 is not None and c0 != c1)

    rows = []
    n_intra_excluded_total = 0
    for (cl0, cl1) in cluster_pairs:
        ptdfs = dc.ptdf(peak_bus[cl0], peak_bus[cl1])
        if not ptdfs:
            continue
        peak = max(abs(v) for v in ptdfs.values())
        bound_records = [(abs(s_nom / ptdfs[(b0, b1)]), s_nom, v_nom)
                         for (b0, b1, b, s_nom, v_nom) in dc.elim_branches
                         if abs(ptdfs[(b0, b1)]) > 0 and branch_is_inter_zone[(b0, b1)]]
        n_intra_excluded_total += sum(
            1 for (b0, b1, b, s_nom, v_nom) in dc.elim_branches
            if abs(ptdfs.get((b0, b1), 0.0)) > 0 and not branch_is_inter_zone[(b0, b1)]
        )
        if not bound_records:
            continue
        f_mw, binding_s_nom, binding_v_nom = min(bound_records, key=lambda t: t[0])
        zp, zq = cluster_to_zone[cl0], cluster_to_zone[cl1]
        rows.append({
            "zone_i": min(zp, zq), "zone_j": max(zp, zq),
            "F_mw": f_mw, "n_binding": len(bound_records), "peak_ptdf": peak,
            "binding_s_nom": binding_s_nom, "binding_v_nom": binding_v_nom,
        })

    out = pd.DataFrame(rows).sort_values("F_mw", ascending=False).reset_index(drop=True)
    if verbose and not out.empty:
        print(f"  {len(out)} pairs: TTC min={out.F_mw.min():.0f} "
              f"median={out.F_mw.median():.0f} max={out.F_mw.max():.0f} MW")
        print(f"  (excluded {n_intra_excluded_total} intra-zone-branch bound candidates "
              f"across all pairs -- not real inter-zone constraints)")
    return out
