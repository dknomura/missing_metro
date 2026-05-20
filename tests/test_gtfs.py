import gtfs_kit
import pandas as pd

from shared.api.gtfs import parent_stations_from_gtfs


def _make_feed(**tables) -> gtfs_kit.Feed:
    """Construct a minimal gtfs_kit Feed from raw table dicts."""
    feed = gtfs_kit.Feed(dist_units="km")
    for attr, rows in tables.items():
        setattr(feed, attr, pd.DataFrame(rows))
    return feed


TRIPS_S1A_S1B = [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk"} for i in range(6)]

STOPS_WITH_PARENT = [
    {
        "stop_id": "S1",
        "stop_name": "Main St",
        "stop_lat": 34.0,
        "stop_lon": -118.0,
        "location_type": 1,
        "parent_station": "",
    },
    {
        "stop_id": "S1A",
        "stop_name": "Main St NB",
        "stop_lat": 34.0,
        "stop_lon": -118.0,
        "location_type": 0,
        "parent_station": "S1",
    },
    {
        "stop_id": "S1B",
        "stop_name": "Main St SB",
        "stop_lat": 34.0,
        "stop_lon": -118.0,
        "location_type": 0,
        "parent_station": "S1",
    },
]


class TestParentStationsFromGtfs:
    def test_parent_station_with_children(self):
        """Child stop route info and trip counts are aggregated up to the parent."""
        # Arrange
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk"},
                {"route_id": "801", "trip_id": "t2", "service_id": "wk"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "S1A", "stop_sequence": 1},
                {"trip_id": "t2", "stop_id": "S1B", "stop_sequence": 1},
            ],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["stop_id"] == "S1"
        assert "801" in result.iloc[0]["route_ids"]
        assert result.iloc[0]["n_arrivals"] == 2

    def test_no_parent_stations_falls_back_to_boarding_stops(self):
        """When no location_type=1 stops exist, boarding stops are treated as parents."""
        # Arrange
        feed = _make_feed(
            stops=[
                {
                    "stop_id": "S1",
                    "stop_name": "Stop 1",
                    "stop_lat": 34.0,
                    "stop_lon": -118.0,
                    "location_type": 0,
                    "parent_station": "",
                },
            ],
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=[{"route_id": "801", "trip_id": "t1", "service_id": "wk"}],
            stop_times=[{"trip_id": "t1", "stop_id": "S1", "stop_sequence": 1}],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["stop_id"] == "S1"

    def test_trip_counts_summed_across_children(self):
        """n_arrivals reflects the sum of trips across all child stops, not just the parent."""
        # Arrange
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=TRIPS_S1A_S1B,
            stop_times=[
                *[{"trip_id": f"t{i}", "stop_id": "S1A", "stop_sequence": 1} for i in range(3)],
                *[{"trip_id": f"t{i}", "stop_id": "S1B", "stop_sequence": 1} for i in range(3, 6)],
            ],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["n_arrivals"] == 6

    def test_routes_merged_across_children(self):
        """route_ids, route_types, and agencies are unioned across all child stops."""
        # Arrange
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[
                {"route_id": "801", "route_type": 1, "agency_id": "metro"},
                {"route_id": "901", "route_type": 2, "agency_id": "metrolink"},
            ],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk"},
                {"route_id": "901", "trip_id": "t2", "service_id": "wk"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "S1A", "stop_sequence": 1},
                {"trip_id": "t2", "stop_id": "S1B", "stop_sequence": 1},
            ],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["route_ids"] == {"801", "901"}
        assert result.iloc[0]["route_types"] == {"1", "2"}
        assert result.iloc[0]["agencies"] == {"metro", "metrolink"}

    def test_stop_with_no_trips_excluded(self):
        """Parent stations with no associated trips are excluded from the result."""
        # Arrange
        feed = _make_feed(
            stops=[
                {
                    "stop_id": "S1",
                    "stop_name": "Served",
                    "stop_lat": 34.0,
                    "stop_lon": -118.0,
                    "location_type": 1,
                    "parent_station": "",
                },
                {
                    "stop_id": "S2",
                    "stop_name": "Unserved",
                    "stop_lat": 34.1,
                    "stop_lon": -118.1,
                    "location_type": 1,
                    "parent_station": "",
                },
            ],
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=[{"route_id": "801", "trip_id": "t1", "service_id": "wk"}],
            stop_times=[{"trip_id": "t1", "stop_id": "S1", "stop_sequence": 1}],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["stop_id"] == "S1"

    def test_multiple_parent_stations(self):
        """Each parent station is returned as a separate row with its own aggregated info."""
        # Arrange
        feed = _make_feed(
            stops=[
                {
                    "stop_id": "S1",
                    "stop_name": "Station 1",
                    "stop_lat": 34.0,
                    "stop_lon": -118.0,
                    "location_type": 1,
                    "parent_station": "",
                },
                {
                    "stop_id": "S2",
                    "stop_name": "Station 2",
                    "stop_lat": 34.1,
                    "stop_lon": -118.1,
                    "location_type": 1,
                    "parent_station": "",
                },
            ],
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk"},
                {"route_id": "801", "trip_id": "t2", "service_id": "wk"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "S1", "stop_sequence": 1},
                {"trip_id": "t2", "stop_id": "S2", "stop_sequence": 1},
            ],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 2
        assert set(result["stop_id"]) == {"S1", "S2"}

    def test_parent_served_directly_and_via_children(self):
        """Trips at the parent stop_id itself are counted alongside child trips."""
        # Arrange
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=[
                {"route_id": "801", "trip_id": "t1", "service_id": "wk"},
                {"route_id": "801", "trip_id": "t2", "service_id": "wk"},
            ],
            stop_times=[
                {"trip_id": "t1", "stop_id": "S1", "stop_sequence": 1},  # direct on parent
                {"trip_id": "t2", "stop_id": "S1A", "stop_sequence": 1},  # via child
            ],
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["n_arrivals"] == 2
