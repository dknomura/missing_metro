from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Point, box

from notebooks.sb79map import (
    aggregate_parcels_to_stops,
    assign_nearest_stop,
    create_buffer_donuts,
    join_scag_ca_parcels,
)
from shared.utils.constants import HALF_MI_M
from tests.test_helpers import _make_parcels_gdf, _make_stops_gdf


def _make_half_mi_buffer_gdf(stops: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Create a GeoDataFrame of half-mile buffers around stops."""
    return gpd.GeoDataFrame(
        {"stop_id": stops["stop_id"].values},
        geometry=stops.to_crs("EPSG:3310").geometry.buffer(HALF_MI_M, resolution=8),
        crs="EPSG:3310",
    )


# ---------------------------------------------------------------------------
# Tests: compute_scag_density
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: trim_around_stations
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: create_buffer_donuts
# ---------------------------------------------------------------------------


class TestCreateBufferDonuts:
    def test_weighted_density(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        parcels = _make_parcels_gdf(
            [
                {
                    "APN20": "1",
                    "current_density_du_per_ac": 10,
                    "ZN19_CITY": "",
                    "ZN19_SCAG": 0,
                    "CITY": "Other",
                    "COUNTY": "LA",
                    "Tier": 1,
                    "bbox": (-118.001, 33.999, -117.999, 34.001),
                },
            ]
        )
        result = create_buffer_donuts(stops, parcels)
        assert len(result) >= 1
        assert "new_density_du_per_ac" in result.columns
        assert "new_dwelling_units" in result.columns
        assert "current_dwelling_units" in result.columns
        assert result["new_density_du_per_ac"].iloc[0] > 0


# ---------------------------------------------------------------------------
# Tests: join_scag_ca_parcels
# ---------------------------------------------------------------------------


class TestJoinScagCaParcels:
    def test_outer_join(self):
        scag = _make_parcels_gdf(
            [
                {"APN20": "1", "current_density_du_per_ac": 10, "new_density_du_per_ac": 20, "bbox": (0, 0, 1, 1)},
            ]
        )
        ca = gpd.GeoDataFrame(
            {"PARCEL_APN": ["1", "2"], "SITE_CITY": ["LA", "SF"], "COUNTYNAME": ["Los Angeles", "San Francisco"]},
            geometry=[box(0, 0, 1, 1), box(2, 2, 3, 3)],
            crs="EPSG:4326",
        )
        result = join_scag_ca_parcels(scag, ca)
        assert len(result) == 2
        assert "city" in result.columns
        assert "county" in result.columns
        assert "apn" in result.columns


# ---------------------------------------------------------------------------
# Tests: assign_nearest_stop
# ---------------------------------------------------------------------------


class TestAssignNearestStop:
    def test_single_stop(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        buffers = _make_half_mi_buffer_gdf(stops)
        parcels = gpd.GeoDataFrame(
            {"PARCEL_APN": ["1"]},
            geometry=[Point(-118.0005, 34.0005).buffer(0.001)],
            crs="EPSG:4326",
        )
        result = assign_nearest_stop(parcels, stops, buffers)
        assert len(result) == 1
        assert "clipped_geom" in result.columns
        assert result["stop_id"].iloc[0] == "A"

    def test_multi_buffer_nearest(self):
        """Parcel intersecting two stop buffers should be assigned to the nearest."""
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
                {"stop_id": "B", "Tier": 1, "lat": 34.01, "lon": -118.0},
            ]
        )
        buffers = _make_half_mi_buffer_gdf(stops)
        # Parcel closer to stop A
        parcels = gpd.GeoDataFrame(
            {"PARCEL_APN": ["1"]},
            geometry=[Point(-118.0001, 34.0001).buffer(0.001)],
            crs="EPSG:4326",
        )
        result = assign_nearest_stop(parcels, stops, buffers)
        assert len(result) == 1
        assert result["stop_id"].iloc[0] == "A"


# ---------------------------------------------------------------------------
# Tests: aggregate_parcels_to_stops
# ---------------------------------------------------------------------------


class TestAggregateParcelsToStops:
    def test_basic_aggregation(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        parcels = gpd.GeoDataFrame(
            {
                "stop_id": ["A"],
                "clipped_geom": [box(-118.001, 33.999, -117.999, 34.001)],
                "new_dwelling_units": [100],
                "current_dwelling_units": [50],
                "additional_du": [50],
                "CITY": ["LOS ANGELES"],
                "COUNTY": ["LOS ANGELES"],
                "geometry": [box(-118.001, 33.999, -117.999, 34.001)],
            },
            crs="EPSG:4326",
        )

        result = aggregate_parcels_to_stops(parcels, stops)
        assert len(result) == 1
        assert result["parcel_acres"].iloc[0] > 0
        assert result["additional_dwelling_units"].iloc[0] == 50  # 100 - 50
        assert result["city"].iloc[0] == "LOS ANGELES"
        assert result["county"].iloc[0] == "LOS ANGELES"
