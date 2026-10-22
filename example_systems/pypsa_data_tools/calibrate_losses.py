"""Calibrate a uniform line-loss percentage for the transport and DCOPF models from an ACOPF run.

    python calibrate_losses.py <ac_results_dir> [util_quantile]

<ac_results_dir> is the output folder of an ACOPF run, e.g. results/ac from Run.jl. It must
contain acopf/line_records.csv and time_weights.csv. util_quantile (default 0.75) selects the
most heavily utilized lines: 0.75 keeps the top 25%.

For each line l, weighting each time step t by its time-domain-reduction weight w(t):

    util(l)     = Σ_t[w · max(|P_from|, |P_to|)] / (ApparentFlowCap · Σ_t[w])     mean utilization
    loss_pct(l) = Σ_t[w · |Loss|] / Σ_t[w · max(|P_from|, |P_to|)] · 100           flow-weighted loss

Time steps where a line carries less than 1 MW are ignored. The calibrated value is the
flow-weighted loss over the high-utilization lines, which carry most of the energy and
therefore set most of the losses. The script prints it alongside the system-wide figure.
Set the result as Line_Loss_Percentage (as a fraction) for every line in Network.csv. The
cases used in our work use 0.035 (Germany) and 0.025 (ERCOT), which the data-preparation notebooks write
directly.
"""

import sys
from pathlib import Path

import pandas as pd

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

ref_dir = Path(sys.argv[1])
util_q = float(sys.argv[2]) if len(sys.argv) >= 3 else 0.75

lr = pd.read_csv(ref_dir / "acopf" / "line_records.csv")
tw = pd.read_csv(ref_dir / "time_weights.csv", index_col=0)["Weight"]
lr["weight"] = lr["Time"].map(tw)
lr["max_flow"] = lr[["ActiveFlow_from", "ActiveFlow_to"]].abs().max(axis=1)
lr = lr[lr["max_flow"] > 1.0].copy()


def per_line(g):
    w = g["weight"]
    wsum = w.sum()
    flow_wtd = (g["max_flow"] * w).sum()
    return pd.Series({
        "util": (g["max_flow"] * w).sum() / (g["ApparentFlowCap"].iloc[0] * wsum),
        "loss_pct": (g["Loss"].abs() * w).sum() / flow_wtd * 100,
        "avg_flow": flow_wtd / wsum,
        "cap": g["ApparentFlowCap"].iloc[0],
    })


tbl = lr.groupby("Line").apply(per_line, include_groups=False)

# Flow-weighted loss over all lines
sys_all = (lr["Loss"].abs() * lr["weight"]).sum() / (lr["max_flow"] * lr["weight"]).sum() * 100

# Flow-weighted loss over the high-utilization lines
thresh = tbl["util"].quantile(util_q)
hi = tbl[tbl["util"] >= thresh]
hi_lines = lr[lr["Line"].isin(hi.index)]
sys_hi = (hi_lines["Loss"].abs() * hi_lines["weight"]).sum() / \
         (hi_lines["max_flow"] * hi_lines["weight"]).sum() * 100

pd.set_option("display.width", 120)
print(f"ACOPF results: {ref_dir}\n")
print("Per line (sorted by utilization):")
print((tbl.sort_values("util", ascending=False)
       .assign(util_pct=lambda d: (d["util"] * 100).round(1),
               loss_pct=lambda d: d["loss_pct"].round(3))
       [["util_pct", "loss_pct", "avg_flow", "cap"]]
       .round({"avg_flow": 0, "cap": 0})).to_string())

print(f"\nFlow-weighted loss, all lines:                   {sys_all:.3f}%")
print(f"Flow-weighted loss, top {int(round((1 - util_q) * 100))}% utilized lines "
      f"(util >= {thresh * 100:.1f}%, n={len(hi)}): {sys_hi:.3f}%   <- calibrated value")
