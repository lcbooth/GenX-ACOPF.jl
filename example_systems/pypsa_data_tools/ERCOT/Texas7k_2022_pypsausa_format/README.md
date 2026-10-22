# Convert the Texas7k network

The network input is TAMU's Texas7k synthetic ERCOT grid, 2022-09-23 version: 6717 buses,
at 69, 138 and 345 kV.

1. Download the case files `Texas7k_20220923.RAW` (PSS/E) and `Texas7k_20220923.AUX`
   (PowerWorld) from https://electricgrids.engr.tamu.edu.
2. From this directory, convert them:

   ```bash
   TEXAS7K_RAW_DIR="/path/to/raw-and-aux-files" python convert_texas7k_2022_to_pypsausa.py
   ```

   Or run the conversion cell in Step 0 of `../ERCOT_build.ipynb`.

This writes `buses.csv`, `bus2sub.csv`, `sub.csv`, `branches.csv` and an empty `links.csv`
into this folder, in the format of PyPSA-USA's `build_base_network.py`.
`../network/build_and_cluster_texas7k.py` reads them.

- Branch `r`, `x` and `b` are in per unit on a 100 MVA base.
- The converter exports network and bus-load data, excluding generators.
- Three-winding transformers are represented by their primary two-winding pair.
