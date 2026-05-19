"""Tests for the ArcGIS pagination utilities."""

import json

import geopandas as gpd
import pytest
import responses
from shapely.geometry import Polygon, box

from shared.api.arcgis import (
    fetch_from_arcgis,
    paginate_arcgis,
    shapely_to_esri_json,
)


class TestShapelyToEsriJson:
    def test_basic_polygon(self):
        """A simple square polygon produces the expected ESRI JSON."""
        poly = box(0, 0, 10, 10)
        # Act
        result = shapely_to_esri_json(poly, wkid=3310)
        # Assert
        assert result is not None
        assert "rings" in result
        assert "spatialReference" in result
        assert result["spatialReference"]["wkid"] == 3310
        # The exterior ring should have 5 coordinates (closed ring)
        assert len(result["rings"][0]) == 5

    def test_empty_polygon(self):
        """An empty polygon returns None."""
        poly = Polygon()
        # Act
        result = shapely_to_esri_json(poly)
        # Assert
        assert result is None

    def test_custom_wkid(self):
        """A custom spatial reference WKID is reflected in the output."""
        poly = box(0, 0, 10, 10)
        # Act
        result = shapely_to_esri_json(poly, wkid=4326)
        # Assert
        assert result["spatialReference"]["wkid"] == 4326

    def test_ring_coordinates_are_closed(self):
        """The exterior ring's first and last coordinates should match."""
        poly = box(1, 2, 5, 6)
        # Act
        result = shapely_to_esri_json(poly, wkid=3310)
        # Assert
        ring = result["rings"][0]
        assert ring[0] == ring[-1]


EXPECTED_APN_1 = "123-456-789"
SAMPLE_FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    "properties": {"APN20": EXPECTED_APN_1, "CITY": "Los Angeles"},
}

EXPECTED_APN_2 = "987-654-321"
SAMPLE_FEATURE_2 = {
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]},
    "properties": {"APN20": EXPECTED_APN_2, "CITY": "San Francisco"},
}

BASE_PARAMS = {
    "geometry": '{"rings":[[[0,0],[10,0],[10,10],[0,10],[0,0]]],"spatialReference":{"wkid":3310}}',
    "geometryType": "esriGeometryPolygon",
    "spatialRel": "esriSpatialRelIntersects",
    "inSR": "3310",
    "outFields": "*",
    "returnGeometry": "true",
    "f": "geojson",
    "where": "1=1",
}
BASE_URL = "https://example.com/arcgis/query"


def _build_geojson_response(features, exceeded_transfer_limit=False):
    """Build a mock GeoJSON response body."""
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {"exceededTransferLimit": exceeded_transfer_limit},
    }


class TestPaginateArcgis:
    @responses.activate
    def test_single_page(self):
        """A single page of results is yielded once."""
        body = _build_geojson_response([SAMPLE_FEATURE], exceeded_transfer_limit=False)
        responses.get(BASE_URL, json=body)

        # Act
        pages = list(
            paginate_arcgis(
                url=BASE_URL,
                geometry=BASE_PARAMS["geometry"],
                max_record_count=2000,
                delay=0,
            )
        )

        # Assert
        assert len(pages) == 1
        assert isinstance(pages[0], gpd.GeoDataFrame)
        assert len(pages[0]) == 1
        assert pages[0]["APN20"].iloc[0] == EXPECTED_APN_1

    @responses.activate
    def test_multi_page(self):
        """Multiple pages are yielded when exceededTransferLimit is true."""
        body_1 = _build_geojson_response([SAMPLE_FEATURE], exceeded_transfer_limit=True)
        body_2 = _build_geojson_response([SAMPLE_FEATURE_2], exceeded_transfer_limit=False)

        responses.get(BASE_URL, json=body_1)
        responses.get(BASE_URL, json=body_2)
        # Act
        pages = list(
            paginate_arcgis(
                url=BASE_URL,
                geometry=BASE_PARAMS["geometry"],
                max_record_count=1,
                delay=0,
            )
        )
        # Assert
        assert len(pages) == 2
        assert len(pages[0]) == 1
        assert len(pages[1]) == 1
        assert pages[0]["APN20"].iloc[0] == EXPECTED_APN_1
        assert pages[1]["APN20"].iloc[0] == EXPECTED_APN_2

    @responses.activate
    def test_empty_response(self):
        """A response with no features yields nothing."""
        body = _build_geojson_response([], exceeded_transfer_limit=False)
        responses.get(BASE_URL, json=body)

        pages = list(
            paginate_arcgis(
                url=BASE_URL,
                geometry=BASE_PARAMS["geometry"],
                max_record_count=2000,
                delay=0,
            )
        )

        assert len(pages) == 0

    @responses.activate
    def test_http_error(self):
        """A non-2xx response raises an HTTPError."""
        responses.get(BASE_URL, status=500)

        with pytest.raises(Exception) as exc_info:
            list(
                paginate_arcgis(
                    url=BASE_URL,
                    geometry=BASE_PARAMS["geometry"],
                    max_record_count=2000,
                    delay=0,
                )
            )

        assert exc_info.type.__name__ in ("HTTPError", "ConnectionError")

    @responses.activate
    def test_offset_increments_correctly(self):
        """The resultOffset parameter increases by max_record_count each page."""
        body_1 = _build_geojson_response([SAMPLE_FEATURE], exceeded_transfer_limit=True)
        body_2 = _build_geojson_response([SAMPLE_FEATURE_2], exceeded_transfer_limit=False)
        record_count = 100

        def request_callback(request):
            offset = int(request.params.get("resultOffset", "0"))
            if offset == 0:
                return (200, {}, json.dumps(body_1))
            elif offset == record_count:
                return (200, {}, json.dumps(body_2))
            return (200, {}, json.dumps(_build_geojson_response([], False)))

        responses.add_callback(
            responses.GET,
            BASE_URL,
            callback=request_callback,
        )

        pages = list(
            paginate_arcgis(
                url=BASE_URL,
                geometry=BASE_PARAMS["geometry"],
                max_record_count=record_count,
                delay=0,
            )
        )

        assert len(pages) == 2


class TestFetchAllParcels:
    @responses.activate
    def test_single_geometry(self):
        """A single geometry returns all pages concatenated."""
        body = _build_geojson_response([SAMPLE_FEATURE], exceeded_transfer_limit=False)
        responses.get(BASE_URL, json=body)

        geom = box(0, 0, 10, 10)
        result = fetch_from_arcgis(
            url=BASE_URL,
            geometries=[geom],
            wkid=3310,
            max_record_count=2000,
            delay=0,
        )

        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 1
        assert result["APN20"].iloc[0] == "123-456-789"

    @responses.activate
    def test_multiple_geometries(self):
        """Multiple geometries have their results concatenated."""
        body = _build_geojson_response([SAMPLE_FEATURE], exceeded_transfer_limit=False)
        responses.get(BASE_URL, json=body)

        geoms = [box(0, 0, 10, 10), box(20, 20, 30, 30)]
        result = fetch_from_arcgis(
            url=BASE_URL,
            geometries=geoms,
            wkid=3310,
            max_record_count=2000,
            delay=0,
        )

        # Two geometries, each returning one feature = 2 rows
        assert len(result) == 2

    @responses.activate
    def test_empty_geometries_list(self):
        """An empty list of geometries returns an empty GeoDataFrame."""
        result = fetch_from_arcgis(
            url=BASE_URL,
            geometries=[],
            wkid=3310,
            max_record_count=2000,
            delay=0,
        )

        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0
