"""Store each line's impedance per real circuit.

    python correct_realN_impedance.py <case_dir>   # e.g. ../../../13_ERCOT_20_zone

The notebook's assembly step writes Line_Resistance_Ohms = r_eq_ohm * Line_Num_Parallel
(likewise reactance), where r_eq_ohm is the Kron-reduced impedance of all real branches
between two zones (kron_reduce_sparse.py) and Line_Num_Parallel = Line_Max_Flow_MW /
Single_Line_MW is a capacity-based circuit count. This script multiplies the stored values
by N_real / Line_Num_Parallel, where N_real is the number of real Texas7k branches between
the two zones, so the stored impedance is that of one average real branch. N_real is
counted from branches.csv and the bus -> zone map, with zones numbered in the clustered
network's bus order. See README.md, line impedance section.

As a check, the resulting impedances per km are within about 5% of the German network's.

Overwrites <case_dir>/system/Network.csv.
"""
import sys
from pathlib import Path

import pandas as pd
import pypsa

sys.path.insert(0, str(Path(__file__).parent))
from paths import CLUSTERED_NC, TEXAS7K_CSV_DIR, INTERMEDIATES, require


def real_branch_counts_per_zone_pair():
    n = pypsa.Network(str(require(CLUSTERED_NC, "clustered network")))
    real_zone_order = n.buses.index.tolist()
    zone_number = {cl: i + 1 for i, cl in enumerate(real_zone_order)}

    busmap = pd.read_csv(require(INTERMEDIATES / "texas7k_busmap_20zone.csv", "busmap"))
    bus_to_zone = {bus: zone_number[str(cl)] for bus, cl in zip(busmap.Bus, busmap.cluster)}

    branches = pd.read_csv(require(TEXAS7K_CSV_DIR / "branches.csv", "Texas7k branches"))
    branches["z_from"] = branches.from_bus_id.map(bus_to_zone)
    branches["z_to"] = branches.to_bus_id.map(bus_to_zone)
    interzone = branches.dropna(subset=["z_from", "z_to"]).copy()
    interzone = interzone[interzone.z_from != interzone.z_to]
    interzone["pair"] = interzone.apply(
        lambda r: frozenset((int(r.z_from), int(r.z_to))), axis=1
    )
    return interzone.groupby("pair").size()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    case_dir = Path(sys.argv[1])
    net_path = case_dir / "system" / "Network.csv"
    net = pd.read_csv(require(net_path, "Network.csv"))

    is_line = net.Line_Max_Flow_MW.notna()
    lines = net[is_line].copy()
    lines["pair"] = lines.apply(
        lambda r: frozenset((int(r.Start_Zone), int(r.End_Zone))), axis=1
    )

    real_n = real_branch_counts_per_zone_pair()
    lines["real_N"] = lines.pair.map(real_n)
    missing = lines.real_N.isna()
    if missing.any():
        raise RuntimeError(
            f"{missing.sum()} line(s) have no real branch count match "
            f"(e.g. {lines.loc[missing, 'transmission_path_name'].tolist()[:3]}) -- "
            f"check zone numbering against kron_reduce_sparse.py's real_zone_order."
        )

    scale = lines.real_N / lines.Line_Num_Parallel
    print(f"scale factor (real_N / Line_Num_Parallel): median={scale.median():.2f} "
          f"mean={scale.mean():.2f} min={scale.min():.2f} max={scale.max():.2f}")

    net.loc[is_line, "Line_Resistance_Ohms"] = (net.loc[is_line, "Line_Resistance_Ohms"] * scale).values
    net.loc[is_line, "Line_Reactance_Ohms"] = (net.loc[is_line, "Line_Reactance_Ohms"] * scale).values

    km_per_mile = 1.609344
    length_km = lines.distance_mile * km_per_mile
    r_per_km = net.loc[is_line, "Line_Resistance_Ohms"] / length_km
    x_per_km = net.loc[is_line, "Line_Reactance_Ohms"] / length_km
    print(f"corrected r_per_km: median={r_per_km.median():.5f} mean={r_per_km.mean():.5f}")
    print(f"corrected x_per_km: median={x_per_km.median():.5f} mean={x_per_km.mean():.5f}")

    net.to_csv(net_path, index=False)
    print(f"wrote {net_path}")


if __name__ == "__main__":
    main()
