# Network build: Texas7k → 20 zones

Builds the 20-zone transmission network written to `Network.csv`. Zones come from
clustering, line capacities from LPE, and line impedances from Kron reduction. The notebook
runs these scripts in order (Steps 1–5); paths are set in `paths.py`, and intermediate files
go to `../intermediates/`.

## Scripts

| Order | Script | Purpose |
|---|---|---|
| 1 | `build_and_cluster_texas7k.py` | Load the Texas7k CSVs into PyPSA and keep the ERCOT buses |
| 2 | `build_hifld_line_confirmation.py` | Clustering feature: share of each bus's lines that follow real HIFLD lines |
| 2 | `../scripts/build_bus_vre_features_for_hac.py` | Clustering feature: hourly wind and solar capacity factors per bus |
| 3 | `cluster_texas7k.py` | Cluster to 20 zones; write the bus → zone map |
| 3 | `verify_345_lines.py` | Remove inter-zone lines without a real 345 kV corridor |
| 4 | `lpe_all_lines.py` (with `lpe_core.py`) | Line capacity → `outputs/lpe_all_lines_capacity_20zone.csv` |
| 5 | `kron_reduce_sparse.py` | Line impedance → `outputs/kron_reduced_capacity_20zone.csv` |
| 5 | `apply_conductor_impedance.py` | Fallback impedance; writes the clustered network read in Step 11 |
| 11 | `correct_realN_impedance.py` | Store impedance per real circuit (see [line impedance](#line-impedance)) |

## Clustering

`cluster_texas7k.py` uses `busmap_by_hac` (Ward linkage), constrained to the network
adjacency so that every zone is connected. Two per-bus features are used:

- **Hourly wind and solar capacity factors** (`bus_vre_hac_feature.csv`, 8760 h × 2). This
  is the main clustering feature.
- **HIFLD confirmation** (`HIFLD_FEATURE_WEIGHT = 5.0`): the load-weighted share of a bus's
  ≥138 kV lines that lie along an in-service HIFLD line.

Other settings:

- **Zone order:** zones are ordered by descending Texas7k bus demand (`Pd`); zone 1 is the
  ACOPF reference zone. Every later script takes zone numbering from this order.
- **Voltage basis:** lines are rescaled to 345 kV before aggregation, as in PyPSA-USA's
  `convert_to_voltage_level()`.
- **Line length:** straight-line distance (`line_length_factor = 1.0`).
- **Islands:** the 9 buses in 4 small islands are dropped.

`verify_345_lines.py` then removes any inter-zone line with no real 345 kV corridor between
the two zones. A zone with no 345 kV connection keeps its lower-voltage tie, and a line is
only removed if the zones stay connected.

## Line capacity: LPE

`lpe_core.py` implements the line-limit-preserving equivalent of Jang, Mohapatra, Overbye &
Zhu (IEEE PECI 2013). For a unit transfer between two zones it computes every original
branch's DC PTDF and limits the transfer by the most constraining branch:
`TTC = min_k (s_nom_k / |PTDF_k|)`. This carries the real branch limits of Texas7k through
the clustering.

- **DC model:** lossless, with `B = 1/X`.
- **Injection:** at each zone's peak-demand bus.
- **Branches considered:** only those connecting two different zones. Branches inside a
  zone can have small PTDFs that do not represent an inter-zone limit.
- **Check:** power-flow conservation is asserted before capacities are used.
- **Limiting branch:** recorded for each zone pair (`binding_s_nom`, `binding_v_nom`) and
  used for `Single_Line_MW`.

The GenX columns are `Line_Max_Flow_MW = TTC`, `Single_Line_MW = s_nom × 345 / v_nom`
and `Line_Num_Parallel = Line_Max_Flow_MW / Single_Line_MW`, where `s_nom` and `v_nom`
belong to the limiting branch.

The ReEDS shapes are used only to decide which buses are in ERCOT, not for capacities.

## Line impedance

`kron_reduce_sparse.py` follows the border-bus-preserving Kron reduction of Ploussard,
Olmos & Ramos (*IEEE Trans. Power Systems* 33(6), 2018):

1. Eliminate interior buses using a Schur complement of the admittance matrix, retaining
   all 589 border buses with inter-zone branches.
2. Merge each zone's border buses: `Y_zone = M @ Y_reduced @ M.T`, where `M` maps buses
   to zones. Retaining border buses until this step preserves parallel paths.

Calculations use Texas7k's 100 MVA per-unit base across all voltage levels; results are
converted to ohms at 345 kV. The output is
`outputs/kron_reduced_capacity_20zone.csv`, with `zone_i`, `zone_j`, `r_eq_pu`, `x_eq_pu`,
`r_eq_ohm` and `x_eq_ohm`.

`validate_kron_borderbus.py` compares DC transfer angles against the full network with
each zone's border buses shorted.

### Impedance per circuit

GenX divides per-circuit impedance by the modeled number of parallel circuits. The Kron
impedance combines all real Texas7k branches in a corridor, so the stored values are:

```
Line_Resistance_Ohms = r_eq_ohm × N_real
Line_Reactance_Ohms  = x_eq_ohm × N_real
```

`N_real` is the count of original Texas7k branches between the zones. Step 11 initially scales by `Line_Num_Parallel`; `correct_realN_impedance.py` then
rescales by `N_real / Line_Num_Parallel` to obtain these per-circuit values.

### Fallback impedance

For zone pairs absent from the Kron output, `apply_conductor_impedance.py` uses typical
345 kV circuit parameters derived by `derive_conductor_spec.py`, multiplied by line length:
`r = 0.0353 Ω/km`, `x = 0.4195 Ω/km`, `b = 6.224e-06 S/km`. The fallback circuit rating is
1232 MVA.
