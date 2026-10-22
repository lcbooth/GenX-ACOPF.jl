# Case 12: Germany, 20 zones

A 20-zone model of Germany derived from PyPSA-Eur. It compares three representations of
the transmission network in capacity expansion: transport, DCOPF and ACOPF.

## Inputs

Input data is not included. Build `system/` and `resources/` by following
[Germany build guide](../pypsa_data_tools/Germany/README.md).

## Running

From the repository root:

```bash
julia --project=. example_systems/12_PyPSA_DE_20_zone/Run.jl            # transport, dc and ac
julia --project=. example_systems/12_PyPSA_DE_20_zone/Run.jl ac         # or any subset
```

`Run.jl` solves the configuration in `settings/genx_settings.yml` with each transmission
model and writes the results to `results/<model>/`. ACOPF runs also write `acopf/`, with
voltages and active and reactive flows by line and zone.

**Settings**

- Network expansion is on.
- 20 representative weeks, read from `period_map.csv` (see below).
- Minimum power factor 0.85 for thermal plants and 0.95 for inverters; demand power
  factor 0.9; shunt fraction 1.0.
- Line losses for transport and DCOPF: a uniform 3.5%, calibrated from ACOPF.

**Solvers**

- Transport is a linear program, solved with HiGHS. Reference runs used Gurobi (barrier with
  crossover). To use Gurobi (licence required), install it in Julia's default environment
  with `julia -e 'using Pkg; Pkg.add("Gurobi")'` and pass `Gurobi.Optimizer` in `Run.jl`.
- DCOPF with network expansion and ACOPF are both nonlinear and nonconvex. Both are solved with Ipopt.
- Ipopt uses the HSL MA86 linear solver when `HSL_jll` is installed (see "Using this fork"
  in the main README), otherwise MUMPS, which is slower.

**Compute.** With HSL and 32 CPU threads (48 GB of memory), the ACOPF run took about 5.5
hours; DCOPF with network expansion takes roughly half as long, and transport a few minutes.
`OMP_NUM_THREADS` sets the number of threads MA86 uses.

**Representative weeks.** The time series are reduced to 20 representative weeks by k-means
clustering. k-means results can differ between machines even with a fixed seed, so the
case reads the saved assignment of weeks from `period_map.csv` (`PeriodMap` in
`settings/time_domain_reduction_settings.yml`). To cluster afresh instead, delete the
`PeriodMap` line; clustering then uses `Seed: 1234`.
