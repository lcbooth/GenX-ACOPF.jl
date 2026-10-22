# Case 13: ERCOT, 20 zones

A 20-zone model of the ERCOT (Texas) system, built from public data: the TAMU Texas7k
synthetic network, ERCOT metered load, ERA5 weather, PUDL/EIA generators and NLR ATB
costs.

## Inputs

Input data is not included. Build it with the notebook in
[`../pypsa_data_tools/ERCOT`](../pypsa_data_tools/ERCOT/README.md), which
writes `system/`, `resources/` and the 5 GW per-zone thermal cap folders.

ERCOT's metered weather-zone load is allocated using NLR
county shares and Texas7k bus demand. The notebook builds only this demand and writes
`system/Demand_data.csv` directly, with no demand selection or switching step. See
[demand documentation](../pypsa_data_tools/ERCOT/README.md#demand) for the method and rebuild instructions.

`build_thermal_zonal_cap.py` builds thermal caps other than 5 GW.

## Running

From the repository root:

```bash
julia --project=. example_systems/13_ERCOT_20_zone/Run.jl            # transport, dc and ac
julia --project=. example_systems/13_ERCOT_20_zone/Run.jl ac         # or any subset
```

Settings and solvers are as in [case 12](../12_PyPSA_DE_20_zone/README.md#running), with
these differences:

- **32 representative weeks**, read from
  `period_map_n32.csv`. Sensitivity configurations (thermal caps, fixed
  network, reactive power) use 20 weeks: set `MinPeriods` and `MaxPeriods` to 20 and
  `PeriodMap: period_map_n20.csv` in `settings/time_domain_reduction_settings.yml`,
  and delete `TDR_results/` so the time series are rebuilt.
- Total thermal capacity in each zone is capped at 5 GW.
- Line losses for transport and DCOPF are a uniform 2.5%, calibrated from ACOPF.

**Compute.** The 32-week ACOPF run took about 10 hours with 32 CPU threads and
96 GB of memory; with 20 weeks it takes about 6.5 hours on 48 GB memory.
