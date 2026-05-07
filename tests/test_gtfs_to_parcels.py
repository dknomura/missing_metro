from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from sb79map import (
    aggregate_parcels_to_stops,
    assign_nearest_stop,
    compute_scag_density,
    create_buffer_donuts,
    create_half_mi_buffers,
    join_scag_ca_parcels,
    parse_gtfs_zip,
    trim_around_stations,
)
from shapely.geometry import Point, box


def _make_gtfs_zip(
    routes: list[dict],
    trips: list[dict],
    stop_times: list[dict],
    stops: list[dict],
    calendar: list[dict] | None = None,
) -> bytes:
    """Create an in-memory GTFS zip file for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if routes:
            cols = list(routes[0].keys())
            z.writestr(
                "routes.txt",
                ",".join(cols) + "\n" + "\n".join(",".join(str(r[c]) for c in cols) for r in routes),
            )
        if trips:
            cols = list(trips[0].keys())
            z.writestr(
                "trips.txt",
                ",".join(cols) + "\n" + "\n".join(",".join(str(t[c]) for c in cols) for t in trips),
            )
        if stop_times:
            cols = list(stop_times[0].keys())
            z.writestr(
                "stop_times.txt",
                ",".join(cols) + "\n" + "\n".join(",".join(str(s[c]) for c in cols) for s in stop_times),
            )
        if stops:
            cols = list(stops[0].keys())
            z.writestr(
                "stops.txt",
                ",".join(cols) + "\n" + "\n".join(",".join(str(s[c]) for c in cols) for s in stops),
            )
        if calendar:
            cols = list(calendar[0].keys())
            z.writestr(
                "calendar.txt",
                ",".join(cols) + "\n" + "\n".join(",".join(str(c[col]) for col in cols) for c in calendar),
            )
    return buf.getvalue()


def _make_stops_gdf(stops_data: list[dict]) -> gpd.GeoDataFrame:
    """Create a stops GeoDataFrame from a list of dicts."""
    records = []
    for s in stops_data:
        records.append(
            {
                "stop_id": s["stop_id"],
                "stop_name": s.get("stop_name", ""),
                "Tier": s["Tier"],
                "geometry": Point(s["lon"], s["lat"]),
            }
        )
    return gpd.GeoDataFrame(records, crs="EPSG:4326")


def _make_parcels_gdf(parcels_data: list[dict]) -> gpd.GeoDataFrame:
    """Create a parcels GeoDataFrame from a list of dicts."""
    records = []
    for p in parcels_data:
        rec = {
            "APN20": p["APN20"],
            "current_density_du_per_ac": p.get("current_density_du_per_ac", 0),
            "ZN19_CITY": p.get("ZN19_CITY", ""),
            "ZN19_SCAG": p.get("ZN19_SCAG", 0),
            "CITY": p.get("CITY", ""),
            "COUNTY": p.get("COUNTY", ""),
            "Tier": p.get("Tier", 1),
            "geometry": box(*p["bbox"]),
        }
        # Include any extra columns passed in the dict
        for k, v in p.items():
            if k not in rec and k != "bbox":
                rec[k] = v
        records.append(rec)
    return gpd.GeoDataFrame(records, crs="EPSG:4326")


class TestParseGtfsZip:
    def test_basic_parse(self):
        """Parse a minimal GTFS zip with one subway route."""
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "801", "route_type": "1", "agency_id": "metro"}],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                {"route_id": "801", "trip_id": "t2", "service_id": "wk", "direction_id": "1"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "801S"},
                {"trip_id": "t2", "stop_id": "801S"},
            ],
            stops=[
                {
                    "stop_id": "801S",
                    "stop_name": "Test Station",
                    "stop_lat": "34.0",
                    "stop_lon": "-118.0",
                    "location_type": "1",
                },
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            assert result.iloc[0]["stop_id"] == "801S"
            assert result.iloc[0]["Tier"] == 1  # subway → Tier 1
            assert result.iloc[0]["routetypes"] == "1"
        finally:
            path.unlink(missing_ok=True)

    def test_tier1_light_rail_high_freq(self):
        """route_type=0 with n_arrivals >= 72 → Tier 1."""
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
            trips=[{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(40)]
            + [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(40, 80)],
            stop_times=[{"trip_id": f"t{i}", "stop_id": "801S"} for i in range(80)],
            stops=[
                {"stop_id": "801S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs2.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            # 80 trips / 1 weekday service = 80/day >= 72 → Tier 1
            assert result.iloc[0]["Tier"] == 1
        finally:
            path.unlink(missing_ok=True)

    def test_tier1_light_rail_high_freq_72plus(self):
        """route_type=0 with n_arrivals >= 72 → Tier 1."""
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
            trips=[{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(200)]
            + [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(200, 400)],
            stop_times=[{"trip_id": f"t{i}", "stop_id": "801S"} for i in range(400)],
            stops=[
                {"stop_id": "801S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs3.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            # 400 trips / 5 days = 80/day >= 72 → Tier 1
            assert result.iloc[0]["Tier"] == 1
        finally:
            path.unlink(missing_ok=True)

    def test_tier2_light_rail_low_freq(self):
        """route_type=0 with n_arrivals < 72 → Tier 2."""
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
            trips=[{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(10)]
            + [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(10, 20)],
            stop_times=[{"trip_id": f"t{i}", "stop_id": "801S"} for i in range(20)],
            stops=[
                {"stop_id": "801S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs4.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            # 20 trips / 5 = 4/day < 72 → Tier 2
            assert result.iloc[0]["Tier"] == 2
        finally:
            path.unlink(missing_ok=True)

    def test_tier2_commuter_rail_medium(self):
        """route_type=2 with 48 <= n_arrivals < 72 → Tier 2."""
        # Use 2 weekday services so 300 trips / 2 = 150/day... still >= 72
        # Need fewer trips: 120 trips / 2 services = 60/day, 48 <= 60 < 72 → Tier 2
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "701", "route_type": "2", "agency_id": "metrolink"}],
            trips=[{"route_id": "701", "trip_id": f"t{i}", "service_id": "wk1", "direction_id": "0"} for i in range(30)]
            + [{"route_id": "701", "trip_id": f"t{i}", "service_id": "wk2", "direction_id": "0"} for i in range(30, 60)]
            + [{"route_id": "701", "trip_id": f"t{i}", "service_id": "wk1", "direction_id": "1"} for i in range(60, 90)]
            + [{"route_id": "701", "trip_id": f"t{i}", "service_id": "wk2", "direction_id": "1"} for i in range(90, 120)],
            stop_times=[{"trip_id": f"t{i}", "stop_id": "701S"} for i in range(120)],
            stops=[
                {"stop_id": "701S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk1",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                },
                {
                    "service_id": "wk2",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                },
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs5.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            # 120 trips / 2 weekday services = 60/day, 48 <= 60 < 72 → Tier 2
            assert result.iloc[0]["Tier"] == 2
        finally:
            path.unlink(missing_ok=True)

    def test_tier_override(self):
        """Override a Tier 1 route to Tier 2."""
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "801", "route_type": "1", "agency_id": "metro"}],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
            ],
            stop_times=[{"trip_id": "t1", "stop_id": "801S"}],
            stops=[
                {"stop_id": "801S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs6.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path, tier_overrides={"801": 2})
            assert len(result) == 1
            assert result.iloc[0]["Tier"] == 2  # overridden
        finally:
            path.unlink(missing_ok=True)

    def test_skip_non_tier_stops(self):
        """Stops with route_type > 2 (e.g. bus) should be excluded."""
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "901", "route_type": "3", "agency_id": "metro"}],
            trips=[
                {"route_id": "901", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
            ],
            stop_times=[{"trip_id": "t1", "stop_id": "901S"}],
            stops=[
                {"stop_id": "901S", "stop_name": "Bus Stop", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs7.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 0  # no Tier 1 or 2 stops
        finally:
            path.unlink(missing_ok=True)

    def test_no_parent_stations(self):
        """Feed with only location_type=0 stops (no parent stations) should still work.

        Some GTFS feeds (e.g. OC Streetcar) have no location_type=1 entries.
        Each boarding stop should be treated as its own station.
        """
        gtfs_bytes = _make_gtfs_zip(
            routes=[{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                {"route_id": "801", "trip_id": "t2", "service_id": "wk", "direction_id": "1"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "S1"},
                {"trip_id": "t2", "stop_id": "S1"},
            ],
            stops=[
                {
                    "stop_id": "S1",
                    "stop_name": "Stop 1",
                    "stop_lat": "34.0",
                    "stop_lon": "-118.0",
                    "location_type": "0",
                },
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs_no_parent.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            assert result.iloc[0]["stop_id"] == "S1"
            assert result.iloc[0]["Tier"] == 2  # route_type=0, 2 trips/5=0.4/day < 72
        finally:
            path.unlink(missing_ok=True)

    def test_mixed_route_types(self):
        """Station with route_types '0,1' should be Tier 1 (has subway)."""

        gtfs_bytes = _make_gtfs_zip(
            routes=[
                {"route_id": "801", "route_type": "1", "agency_id": "metro"},
                {"route_id": "802", "route_type": "0", "agency_id": "metro"},
            ],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                {"route_id": "802", "trip_id": "t2", "service_id": "wk", "direction_id": "0"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "801S"},
                {"trip_id": "t2", "stop_id": "801S"},
            ],
            stops=[
                {"stop_id": "801S", "stop_name": "Mixed", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
            ],
            calendar=[
                {
                    "service_id": "wk",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ],
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs8.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = parse_gtfs_zip(path)
            assert len(result) == 1
            assert result.iloc[0]["Tier"] == 1  # has route_type 1
            assert "1" in result.iloc[0]["routetypes"]
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: create_half_mi_buffers
# ---------------------------------------------------------------------------


class TestCreateHalfMiBuffers:
    def test_basic(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        buffers = create_half_mi_buffers(stops)
        assert len(buffers) == 1
        assert buffers.crs.to_string() == "EPSG:3310"
        # Buffer should be a polygon, not a point
        assert buffers.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon")
        # Area should be roughly pi * 804.7^2 ≈ 2,034,000 m²
        area = buffers.geometry.iloc[0].area
        assert 1_900_000 < area < 2_100_000


# ---------------------------------------------------------------------------
# Tests: compute_scag_density
# ---------------------------------------------------------------------------


class TestComputeScagDensity:
    def test_la_density(self):
        parcels = _make_parcels_gdf(
            [
                {"APN20": "1", "ZN19_CITY": "RD1.5", "CITY": "Los Angeles", "bbox": (0, 0, 100, 100)},
            ]
        )
        result = compute_scag_density(parcels)
        assert "current_density_du_per_ac" in result.columns
        # RD1.5 → 43560 / 1500 = 29.04
        assert result.iloc[0]["current_density_du_per_ac"] == pytest.approx(29.04, rel=0.01)

    def test_non_la_density(self):
        parcels = _make_parcels_gdf(
            [
                {"APN20": "1", "ZN19_SCAG": 1111, "CITY": "Other", "bbox": (-118.3, 34.0, -118.2, 34.1)},
            ]
        )
        result = compute_scag_density(parcels)
        # 1111 → 1 / area_acres (area-dependent)
        assert result.iloc[0]["current_density_du_per_ac"] > 0

    def test_non_la_density_zero_default(self):
        """Unrecognized ZN19_SCAG codes should get NaN (excluded downstream)."""
        parcels = _make_parcels_gdf(
            [
                {"APN20": "1", "ZN19_SCAG": 9999, "CITY": "Other", "bbox": (-118.3, 34.0, -118.2, 34.1)},
            ]
        )
        result = compute_scag_density(parcels)
        assert np.isnan(result.iloc[0]["current_density_du_per_ac"])


# ---------------------------------------------------------------------------
# Tests: trim_around_stations
# ---------------------------------------------------------------------------


class TestTrimAroundStations:
    def test_parcel_within_buffer(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        # Parcel right next to the stop
        parcels = _make_parcels_gdf(
            [
                {"APN20": "1", "current_density_du_per_ac": 10, "Tier": 1, "bbox": (-118.001, 33.999, -117.999, 34.001)},
            ]
        )
        result = trim_around_stations(parcels, stops, 200)
        assert len(result) >= 1
        assert "buffer_zone_id" in result.columns
        assert result["buffer_zone_id"].iloc[0] == "200ft"

    def test_parcel_far_from_stop(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        # Parcel far away
        parcels = _make_parcels_gdf(
            [
                {"APN20": "1", "current_density_du_per_ac": 10, "Tier": 1, "bbox": (-117.0, 34.0, -116.99, 34.01)},
            ]
        )
        result = trim_around_stations(parcels, stops, 200)
        assert len(result) == 0  # no intersection


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
        buffers = create_half_mi_buffers(stops)
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
        buffers = create_half_mi_buffers(stops)
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
