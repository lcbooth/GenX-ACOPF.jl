# Build the ERCOT 20-zone case

`ERCOT_build.ipynb` builds [`13_ERCOT_20_zone`](../../13_ERCOT_20_zone) from public data.
Input data is not included. All hourly demand and renewable profiles use 2022.

## Python environment

From the repository root:

```bash
python3.12 -m venv venv-pypsa
source venv-pypsa/bin/activate
pip install -r example_systems/pypsa_data_tools/requirements.txt
```

## Run the build

1. Activate the Python environment above.
2. Start Jupyter in this directory and open `ERCOT_build.ipynb`.
3. Follow Step 0 to download the inputs, convert Texas7k and check the required files.
4. Run the remaining cells in order:
   - Steps 1–5 build the network; see [network methods](network/README.md).
   - Steps 6–10 build demand, renewable profiles, generators, costs and fuel prices.
   - Step 11 writes `system/` and `resources/` in the case directory.
   - Step 12 builds the 5 GW per-zone thermal cap files.

The build takes several hours, mainly for ERA5 downloads and renewable profiles.
Paths are relative to this directory and defined in `network/paths.py`. Downloaded data
belongs in `source_data/`; generated files go to `intermediates/` and `outputs/`.
Only code, settings and documentation are version-controlled.

## Data sources

Step 0 lists download links, filenames and destination paths.

| Input | Source | Version |
|---|---|---|
| Network | TAMU Texas7k; see [conversion instructions](Texas7k_2022_pypsausa_format/README.md) | 2022-09-23 case |
| Metered demand | ERCOT native load by weather zone | `Native_Load_2022.xlsx`, published January 2023 |
| County demand | NLR, Obika, Cole & Rivers, *Scientific Data* (2026), [DOI](https://doi.org/10.1038/s41597-026-07693-3), [OpenEI data](https://data.openei.org/submissions/8562) | 2016–2023; prepare the Texas subset below |
| Weather | ERA5 via atlite and the Copernicus Climate Data Store | 2022 |
| Generators and costs | EIA-860 and NLR ATB tables via PUDL | Nightly build downloaded 2026-07-15 |
| Technology costs | [NLR ATB workbook](https://data.openei.org/submissions/5716) | 2022 v2 ("Corrected 7-21-2022") |
| Fuel prices | EIA Open Data API | 2022–2025 |
| Transmission routes | [HIFLD archive](https://www.arcgis.com/home/item.html?id=d4090758322c4d32a4cd002ffaa0aa12), export as GeoJSON | Downloaded 2026-07-20 |
| Geographic inputs | PyPSA-USA `repo_data`, including balancing-area shapes | Commit `f38d756` (2026-05-11) |
| Geographic inputs | [PyPSA-USA data bundle](https://zenodo.org/records/14219029): land cover, protected areas and counties | v15, as pinned by PyPSA-USA `f38d756` |

Extract `pypsa_usa_data.zip` so `copernicus/`, `natura.tiff` and `counties/`
are directly under `source_data/pypsa_usa_data/`.

The NLR download is the national `historic_load_hourly_2016_2023_county.h5`, not a
separate Texas file. Save it in `source_data/NREL_Nature_Demand/`, then create the
local Texas subset (county FIPS codes beginning with 48), from this directory:

```python
import pandas as pd
folder = "source_data/NREL_Nature_Demand/"
load = pd.read_hdf(folder + "historic_load_hourly_2016_2023_county.h5")
load.filter(regex=r"^p?48\d{3}$").to_hdf(
    folder + "historic_load_hourly_2016_2023_texas.h5", key="load", mode="w")
```

## Demand

ERCOT's metered load sets the magnitude and hourly profile for
each of its eight weather zones. NLR county annual-energy shares and Texas7k bus demand
(`Pd`) allocate that load to the 20 model zones:

```
load[t, z] = Σ_w metered_load[t, w] × allocation_share[w, z]
```

`scripts/build_demand_nrel_ercot_wz.py` builds the allocation in Step 6:

1. Assign each county to the weather zone with the largest areal overlap, requiring at
   least 5% overlap, using PyPSA-USA's `Modified_BE_BA_Shapes.shp`.
2. Split each county between model zones in proportion to its Texas7k buses' `Pd`.
   Counties with zero `Pd` are split evenly.
3. Weight counties by their NLR annual energy within each weather zone and normalize
   each weather zone's allocation shares to sum to one.
4. Apply the shares to ERCOT's hourly weather-zone load.

The script reports energy conservation, weather-zone energy, correlations and peak-hour
checks.

ERCOT reports hour-ending local time; NLR reports hour-ending UTC. Both are converted to
hour-beginning ERCOT local time by `scripts/timezones.py`. The demand output includes
`local_time`, which the renewable-profile build uses to align timestamps. Step 11 removes
that column, adds GenX demand settings and writes `system/Demand_data.csv`. Demand uses
`WEATHER_YEAR` in `scripts/era5_config.py` (2022); zone numbers follow the network bus order.

### Rebuild demand

Run the notebook from Step 6 onward. To run only the demand builder after the network
steps, from this directory:

```bash
python scripts/build_demand_nrel_ercot_wz.py
```

This writes `outputs/ercot_demand_20zone_nrel_ercot_wz.csv`; Step 11 is still needed to
export it to GenX. There is one demand build, with no source-selection setting.
When replacing demand in an existing case, delete its `TDR_results/` directory before
running GenX so it reclusters the new time series.

## Other build scripts

Run these through the notebook to preserve step order and working directories. Paths
are relative to this directory.

| Build | Scripts |
|---|---|
| Weather and bus regions | `scripts/build_era5_cutout.py`, `scripts/build_bus_regions.py` |
| Renewable profiles and potentials | `scripts/build_vre_profiles.py` |
| Generators and bus mapping | `scripts/build_generators.py`, `scripts/map_generators_to_buses.py` |
| Technology costs and fuel prices | `scripts/build_tech_costs.py`, `source_data/EIA/build_fuel_prices.py` |
| Thermal capacity caps | `../../13_ERCOT_20_zone/build_thermal_zonal_cap.py` |

The notebook's Step 11 assembles these outputs into GenX CSVs; its cells document the
column mappings and conversions. For running GenX after the build, see the
[case README](../../13_ERCOT_20_zone/README.md).

## Loss calibration

The conversion writes `Line_Loss_Percentage: 0.025`. To calculate loss percentages from
an ACOPF results directory, run from the repository root:

```bash
python example_systems/pypsa_data_tools/calibrate_losses.py example_systems/13_ERCOT_20_zone/results/ac
```
