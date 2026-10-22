"""Paths for the scripts in this folder: a re-export of ../network/paths.py, so that both
folders use the same locations.
"""
import sys
from pathlib import Path

_NETWORK = Path(__file__).resolve().parents[1] / "network"
sys.path.insert(0, str(_NETWORK))

from paths import (  # noqa: E402
    CLUSTERED_NC as CLUSTERED_NETWORK_NC,
    CLUSTERED_REALIMPEDANCE_NC,
    RAW_NETWORK_NC,
    BUSES_WITH_REEDS_ZONE_CSV,
    BUSMAP_CSV,
    LPE_ALL_LINES_CSV,
    KRON_CAPACITY_CSV,
    REEDS_SHAPES,
    OUTPUTS,
    SOURCE_DATA,
    TEXAS7K_CSV_DIR,
    VRE_HAC_FEATURE_CSV,
    EXAMPLE_SYSTEMS,
    SINGLE_CIRCUIT_MVA,
    require,
)

ZONE_REGIONS_GEOJSON = OUTPUTS / "ercot_zone_regions.geojson"

__all__ = [
    "CLUSTERED_NETWORK_NC",
    "CLUSTERED_REALIMPEDANCE_NC",
    "RAW_NETWORK_NC",
    "BUSES_WITH_REEDS_ZONE_CSV",
    "BUSMAP_CSV",
    "LPE_ALL_LINES_CSV",
    "KRON_CAPACITY_CSV",
    "ZONE_REGIONS_GEOJSON",
    "REEDS_SHAPES",
    "OUTPUTS",
    "SOURCE_DATA",
    "TEXAS7K_CSV_DIR",
    "VRE_HAC_FEATURE_CSV",
    "EXAMPLE_SYSTEMS",
    "SINGLE_CIRCUIT_MVA",
    "require",
]
