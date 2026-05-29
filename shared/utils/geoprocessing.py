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

    sources_reproj = sources_gdf.to_crs(buffer_crs)
    source_geom = sources_reproj.geometry

    attr_buffers = {
        dist: gpd.GeoDataFrame(
            sources_reproj.drop(columns="geometry"),
            geometry=source_geom.buffer(dist),
            crs=buffer_crs,
        )
        for dist in distances
    }
    geom_buffers = {dist: gpd.GeoDataFrame(geometry=source_geom.buffer(dist), crs=buffer_crs) for dist in distances}

    features_reproj = features_gdf.to_crs(buffer_crs)

    candidates = gpd.sjoin(features_reproj, attr_buffers[distances[-1]], how="inner", predicate="intersects").drop(
        columns=["index_right"], errors="ignore"
    )

    clipped = {}
    current = candidates
    for dist in reversed(distances):
        current = gpd.overlay(current, geom_buffers[dist], how="intersection")
        clipped[dist] = current

    rings = []
    for outer, inner in ring_definitions:
        outer_clip = clipped[outer]
        if inner is None or outer_clip.empty:
            rings.append(outer_clip)
        else:
            rings.append(gpd.overlay(outer_clip, geom_buffers[inner], how=donut_how))
    return rings
