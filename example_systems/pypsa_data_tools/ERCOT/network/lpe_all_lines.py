"""LPE transfer capacity for every inter-zone line.

    python lpe_all_lines.py

Writes outputs/lpe_all_lines_capacity_20zone.csv, used for Line_Max_Flow_MW and
Single_Line_MW in the notebook's assembly step. See lpe_core.py for the method.
"""
import pandas as pd
import pypsa

from paths import (
    RAW_NETWORK_NC, BUSES_WITH_REEDS_ZONE_CSV, CLUSTERED_NC, BUSMAP_CSV, OUTPUTS, require,
)
from lpe_core import (
    load_ercot_connected_network, representative_buses, load_weights, DCNetwork,
    compute_lpe_capacities,
)

OUT_CSV = OUTPUTS / "lpe_all_lines_capacity_20zone.csv"

# Kassel, Rhodes & Webber (Applied Energy 378 (2025) 124732), Fig. 2 -- real published
# ERCOT-internal corridor capacities, as an external sanity reference.
WEBBER_MIN_GW, WEBBER_MAX_GW, WEBBER_MEDIAN_GW = 0.1, 4.2, 0.9

n = load_ercot_connected_network(require(RAW_NETWORK_NC, "raw network"),
                                 require(BUSES_WITH_REEDS_ZONE_CSV, "bus->ReEDS zone map"))
print(f"ERCOT network: {len(n.buses)} buses, {len(n.lines)} lines, {len(n.transformers)} transformers")

busmap = pd.read_csv(require(BUSMAP_CSV, "busmap"), index_col=0)["cluster"]
busmap.index = busmap.index.astype(str)
busmap = busmap.astype(str)

n_clustered = pypsa.Network(str(require(CLUSTERED_NC, "clustered network")))
# Zone numbering follows the clustered network's bus order, as in every other step.
real_zone_order = n_clustered.buses.index.tolist()
cluster_to_zone = {cl: i + 1 for i, cl in enumerate(real_zone_order)}

rep_buses = representative_buses(n, busmap)
dc = DCNetwork(n, [rep_buses[cl] for cl in real_zone_order])

# Conservation must hold before any capacity is trusted.
weights = load_weights(n, busmap, cluster_to_zone)
cl0, cl1 = real_zone_order[0], real_zone_order[1]
inj = {b: w / sum(weights[cl0].values()) for b, w in weights[cl0].items()}
for b, w in weights[cl1].items():
    inj[b] = inj.get(b, 0.0) - w / sum(weights[cl1].values())
net, ok = dc.check_conservation(inj)
print(f"Conservation check: net={net:.9f} ({'OK' if ok else 'FAIL'})")
assert ok, "PTDF conservation failed -- capacities would be meaningless"

pairs = [(str(row.bus0), str(row.bus1)) for _, row in n_clustered.lines.iterrows()]

print(f"\nComputing LPE for all {len(pairs)} clustered lines...")
cap = compute_lpe_capacities(n, busmap, pairs, cluster_to_zone, rep_buses, dc=dc)
cap.to_csv(OUT_CSV, index=False)
print(f"Saved -> {OUT_CSV}")

print(f"\nTotal network capacity: {cap.F_mw.sum():,.0f} MW across {len(cap)} lines")

# Order-of-magnitude reference: Kassel, Rhodes & Webber (Applied Energy 378, 2025, Fig. 2).
# Their lines cover a 16-zone ERCOT and a 4-zone WECC model, and their capacities are
# 0.76 x the maximum simulated AC flow rather than a thermal limit, so this is only a rough
# bound.
print("\n=== vs Kassel, Rhodes & Webber (Applied Energy 378 (2025) 124732), Fig. 2 (loose ref only) ===")
print(f"  Webber: min {WEBBER_MIN_GW:.2f} / median {WEBBER_MEDIAN_GW:.2f} / max {WEBBER_MAX_GW:.2f} GW")
print(f"  LPE:    min {cap.F_mw.min()/1000:.2f} / median {cap.F_mw.median()/1000:.2f} / "
      f"max {cap.F_mw.max()/1000:.2f} GW")
if cap.F_mw.max() / 1000 > WEBBER_MAX_GW:
    print("  NOTE: largest line exceeds Webber's largest reported corridor (different "
          "methodology/network resolution -- not necessarily a problem, see comment above).")
