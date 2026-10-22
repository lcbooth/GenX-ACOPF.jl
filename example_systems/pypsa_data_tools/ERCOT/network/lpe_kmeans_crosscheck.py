"""Optional check: line capacities with a k-means clustering instead of HAC.

    python lpe_kmeans_crosscheck.py

Writes outputs/lpe_all_lines_capacity_20zone_kmeans.csv.

LPE capacities should depend on the Texas7k network, not on the clustering method. This
script clusters the buses with k-means (by position and weight only, ignoring the network)
and recomputes LPE capacities. The two clusterings give different zones, so the comparison
is of the capacity distributions, not line by line: median 1801 MW (HAC) against 1858 MW
(k-means). The number of lines depends on which zones end up adjacent and is not a
meaningful comparison.

The pipeline uses HAC (cluster_texas7k.py), which keeps zones connected.
"""
import numpy as np
import pandas as pd
import pypsa
from pypsa.clustering.spatial import busmap_by_kmeans, get_clustering_from_busmap

from paths import (
    RAW_NETWORK_NC, BUSES_WITH_REEDS_ZONE_CSV, CLUSTERED_NC, BUSMAP_CSV, OUTPUTS,
    INTERMEDIATES, LPE_ALL_LINES_CSV, require,
)
from lpe_core import (
    load_ercot_connected_network, representative_buses, load_weights, DCNetwork,
    compute_lpe_capacities,
)

N_CLUSTERS = 20
OUT_CSV = OUTPUTS / "lpe_all_lines_capacity_20zone_kmeans.csv"
KMEANS_BUSMAP_CSV = INTERMEDIATES / "texas7k_busmap_20zone_kmeans.csv"
KMEANS_CLUSTERED_NC = INTERMEDIATES / "texas7k_clustered_20_kmeans.nc"

n = load_ercot_connected_network(require(RAW_NETWORK_NC, "raw network"),
                                 require(BUSES_WITH_REEDS_ZONE_CSV, "bus->ReEDS zone map"))
print(f"ERCOT network: {len(n.buses)} buses, {len(n.lines)} lines, {len(n.transformers)} transformers")

# ---------------------------------------------------------------------------
# k-means clustering. Same 345kV rescale + line_length_factor as cluster_texas7k.py, so
# the ONLY difference vs the HAC run is the partitioning algorithm itself.
# ---------------------------------------------------------------------------
cluster_reeds_zone_raw = n.buses["reeds_zone"].copy()
nk = n.copy()

NEW_VOLTAGE = 345.0
base_z = nk.lines["v_nom"] ** 2 / nk.lines["s_nom"]
r_pu, x_pu, b_pu = nk.lines["r"] / base_z, nk.lines["x"] / base_z, nk.lines["b"] * base_z
new_base_z = NEW_VOLTAGE**2 / nk.lines["s_nom"]
nk.lines["r"], nk.lines["x"] = r_pu * new_base_z, x_pu * new_base_z
nk.lines["b"] = b_pu / new_base_z
nk.lines["v_nom"] = NEW_VOLTAGE
nk.buses["v_nom"] = NEW_VOLTAGE
nk.lines = nk.lines.drop(columns=["v_nom"])
nk.buses = nk.buses.drop(columns=["reeds_zone"])

# Pd weighting, matching cluster_texas7k.py's bus_strategies={"Pd": "sum"} intent.
weight = nk.buses["Pd"].clip(lower=0.0)
weight = (weight / weight.sum() * len(weight)).round().clip(lower=1).astype(int)
print(f"\nk-means clustering to {N_CLUSTERS} zones (Pd-weighted)...")
busmap_k = busmap_by_kmeans(nk, bus_weightings=weight, n_clusters=N_CLUSTERS, random_state=0)
print(f"busmap: {busmap_k.nunique()} clusters, sizes {sorted(busmap_k.value_counts().tolist())}")

clustering_k = get_clustering_from_busmap(
    nk, busmap_k, line_length_factor=1.0, bus_strategies={"Pd": "sum"},
    aggregate_generators_weighted=False,
)
ck = clustering_k.network
# Same convention as cluster_texas7k.py: highest-Pd cluster becomes zone 1.
ck.buses = ck.buses.sort_values("Pd", ascending=False)
ck.lines["bus0"] = ck.lines["bus0"].astype(str)
ck.lines["bus1"] = ck.lines["bus1"].astype(str)
ck.export_to_netcdf(str(KMEANS_CLUSTERED_NC))
busmap_k.to_csv(KMEANS_BUSMAP_CSV, header=["cluster"])
print(f"k-means clustered network: {len(ck.buses)} buses, {len(ck.lines)} lines")

# ---------------------------------------------------------------------------
# LPE on the k-means busmap -- identical method, different partition.
# ---------------------------------------------------------------------------
busmap_k = busmap_k.astype(str)
order_k = ck.buses.index.tolist()
c2z_k = {cl: i + 1 for i, cl in enumerate(order_k)}
reps_k = representative_buses(n, busmap_k)
dc_k = DCNetwork(n, [reps_k[cl] for cl in order_k])

w = load_weights(n, busmap_k, c2z_k)
a, b = order_k[0], order_k[1]
inj = {k: v / sum(w[a].values()) for k, v in w[a].items()}
for k, v in w[b].items():
    inj[k] = inj.get(k, 0.0) - v / sum(w[b].values())
net, ok = dc_k.check_conservation(inj)
print(f"Conservation check: net={net:.9f} ({'OK' if ok else 'FAIL'})")
assert ok, "PTDF conservation failed"

pairs_k = [(str(r.bus0), str(r.bus1)) for _, r in ck.lines.iterrows()]
print(f"\nComputing LPE for {len(pairs_k)} k-means lines...")
cap_k = compute_lpe_capacities(n, busmap_k, pairs_k, c2z_k, reps_k, dc=dc_k)
cap_k.to_csv(OUT_CSV, index=False)
print(f"Saved -> {OUT_CSV}")

# ---------------------------------------------------------------------------
# Compare the two distributions.
# ---------------------------------------------------------------------------
cap_h = pd.read_csv(require(LPE_ALL_LINES_CSV, "HAC LPE capacities"))
print("\n=== LPE capacity distribution: HAC vs k-means (MW) ===")
summary = pd.DataFrame({
    "HAC": cap_h.F_mw.describe(percentiles=[0.25, 0.5, 0.75]),
    "kmeans": cap_k.F_mw.describe(percentiles=[0.25, 0.5, 0.75]),
}).round(0)
print(summary)
print(f"\nTotal capacity:  HAC {cap_h.F_mw.sum():,.0f} MW   "
      f"k-means {cap_k.F_mw.sum():,.0f} MW   "
      f"(ratio {cap_k.F_mw.sum()/cap_h.F_mw.sum():.2f}x)")
print(f"Lines:           HAC {len(cap_h)}   k-means {len(cap_k)}")
print(f"\nWebber Fig. 2 real reference: min 100 / median 900 / max 4200 MW")
print(f"  HAC:     min {cap_h.F_mw.min():.0f} / median {cap_h.F_mw.median():.0f} / max {cap_h.F_mw.max():.0f} MW")
print(f"  k-means: min {cap_k.F_mw.min():.0f} / median {cap_k.F_mw.median():.0f} / max {cap_k.F_mw.max():.0f} MW")
