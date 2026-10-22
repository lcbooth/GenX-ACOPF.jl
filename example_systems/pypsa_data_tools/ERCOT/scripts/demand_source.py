"""Path to the ERCOT weather-zone demand used in our work, shared by VRE and GenX assembly."""
from network_paths import OUTPUTS


def demand_csv():
    """Return the demand output, with a build hint if it is missing."""
    path = OUTPUTS / "ercot_demand_20zone_nrel_ercot_wz.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Demand file not found: {path}\n"
            "Build it with: python scripts/build_demand_nrel_ercot_wz.py"
        )
    return path
