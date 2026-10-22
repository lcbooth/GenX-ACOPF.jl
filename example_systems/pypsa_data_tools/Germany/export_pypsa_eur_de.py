"""Export a clustered PyPSA-Eur network to the CSVs read by DE_Pypsa-GenX-conversion.ipynb.

    python export_pypsa_eur_de.py <base_s_20_elec.nc> [n_zones]

The input is PyPSA-Eur's clustered electricity network, resources/networks/base_s_<n>_elec.nc,
built with PyPSA-Eur v2025.04.0 and config.de20.yaml (Germany, 20 clusters, weather year
2023, hourly). Writes
DE_<n>_zone_pypsa/DE_<n>_{buses,lines,links,loads,loads_t,generators,generators_t,storage}.csv
next to this script.
"""
import sys
from pathlib import Path

import pypsa


def main():
    nc = sys.argv[1]
    n_zones = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    out = Path(__file__).resolve().parent / f"DE_{n_zones}_zone_pypsa"
    out.mkdir(exist_ok=True)
    prefix = out / f"DE_{n_zones}"

    n = pypsa.Network(nc)
    static = {
        "buses": n.buses,
        "lines": n.lines,
        "links": n.links,
        "loads": n.loads,
        "generators": n.generators,
        "storage": n.storage_units,
    }
    for name, df in static.items():
        df.to_csv(f"{prefix}_{name}.csv")
    n.loads_t.p_set.to_csv(f"{prefix}_loads_t.csv")
    n.generators_t.p_max_pu.to_csv(f"{prefix}_generators_t.csv")
    print(f"Wrote {len(static) + 2} files to {out}")


if __name__ == "__main__":
    main()
