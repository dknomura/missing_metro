import io
import tempfile
import zipfile
from pathlib import Path

from shared.pipelines.sb79 import assign_tier_to_stops_from_gtfs


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
            assert result.iloc[0]["routetypes"] == "1"
        finally:
            path.unlink(missing_ok=True)

    def test_tier1_light_rail_high_freq(self):
        """route_type=0 with n_arrivals >= 72 → Tier 1."""
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(40)
                ]
                + [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(40, 80)],
                "stop_times": [
                    {"trip_id": f"t{i}", "stop_id": "801S", "arrival_time": "08:00:00", "departure_time": "08:01:00"}
                    for i in range(80)
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
        path = Path(tempfile.gettempdir()) / "_test_gtfs2.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            # 80 trips / 1 weekday service = 80/day >= 72 → Tier 1
            assert result.iloc[0]["Tier"] == 1
        finally:
            path.unlink(missing_ok=True)

    def test_tier1_light_rail_high_freq_72plus(self):
        """route_type=0 with n_arrivals >= 72 → Tier 1."""
        gtfs_bytes = _make_gtfs_zip(
            {
                "routes": [{"route_id": "801", "route_type": "0", "agency_id": "metro"}],
                "trips": [
                    {"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "0"} for i in range(200)
                ]
                + [
                    {"route_id": "801", "trip_id": f"t{i}", "service_id": "wk", "direction_id": "1"} for i in range(200, 400)
                ],
                "stop_times": [
                    {"trip_id": f"t{i}", "stop_id": "801S", "arrival_time": "08:00:00", "departure_time": "08:01:00"}
                    for i in range(400)
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
        path = Path(tempfile.gettempdir()) / "_test_gtfs3.zip"

        path.write_bytes(gtfs_bytes)
        try:
            result = assign_tier_to_stops_from_gtfs(path)
            assert len(result) == 1
            # 400 trips / 5 days = 80/day >= 72 → Tier 1
            assert result.iloc[0]["Tier"] == 1
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
            # 60 trips, 48 <= 60 < 72 → Tier 2
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
            assert "1" in result.iloc[0]["routetypes"]
        finally:
            path.unlink(missing_ok=True)
