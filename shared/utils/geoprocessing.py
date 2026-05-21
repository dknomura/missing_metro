from typing import Literal

import geopandas as gpd


def clip_to_buffer_rings(
    features_gdf: gpd.GeoDataFrame,
    sources_gdf: gpd.GeoDataFrame,
    buffer_distances: list[int | float],
    buffer_crs: str = "EPSG:2229",
    donut_how: Literal["difference", "union"] = "difference",
) -> list[gpd.GeoDataFrame]:
    """Clip features to concentric donut rings buffered around source geometries.

    Parameters
    ----------
    features_gdf:
        Features to clip.
    sources_gdf:
        Source geometries to buffer. All non-geometry columns are carried
        through to the result.
    buffer_distances:
        Buffer distances in the units of ``buffer_crs``. Rings are built
        from smallest to largest — e.g. ``[200, 1320, 2640]`` produces a
        200ft disc, a 200–1320ft donut, and a 1320–2640ft donut.
    buffer_crs:
        CRS to reproject into before buffering and spatial operations.
        Defaults to EPSG:2229 (California State Plane, feet).
    donut_how:
        How to subtract the inner buffer from the outer clip when forming
        donuts. ``"difference"`` produces smooth donut rings clipped to the
        buffer boundary; ``"union"`` retains the full parcel geometry within
        the outer ring without cutting at the inner boundary.

    Returns
    -------
    list[gpd.GeoDataFrame]
        One GeoDataFrame per distance in ``buffer_distances``, in ascending
        order. The first entry is the innermost disc; subsequent entries are
        donuts. Results are returned in ``buffer_crs``.
    """
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

    clipped = {
        dist: gpd.overlay(
            gpd.sjoin(features_reproj, buf, how="inner", predicate="intersects"),
            buf,
            how="intersection",
        )
        for dist, buf in buffers.items()
    }

    rings = []
    for outer, inner in ring_definitions:
        outer_clip = clipped[outer]
        if inner is None:
            rings.append(outer_clip)
        else:
            rings.append(gpd.overlay(outer_clip, buffers[inner][["geometry"]], how=donut_how))
    return rings
