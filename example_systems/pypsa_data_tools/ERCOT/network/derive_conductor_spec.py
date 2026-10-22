"""Derive the typical Texas7k 345 kV circuit used by apply_conductor_impedance.py.

    python derive_conductor_spec.py

Prints r, x and b per km and the median circuit rating; the values are stored as constants
in paths.py. Nothing is written.

Texas7k has no line lengths, so lengths are haversine distances between bus coordinates.
Lines shorter than 5 km (bus couplers and substation jumpers) are excluded.
"""
import numpy as np

from paths import RAW_NETWORK_NC, BUSES_WITH_REEDS_ZONE_CSV, require
from lpe_core import load_ercot_connected_network

V_NOM = 345.0
MIN_KM = 5.0
EARTH_R_KM = 6371.0


def haversine_km(n, bus0, bus1):
    la1 = np.radians(n.buses.loc[bus0, "y"].values)
    lo1 = np.radians(n.buses.loc[bus0, "x"].values)
    la2 = np.radians(n.buses.loc[bus1, "y"].values)
    lo2 = np.radians(n.buses.loc[bus1, "x"].values)
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def main():
    n = load_ercot_connected_network(require(RAW_NETWORK_NC, "raw network"),
                                     require(BUSES_WITH_REEDS_ZONE_CSV, "bus->ReEDS zone map"))
    lines = n.lines.copy()
    lines["v_nom_bus"] = lines.bus0.map(n.buses.v_nom)
    d = lines[lines.v_nom_bus == V_NOM].copy()
    d["km"] = haversine_km(n, d.bus0, d.bus1)
    ok = d[d.km >= MIN_KM].copy()
    for c in ("r", "x", "b"):
        ok[f"{c}_km"] = ok[c] / ok.km

    print(f"Texas7k {V_NOM:.0f} kV lines: {len(d)} total, {len(ok)} at >= {MIN_KM:.0f} km\n")
    print(f"  R_OHM_PER_KM = {ok.r_km.median():.4f}   (IQR {ok.r_km.quantile(.25):.4f}-{ok.r_km.quantile(.75):.4f})")
    print(f"  X_OHM_PER_KM = {ok.x_km.median():.4f}   (IQR {ok.x_km.quantile(.25):.4f}-{ok.x_km.quantile(.75):.4f})")
    print(f"  B_S_PER_KM   = {ok.b_km.median():.3e}   (IQR {ok.b_km.quantile(.25):.3e}-{ok.b_km.quantile(.75):.3e})")
    print(f"  SINGLE_CIRCUIT_MVA = {ok.s_nom.median():.0f}   (IQR {ok.s_nom.quantile(.25):.0f}-{ok.s_nom.quantile(.75):.0f})")
    print(f"\n  X/R = {ok.x_km.median() / ok.r_km.median():.2f}")
    print("\nFor comparison, pandapower 679-AL1/86-ST1A 380.0: r=0.0420 x=0.2500 "
          "b=5.50e-06, X/R=5.95, 687 MVA")


if __name__ == "__main__":
    main()
