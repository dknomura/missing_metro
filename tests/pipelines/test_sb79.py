import io
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from shared.pipelines.sb79 import assign_tier_to_stops_from_gtfs, compute_dwelling_units, compute_scag_density
from tests.test_helpers import _make_parcels_gdf, _make_stops_gdf


def _make_gtfs_zip(
    gtfs_data: dict[str, list[dict]],
) -> bytes:
    """Create an in-memory GTFS zip file for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for filename, rows in gtfs_data.items():
            if not rows:
                continue
            cols = list(rows[0].keys())
            z.writestr(
                f"{filename}.txt",
                ",".join(cols) + "\n" + "\n".join(",".join(str(r[c]) for c in cols) for r in rows),
            )
    return buf.getvalue()


class TestParseGtfsZip:
    def test_basic_parse(self):
        """Parse a minimal GTFS zip with one subway route."""
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "801", "route_type": "1", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                    {"route_id": "801", "trip_id": "t2", "service_id": "wk", "direction_id": "1"},
                ],
                "stop_times": [
                    {"trip_id": "t1", "stop_id": "801S", "arrival_time": "08:00:00", "departure_time": "08:01:00"},
                    {"trip_id": "t2", "stop_id": "801S", "arrival_time": "08:05:00", "departure_time": "08:06:00"},
                ],
                "stops": [
                    {
                        "stop_id": "801S",
                        "stop_name": "Test Station",
                        "stop_lat": "34.0",
                        "stop_lon": "-118.0",
                        "location_type": "1",
                    },
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    }
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            assert result.iloc[0]["stop_id"] == "801S"
            assert result.iloc[0]["Tier"] == 1  # subway → Tier 1
            assert result.iloc[0]["route_types"] == "1"
        finally:
            path.unlink(missing_ok=True)

    def test_tier2_light_rail_low_freq(self):
        """route_type=0 with n_arrivals < 72 → Tier 2."""
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(10)
                ]
                + [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(10, 20)],
                "stop_times": [
                    {"trip_id": f"t{i}", "stop_id": "801S", "arrival_time": "08:00:00", "departure_time": "08:01:00"}
                    for i in range(20)
                ],
                "stops": [
                    {"stop_id": "801S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    }
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs4.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            # 20 trips / 5 = 4/day < 72 → Tier 2
            assert result.iloc[0]["Tier"] == 2
        finally:
            path.unlink(missing_ok=True)

    def test_tier2_commuter_rail_medium(self):
        """route_type=2 with 48 <= n_arrivals < 72 → Tier 2."""
        # gtfs_kit counts all trips across all services active on the weekday.
        # Use 60 trips with a single service: 48 <= 60 < 72 → Tier 2
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "701", "route_type": "2", "agency_id": "metrolink"}],
                "trips": [
                    {"route_id": "701", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(30)
                ]
                + [{"route_id": "701", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(30, 60)],
                "stop_times": [
                    {"trip_id": f"t{i}", "stop_id": "701S", "arrival_time": "08:00:00", "departure_time": "08:01:00"}
                    for i in range(60)
                ],
                "stops": [
                    {"stop_id": "701S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    },
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs5.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            assert result.iloc[0]["Tier"] == 2
        finally:
            path.unlink(missing_ok=True)

    def test_tier_override(self):
        """Override a Tier 1 route to Tier 2."""
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "801", "route_type": "1", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                ],
                "stop_times": [
                    {"trip_id": "t1", "stop_id": "801S", "arrival_time": "08:00:00", "departure_time": "08:01:00"}
                ],
                "stops": [
                    {"stop_id": "801S", "stop_name": "Test", "stop_lat": "34.0", "stop_lon": "-118.0", "location_type": "1"},
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    }
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs6.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path, tier_overrides={"801": 2})
            assert len(result) == 1
            assert result.iloc[0]["Tier"] == 2  # overridden
        finally:
            path.unlink(missing_ok=True)

    def test_skip_non_tier_stops(self):
        """Stops with route_type > 2 (e.g. bus) should be excluded."""
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "901", "route_type": "3", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "901", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                ],
                "stop_times": [
                    {"trip_id": "t1", "stop_id": "901S", "arrival_time": "08:00:00", "departure_time": "08:01:00"}
                ],
                "stops": [
                    {
                        "stop_id": "901S",
                        "stop_name": "Bus Stop",
                        "stop_lat": "34.0",
                        "stop_lon": "-118.0",
                        "location_type": "1",
                    },
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    }
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs7.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 0  # no Tier 1 or 2 stops
        finally:
            path.unlink(missing_ok=True)

    def test_no_parent_stations(self):
        """Feed with only location_type=0 stops (no parent stations) should still work.

        Some GTFS feeds (e.g. OC Streetcar) have no location_type=1 entries.
        Each boarding stop should be treated as its own station.
        """
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                    {"route_id": "801", "trip_id": "t2", "service_id": "wk", "direction_id": "1"},
                ],
                "stop_times": [
                    {"trip_id": "t1", "stop_id": "S1", "arrival_time": "08:00:00", "departure_time": "08:01:00"},
                    {"trip_id": "t2", "stop_id": "S1", "arrival_time": "08:05:00", "departure_time": "08:06:00"},
                ],
                "stops": [
                    {
                        "stop_id": "S1",
                        "stop_name": "Stop 1",
                        "stop_lat": "34.0",
                        "stop_lon": "-118.0",
                        "location_type": "0",
                    },
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    }
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs_no_parent.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            assert result.iloc[0]["stop_id"] == "S1"
            assert result.iloc[0]["Tier"] == 2  # route_type=0, 2 trips/5=0.4/day < 72
        finally:
            path.unlink(missing_ok=True)

    def test_mixed_route_types(self):
        """Station with route_types '0,1' should be Tier 1 (has subway)."""

        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [
                    {"route_id": "801", "route_type": "1", "agency_id": "metro"},
                    {"route_id": "802", "route_type": "0", "agency_id": "metro"},
                ],
                "trips": [
                    {"route_id": "801", "trip_id": "t1", "service_id": "wk", "direction_id": "0"},
                    {"route_id": "802", "trip_id": "t2", "service_id": "wk", "direction_id": "0"},
                ],
                "stop_times": [
                    {"trip_id": "t1", "stop_id": "801S", "arrival_time": "08:00:00", "departure_time": "08:01:00"},
                    {"trip_id": "t2", "stop_id": "801S", "arrival_time": "08:05:00", "departure_time": "08:06:00"},
                ],
                "stops": [
                    {
                        "stop_id": "801S",
                        "stop_name": "Mixed",
                        "stop_lat": "34.0",
                        "stop_lon": "-118.0",
                        "location_type": "1",
                    },
                ],
                "calendar": [
                    {
                        "service_id": "wk",
                        "monday": "1",
                        "tuesday": "1",
                        "wednesday": "1",
                        "thursday": "1",
                        "friday": "1",
                        "saturday": "0",
                        "sunday": "0",
                        "start_date": "20250101",
                        "end_date": "20251231",
                    }
                ],
            }
        )
        path = Path(tempfile.gettempdir()) / "_test_gtfs8.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            assert result.iloc[0]["Tier"] == 1  # has route_type 1
            assert "1" in result.iloc[0]["route_types"]
        finally:
            path.unlink(missing_ok=True)


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


class TestCreateBufferDonuts:
    def test_basic_density(self):
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
        result = compute_dwelling_units(stops, parcels)
        assert len(result) >= 1
        assert "new_density_du_per_ac" in result.columns
        assert "new_dwelling_units" in result.columns
        assert "current_dwelling_units" in result.columns
        assert "additional_du" in result.columns
        assert "buffer_zone_id" in result.columns
        assert result["new_density_du_per_ac"].iloc[0] > 0

    def test_no_intersection_empty(self):
        stops = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        parcels = _make_parcels_gdf(
            [
                {"APN20": "1", "current_density_du_per_ac": 10, "Tier": 1, "bbox": (-117.0, 34.0, -116.99, 34.01)},
            ]
        )
        result = compute_dwelling_units(stops, parcels)
        assert len(result) == 0

    def test_tier_density_mapping(self):
        stops_t1 = _make_stops_gdf(
            [
                {"stop_id": "A", "Tier": 1, "lat": 34.0, "lon": -118.0},
            ]
        )
        stops_t2 = _make_stops_gdf(
            [
                {"stop_id": "B", "Tier": 2, "lat": 34.0, "lon": -118.0},
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
        r1 = compute_dwelling_units(stops_t1, parcels)
        r2 = compute_dwelling_units(stops_t2, parcels)
        assert r1["new_density_du_per_ac"].iloc[0] > r2["new_density_du_per_ac"].iloc[0]

    def test_multiple_apn_aggregation(self):
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
                    "bbox": (-118.0001, 33.9999, -117.9999, 34.0001),
                },
                {
                    "APN20": "2",
                    "current_density_du_per_ac": 20,
                    "ZN19_CITY": "",
                    "ZN19_SCAG": 0,
                    "CITY": "Other",
                    "COUNTY": "LA",
                    "Tier": 1,
                    "bbox": (-118.00015, 33.99985, -117.99985, 34.00015),
                },
            ]
        )
        result = compute_dwelling_units(stops, parcels)
        assert len(result) >= 1
        apns = set(result["APN20"])
        assert "1" in apns or "2" in apns
        assert "area_acres" in result.columns

    def test_additional_du_nonnegative(self):
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
        result = compute_dwelling_units(stops, parcels)
        assert result["additional_du"].iloc[0] >= 0
