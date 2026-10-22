"""Land area of each zone for wind and solar, following PyPSA-USA's build_bus_regions.py.

    python scripts/build_bus_regions.py

Writes outputs/ercot_zone_regions.geojson, read by build_vre_profiles.py.

Within each county, a Voronoi partition is drawn around the county's substations; the cells
are then merged by the zone each substation belongs to. Because substations sit where the
grid is, zones cover land near existing infrastructure. Re-run after re-clustering.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
from scipy.spatial import Voronoi
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from network_paths import CLUSTERED_NETWORK_NC, BUSMAP_CSV, REEDS_SHAPES, require
from network_paths import SOURCE_DATA, TEXAS7K_CSV_DIR, ZONE_REGIONS_GEOJSON

TEXAS7K = str(TEXAS7K_CSV_DIR)
RAW_SRC = str(SOURCE_DATA)
COUNTIES = f"{RAW_SRC}/pypsa_usa_data/counties/cb_2020_us_county_500k.shp"
# Written to outputs/, where build_vre_profiles.py reads it (network_paths.ZONE_REGIONS_GEOJSON)
OUT = str(ZONE_REGIONS_GEOJSON)

ERCOT_ZONES = {"p60", "p61", "p62", "p63", "p64", "p65", "p67"}


def voronoi_partition_pts(points, outline):
    """Voronoi cells for `points`, each clipped to `outline`. Mirrors PyPSA-USA's helper."""
    points = np.asarray(points)
    if len(points) == 1:
        return [outline]

    xmin, ymin, xmax, ymax = outline.bounds
    xspan, yspan = xmax - xmin, ymax - ymin
    # Far-away mirror points so every real point gets a bounded cell.
    far = np.array([
        [xmin - 3 * xspan, ymin - 3 * yspan],
        [xmin - 3 * xspan, ymax + 3 * yspan],
        [xmax + 3 * xspan, ymin - 3 * yspan],
        [xmax + 3 * xspan, ymax + 3 * yspan],
    ])
    vor = Voronoi(np.vstack((points, far)))

    polygons = []
    for i in range(len(points)):
        region = vor.regions[vor.point_region[i]]
        if -1 in region or len(region) < 3:
            polygons.append(outline)
            continue
        poly = Polygon(vor.vertices[region]).buffer(0)
        polygons.append(poly.intersection(outline))
    return polygons


def main():
    # --- substations, and the zone each belongs to (via its buses) ---
    bus2sub = pd.read_csv(f"{TEXAS7K}/bus2sub.csv", index_col=0)
    sub = pd.read_csv(f"{TEXAS7K}/sub.csv", index_col=0)
    bus2sub.index = bus2sub.index.astype(str)

    busmap = pd.read_csv(require(BUSMAP_CSV, "busmap"), index_col=0)["cluster"].astype(str)
    busmap.index = busmap.index.astype(str)
    nc = pypsa.Network(str(require(CLUSTERED_NETWORK_NC, "clustered network")))
    cluster_to_zone = {cl: i + 1 for i, cl in enumerate(nc.buses.index)}

    b2s = bus2sub[bus2sub.index.isin(busmap.index)].copy()
    b2s["zone"] = busmap.reindex(b2s.index).map(cluster_to_zone)
    b2s = b2s.dropna(subset=["zone"])
    # A substation's zone = the zone most of its buses land in.
    sub_zone = b2s.groupby("sub_id")["zone"].agg(lambda s: s.mode().iloc[0]).astype(int)

    subs = sub.loc[sub.index.isin(sub_zone.index)].copy()
    subs["zone"] = sub_zone.reindex(subs.index)
    subs = gpd.GeoDataFrame(
        subs, geometry=gpd.points_from_xy(subs.lon, subs.lat), crs="EPSG:4326"
    )
    print(f"Substations in ERCOT zones: {len(subs)}")

    # --- assign each substation to a county ---
    counties = gpd.read_file(COUNTIES).to_crs("EPSG:4326")
    reeds = gpd.read_file(require(REEDS_SHAPES, "ReEDS shapes")).to_crs("EPSG:4326")
    ercot_shape = unary_union(reeds[reeds["rb"].isin(ERCOT_ZONES)].geometry)
    counties = counties[counties.intersects(ercot_shape)].copy()
    print(f"Counties intersecting ERCOT: {len(counties)}")

    subs = gpd.sjoin(subs, counties[["GEOID", "geometry"]], how="left", predicate="within")
    subs = subs.dropna(subset=["GEOID"])
    print(f"Substations matched to a county: {len(subs)}")

    # --- Voronoi within each county, over that county's substations ---
    cells = []
    for geoid, grp in subs.groupby("GEOID"):
        shape = counties.loc[counties.GEOID == geoid, "geometry"].iloc[0]
        shape = shape.intersection(ercot_shape)
        if shape.is_empty:
            continue
        polys = voronoi_partition_pts(grp[["lon", "lat"]].values, shape)
        for (idx, row), poly in zip(grp.iterrows(), polys):
            if poly.is_empty:
                continue
            cells.append({"zone": int(row.zone), "geometry": poly})
    print(f"County-level Voronoi cells: {len(cells)}")

    # Counties with no substation belong to no zone under this scheme -- give them to the
    # nearest zone's cell so no ERCOT land is silently dropped from the VRE potential.
    cells_gdf = gpd.GeoDataFrame(cells, crs="EPSG:4326")
    covered = unary_union(cells_gdf.geometry)
    leftover = ercot_shape.difference(covered)
    if not leftover.is_empty and leftover.area > 1e-6:
        zone_union = cells_gdf.dissolve("zone")
        pieces = [leftover] if leftover.geom_type == "Polygon" else list(leftover.geoms)
        extra = []
        for piece in pieces:
            if piece.area < 1e-9:
                continue
            nearest = zone_union.distance(piece).idxmin()
            extra.append({"zone": int(nearest), "geometry": piece})
        if extra:
            cells_gdf = pd.concat([cells_gdf, gpd.GeoDataFrame(extra, crs="EPSG:4326")],
                                  ignore_index=True)
            print(f"Reassigned {len(extra)} uncovered piece(s) to the nearest zone")

    regions = cells_gdf.dissolve("zone").reset_index()
    regions["name"] = regions.zone
    regions = regions.set_index("name")
    regions.to_file(OUT, driver="GeoJSON")
    print(f"\nSaved {len(regions)} zone regions -> {OUT}")

    a = regions.to_crs("EPSG:5070").area / 1e6
    print(f"Zone area km^2: min {a.min():,.0f} | median {a.median():,.0f} | max {a.max():,.0f} "
          f"| total {a.sum():,.0f}")


if __name__ == "__main__":
    main()
