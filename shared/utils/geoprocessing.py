from typing import Literal

import geopandas as gpd

from shared.utils.constants import SCAG_FT_CRS


def clip_to_buffer_rings(
    features_gdf: gpd.GeoDataFrame,
    sources_gdf: gpd.GeoDataFrame,
    buffer_distances: list[int | float],
    buffer_crs: str = SCAG_FT_CRS,
    donut_how: Literal["difference", "union"] = "difference",
) -> list[gpd.GeoDataFrame]:
    distances = sorted(buffer_distances)
    ring_definitions = [(dist, distances[i - 1] if i > 0 else None) for i, dist in enumerate(distances)]
    sources_reprojected = sources_gdf.to_crs(buffer_crs).geometry

    buffers = {
        dist: gpd.GeoDataFrame(
            sources_gdf.drop(columns="geometry"),
            geometry=sources_reprojected.buffer(dist),
            crs=buffer_crs,
        )
        for dist in distances
    }

    features_reproj = features_gdf.to_crs(buffer_crs)

    largest = buffers[distances[-1]]
    candidates = gpd.sjoin(features_reproj, largest, how="inner", predicate="intersects")

    clipped = {dist: gpd.overlay(candidates, buf[["geometry"]], how="intersection") for dist, buf in buffers.items()}

    rings = []
    for outer, inner in ring_definitions:
        outer_clip = clipped[outer]
        if inner is None or outer_clip.empty:
            rings.append(outer_clip)
        else:
            rings.append(gpd.overlay(outer_clip, buffers[inner][["geometry"]], how=donut_how))
    return rings
