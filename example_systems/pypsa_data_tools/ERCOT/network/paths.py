"""Paths and shared constants for the ERCOT build.

All paths are relative to the ERCOT folder, so the tree can be moved freely.
Environment variables:
  ERCOT20_CASE_BASELINE   GenX case folder to write (default ../../13_ERCOT_20_zone)
  ERCOT20_INTERMEDIATES   folder for intermediate files (default intermediates/)
  PYPSA_USA_REPO_DATA     PyPSA-USA repo_data folder (default
                          source_data/pypsa_usa_repo_data/workflow/repo_data)
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent   # ERCOT/network
ERCOT_REBUILD = HERE.parent              # ERCOT

REPO_ROOT = ERCOT_REBUILD.parents[2]
EXAMPLE_SYSTEMS = REPO_ROOT / "example_systems"
SCRIPTS = ERCOT_REBUILD / "scripts"
SOURCE_DATA = ERCOT_REBUILD / "source_data"
EIA_DIR = SOURCE_DATA / "EIA"
PYPSA_USA_DATA = SOURCE_DATA / "pypsa_usa_data"

# GenX case written by the notebook
CASE_BASELINE = Path(
    os.environ.get("ERCOT20_CASE_BASELINE", EXAMPLE_SYSTEMS / "13_ERCOT_20_zone")
)

# Typical Texas7k 345 kV circuit (from its 354 345 kV corridors; see derive_conductor_spec.py).
# Used by apply_conductor_impedance.py (r/x/b) and as the Single_Line_MW fallback in the
# notebook's assembly step.
V_NOM_KV = 345.0
R_OHM_PER_KM = 0.0353
X_OHM_PER_KM = 0.4195
B_S_PER_KM = 6.224e-06
SINGLE_CIRCUIT_MVA = 1232.0  # median real circuit rating, IQR 1146-1313

# Texas7k network converted to PyPSA-USA format (see Texas7k_2022_pypsausa_format/README.md)
TEXAS7K_CSV_DIR = ERCOT_REBUILD / "Texas7k_2022_pypsausa_format"

# Intermediate files written by the pipeline
INTERMEDIATES = Path(
    os.environ.get("ERCOT20_INTERMEDIATES", ERCOT_REBUILD / "intermediates")
)
INTERMEDIATES.mkdir(parents=True, exist_ok=True)

RAW_NETWORK_NC = INTERMEDIATES / "texas7k_raw_network.nc"
BUSES_WITH_REEDS_ZONE_CSV = INTERMEDIATES / "texas7k_buses_with_reeds_zone.csv"
CLUSTERED_NC = INTERMEDIATES / "texas7k_clustered_20.nc"
BUSMAP_CSV = INTERMEDIATES / "texas7k_busmap_20zone.csv"
# Clustered network with conductor-based r/x/b (apply_conductor_impedance.py). Line
# impedances in the GenX case come from the Kron reduction (KRON_CAPACITY_CSV); these are
# a fallback. See README.md, line impedance section.
CLUSTERED_REALIMPEDANCE_NC = INTERMEDIATES / "texas7k_clustered_20_realimpedance.nc"

# Pipeline outputs read by the notebook's assembly step
OUTPUTS = ERCOT_REBUILD / "outputs"
LPE_ALL_LINES_CSV = OUTPUTS / "lpe_all_lines_capacity_20zone.csv"
KRON_CAPACITY_CSV = OUTPUTS / "kron_reduced_capacity_20zone.csv"
KRON_Y_CSV = OUTPUTS / "kron_reduced_Y.csv"
VRE_HAC_FEATURE_CSV = OUTPUTS / "bus_vre_hac_feature.csv"

# PyPSA-USA repo_data (ReEDS and balancing-area shapes). Get it with a sparse clone into
# source_data/ (some demand scripts read it from there directly):
#   git clone --filter=blob:none --sparse https://github.com/PyPSA/pypsa-usa.git \
#       source_data/pypsa_usa_repo_data
#   cd source_data/pypsa_usa_repo_data && git sparse-checkout set workflow/repo_data
#   git checkout f38d756      # the version used for the case
PYPSA_USA_REPO_DATA = Path(os.environ.get(
    "PYPSA_USA_REPO_DATA", SOURCE_DATA / "pypsa_usa_repo_data" / "workflow" / "repo_data"))
REEDS_SHAPES = PYPSA_USA_REPO_DATA / "geospatial" / "Reeds_Shapes" / "rb_and_ba_areas.shp" if PYPSA_USA_REPO_DATA else None
REEDS_CAPACITY_CSV = (
    PYPSA_USA_REPO_DATA / "ReEDS_Constraints" / "transmission"
    / "transmission_capacity_init_AC_ba_NARIS2024.csv"
) if PYPSA_USA_REPO_DATA else None


def require(path, what):
    """Fail loudly and early with a rebuild hint, instead of deep inside pandas/pypsa."""
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Missing {what}: {path}\n"
            f"If this is a pipeline intermediate, re-run the pipeline in order:\n"
            f"  build_and_cluster_texas7k.py -> cluster_texas7k.py -> lpe_all_lines.py\n"
            f"If it is PyPSA-USA repo_data, set $PYPSA_USA_REPO_DATA (see paths.py)."
        )
    return path
