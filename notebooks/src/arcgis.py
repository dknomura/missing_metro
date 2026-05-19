"""Reusable utilities for paginating ArcGIS REST API queries."""

import json
import time
from collections.abc import Generator

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Polygon


def shapely_to_esri_json(polygon: Polygon, wkid: int = 3857) -> dict | None:
    """Convert a Shapely polygon to an ESRI JSON geometry object."""
    if not polygon or polygon.is_empty:
        return None
    coords = list(polygon.exterior.coords)
    rings = [[[x, y] for x, y in coords]]
    return {"rings": rings, "spatialReference": {"wkid": wkid}}


def paginate_arcgis(
    url: str,
    geometry: str = None,
    geometry_type: str = "esriGeometryPolygon",
    spatial_rel: str = "esriSpatialRelIntersects",
    in_sr: int = 3310,
    out_fields: str = "*",
    return_geometry: str = "true",
    f: str = "geojson",
    where: str = "1=1",
    max_record_count: int = 2000,
    delay: float = 0,
) -> Generator[gpd.GeoDataFrame, None, None]:
    """Paginate through an ArcGIS REST API query and yield GeoDataFrames."""
    offset = 0
    while True:
        params = {
            "geometry": geometry,
            "geometryType": geometry_type,
            "spatialRel": spatial_rel,
            "inSR": str(in_sr),
            "outFields": out_fields,
            "returnGeometry": return_geometry,
            "f": f,
            "where": where,
            "resultOffset": str(offset),
            "resultRecordCount": str(max_record_count),
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        if not features:
            break
        page = gpd.GeoDataFrame.from_features(features)
        yield page
        offset += max_record_count
        time.sleep(delay)
        if not data.get("properties", {}).get("exceededTransferLimit", False):
            break


def fetch_from_arcgis(
    url: str,
    geometries: list[Polygon] = None,
    out_fields: str = "*",
    wkid: int = 3310,
    max_record_count: int = 2000,
    delay: float = 0,
) -> gpd.GeoDataFrame:
    """Query an ArcGIS parcel endpoint for multiple geometries and concatenate results."""
    if geometries is None:
        geometries = [None]
    all_geometries: list[gpd.GeoDataFrame] = []
    for geom in geometries:
        esri_geom = shapely_to_esri_json(geom, wkid=wkid)
        geometry_json = json.dumps(esri_geom) if esri_geom else None
        for page in paginate_arcgis(
            url=url,
            geometry=geometry_json,
            geometry_type="esriGeometryPolygon",
            spatial_rel="esriSpatialRelIntersects",
            in_sr=wkid,
            out_fields=out_fields,
            return_geometry="true",
            f="geojson",
            where="1=1",
            max_record_count=max_record_count,
            delay=delay,
        ):
            all_geometries.append(page)
    if not all_geometries:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(pd.concat(all_geometries, ignore_index=True))
