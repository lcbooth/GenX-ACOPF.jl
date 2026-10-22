"""Assign each clustered line the impedance of a typical Texas7k 345 kV circuit.

    python apply_conductor_impedance.py

Reads intermediates/texas7k_clustered_20.nc and writes
intermediates/texas7k_clustered_20_realimpedance.nc.

Each line gets r, x and b per km of a typical 345 kV circuit (constants in paths.py, derived
by derive_conductor_spec.py from the 354 Texas7k 345 kV corridors) times its length,
replacing the series-summed impedance produced by clustering. Values are per circuit;
GenX divides by the number of parallel circuits itself.

The GenX case uses Kron-reduced impedances instead (kron_reduce_sparse.py); these values
are only a fallback for zone pairs missing from the Kron reduction. The notebook reads this
network for zone order and voltage limits. Line capacity (s_nom) is not changed.
"""
import pandas as pd
import numpy as np
import pypsa
import math

from paths import (
    CLUSTERED_NC, CLUSTERED_REALIMPEDANCE_NC, LPE_ALL_LINES_CSV, require,
    V_NOM_KV, R_OHM_PER_KM, X_OHM_PER_KM, B_S_PER_KM, SINGLE_CIRCUIT_MVA,
)

n = pypsa.Network(str(require(CLUSTERED_NC, "clustered network")))

V_NOM = V_NOM_KV
single_circuit_mva = SINGLE_CIRCUIT_MVA
print(f"One 345 kV circuit (real Texas7k median): {single_circuit_mva:.1f} MVA")

# num_parallel is a CONTINUOUS aggregation factor -- how many circuits' worth of transfer
# capability this corridor represents -- not a count of wires. Transmission expansion is
# continuous here (no MILP), so GenX treats it as a smooth scaling factor:
# vNumParallel = eAvail_Trans_Cap / Single_Line_MW, and admittance scales with it. Values
# below 1 are meaningful: LPE finds several corridors transfer less than one circuit's
# worth, since stability and contingency limits bind well below thermal.
#
# It is derived from LPE capacity, not from the merged circuit count. Counting wires answers
# "how many conductors exist"; GenX needs "how much capacity, in units of one circuit". Those
# differ because Texas7k's merged circuits are a 69/138/345 kV mix, not 40 identical 345 kV
# lines -- the count (396 total, median 5.5, max 40) drives impedance ~4x below the DE
# 20-zone reference network's, while this gives 0.77-3.86 against DE's 1.3-7.0.
lpe = pd.read_csv(require(LPE_ALL_LINES_CSV, "LPE line capacities"))
lpe_cap = {frozenset((int(r.zone_i), int(r.zone_j))): r.F_mw for r in lpe.itertuples()}
bus_to_zone = {b: i + 1 for i, b in enumerate(n.buses.index)}
keys = [frozenset((bus_to_zone[a], bus_to_zone[b])) for a, b in zip(n.lines.bus0, n.lines.bus1)]
missing = [k for k in keys if k not in lpe_cap]
if missing:
    raise RuntimeError(f"{len(missing)} line(s) have no LPE capacity -- re-run lpe_all_lines.py")
n.lines["num_parallel"] = [lpe_cap[k] / single_circuit_mva for k in keys]
print(f"num_parallel (LPE capacity / one circuit, continuous): "
      f"min {n.lines['num_parallel'].min():.2f}, median {n.lines['num_parallel'].median():.2f}, "
      f"max {n.lines['num_parallel'].max():.2f}")

# PER-CIRCUIT impedance -- do NOT divide by num_parallel here. GenX scales it itself:
# acopf_transmission.jl builds Ys = 1/(r+jx) from these values and then forms
# Yr = vNumParallel * Yr_base, where vNumParallel = eAvail_Trans_Cap / Single_Line_MW.
# Dividing here as well applies the aggregation twice (x_single / num_parallel^2).
# The DE 20-zone reference network stores per-circuit impedance for the same reason.
n.lines["r"] = R_OHM_PER_KM * n.lines["length"]
n.lines["x"] = X_OHM_PER_KM * n.lines["length"]
n.lines["b"] = B_S_PER_KM * n.lines["length"]
n.lines["v_nom"] = V_NOM  # re-add for output clarity, PyPSA itself doesn't require it on Line

print(f"\n{len(n.lines)} lines updated with conductor-based impedance")
print(n.lines[["bus0", "bus1", "length", "s_nom", "num_parallel", "r", "x", "b"]])

n.export_to_netcdf(str(CLUSTERED_REALIMPEDANCE_NC))
print(f"\nSaved -> {CLUSTERED_REALIMPEDANCE_NC}")
