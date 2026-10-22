# Build the Germany 20-zone case

`DE_Pypsa-GenX-conversion.ipynb` converts a clustered PyPSA-Eur network into inputs for
[`12_PyPSA_DE_20_zone`](../../12_PyPSA_DE_20_zone). Source data is downloaded by
PyPSA-Eur; generated data is not included in this repository.

## Python environment

From the repository root:

```bash
python3.12 -m venv venv-pypsa
source venv-pypsa/bin/activate
pip install -r example_systems/pypsa_data_tools/requirements.txt
```

PyPSA-Eur uses a separate environment for the network build below.

## Build and export

Commands use the repository root unless a step changes directories. Paths beginning
with `Germany/` below are relative to `example_systems/pypsa_data_tools/`.

1. **Build the PyPSA-Eur network** with [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur)
   **v2025.04.0** (PyPSA 0.34.1). PyPSA-Eur has its own environment and downloads its own
   data, including the `europe-2023-sarah3-era5` weather cutout; see its
   [installation guide](https://pypsa-eur.readthedocs.io).

   ```bash
   git clone https://github.com/PyPSA/pypsa-eur.git && cd pypsa-eur
   git checkout v2025.04.0
   # create and activate the PyPSA-Eur environment as described in its documentation, then:
   cp <path to Germany/>/config.de20.yaml config/config.de20.yaml
   snakemake --configfile config/config.de20.yaml -call resources/test-elec-de-20/networks/base_s_20_elec.nc
   ```

   The config covers Germany only, with 20 clusters, the OSM prebuilt base network v0.6,
   the 2023 weather year, hourly resolution, and renewable capacities estimated from GEM.
   Because the run is named `test-elec-de-20`, outputs go to `resources/test-elec-de-20/`.
   [`config.de20.resolved.yaml`](config.de20.resolved.yaml) is the complete
   configuration of the original run, including all defaults, as recorded in the network
   file.
2. **Export it to CSVs** in `Germany/DE_20_zone_pypsa/`:

   ```bash
   python example_systems/pypsa_data_tools/Germany/export_pypsa_eur_de.py <path>/base_s_20_elec.nc
   ```

3. **Convert to GenX.** Run `Germany/DE_Pypsa-GenX-conversion.ipynb` with `Germany/` as the
   working directory. It writes `Germany/DE_20_zone_GenX/`, including the ACOPF columns
   (line impedances, charging susceptance, voltage limits, parallel circuits). Cost and
   price assumptions and their sources are documented in the notebook.
4. **Copy the files into the case:**

   ```
   Germany/DE_20_zone_GenX/{Network,Demand_data,Fuels_data,Generators_variability}.csv -> 12_PyPSA_DE_20_zone/system/
   Germany/DE_20_zone_GenX/{Thermal,Vre,Storage}.csv                                   -> 12_PyPSA_DE_20_zone/resources/
   ```

## Loss calibration

The conversion writes `Line_Loss_Percentage: 0.035`. To calculate loss percentages from
an ACOPF results directory, run from the repository root:

```bash
python example_systems/pypsa_data_tools/calibrate_losses.py example_systems/12_PyPSA_DE_20_zone/results/ac
```
