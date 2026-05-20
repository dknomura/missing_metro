import gtfs_kit
import pandas as pd

from shared.api.gtfs import parent_stations_from_gtfs


def _make_feed(**tables) -> gtfs_kit.Feed:
    """Construct a minimal gtfs_kit Feed from raw table dicts."""
    feed = gtfs_kit.Feed(dist_units="km")
    for attr, rows in tables.items():
        setattr(feed, attr, pd.DataFrame(rows))
    return feed


CALENDAR_FULL_WEEK = [
    {
        "service_id": "wk",
        "monday": 1,
        "tuesday": 1,
        "wednesday": 1,
        "thursday": 1,
        "friday": 1,
        "saturday": 1,
        "sunday": 1,
        "start_date": "20250106",
        "end_date": "20250112",
    },
]

TRIPS_S1A_S1B = [{"route_id": "801", "trip_id": f"t{i}", "service_id": "wk"} for i in range(6)]

STOPA_W_PARENT = "S1A"
STOPB_W_PARENT = "S1B"
PARENT_STOP = "S1"
STOPS_WITH_PARENT = [
    {
        "stop_id": PARENT_STOP,
        "stop_name": "Main St",
        "stop_lat": 34.0,
        "stop_lon": -118.0,
        "location_type": 1,
        "parent_station": "",
    },
    {
        "stop_id": STOPA_W_PARENT,
        "stop_name": "Main St NB",
        "stop_lat": 34.0,
        "stop_lon": -118.0,
        "location_type": 0,
        "parent_station": "S1",
    },
    {
        "stop_id": STOPB_W_PARENT,
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
        trip1 = "t1"
        trip2 = "t2"
        route1 = "801"
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[{"route_id": route1, "route_type": 1, "agency_id": "metro"}],
            trips=[
                {"route_id": route1, "trip_id": trip1, "service_id": "wk"},
                {"route_id": route1, "trip_id": trip2, "service_id": "wk"},
            ],
            stop_times=[
                {
                    "trip_id": trip1,
                    "stop_id": STOPA_W_PARENT,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
                {
                    "trip_id": trip2,
                    "stop_id": STOPB_W_PARENT,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["stop_id"] == "S1"
        assert route1 in result.iloc[0]["route_ids"]
        assert result.iloc[0]["n_arrivals"] == 2

    def test_no_parent_stations_falls_back_to_boarding_stops(self):
        """When no location_type=1 stops exist, boarding stops are treated as parents."""
        # Arrange
        stop1 = "S1"
        trip1 = "t1"
        route1 = "801"
        feed = _make_feed(
            stops=[
                {
                    "stop_id": stop1,
                    "stop_name": "Stop 1",
                    "stop_lat": 34.0,
                    "stop_lon": -118.0,
                    "location_type": 0,
                    "parent_station": "",
                },
            ],
            routes=[{"route_id": route1, "route_type": 1, "agency_id": "metro"}],
            trips=[{"route_id": route1, "trip_id": trip1, "service_id": "wk"}],
            stop_times=[
                {
                    "trip_id": trip1,
                    "stop_id": stop1,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                }
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["stop_id"] == stop1

    def test_trip_counts_summed_across_children(self):
        """n_arrivals reflects the sum of trips across all child stops, not just the parent."""
        # Arrange
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[{"route_id": "801", "route_type": 1, "agency_id": "metro"}],
            trips=TRIPS_S1A_S1B,
            stop_times=[
                *[
                    {
                        "trip_id": f"t{i}",
                        "stop_id": STOPA_W_PARENT,
                        "stop_sequence": 1,
                        "arrival_time": "08:00:00",
                        "departure_time": "08:00:00",
                    }
                    for i in range(3)
                ],
                *[
                    {
                        "trip_id": f"t{i}",
                        "stop_id": STOPB_W_PARENT,
                        "stop_sequence": 1,
                        "arrival_time": "08:00:00",
                        "departure_time": "08:00:00",
                    }
                    for i in range(3, 6)
                ],
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["n_arrivals"] == 6

    def test_routes_merged_across_children(self):
        """route_ids, route_types, and agencies are unioned across all child stops."""
        # Arrange
        trip1 = "t1"
        trip2 = "t2"
        route1 = {"route_id": "route1", "route_type": 1, "agency_id": "agency1"}
        route2 = {"route_id": "route2", "route_type": 2, "agency_id": "agency2"}
        feed = _make_feed(
            stops=STOPS_WITH_PARENT,
            routes=[
                route1,
                route2,
            ],
            trips=[
                {"route_id": route1["route_id"], "trip_id": trip1, "service_id": "wk"},
                {"route_id": route2["route_id"], "trip_id": trip2, "service_id": "wk"},
            ],
            stop_times=[
                {
                    "trip_id": trip1,
                    "stop_id": STOPA_W_PARENT,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
                {
                    "trip_id": trip2,
                    "stop_id": STOPB_W_PARENT,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["route_ids"] == {route1["route_id"], route2["route_id"]}
        assert result.iloc[0]["route_types"] == {str(route1["route_type"]), str(route2["route_type"])}
        assert result.iloc[0]["agencies"] == {route1["agency_id"], route2["agency_id"]}

    def test_stop_with_no_trips_excluded(self):
        """Parent stations with no associated trips are excluded from the result."""
        # Arrange
        stop1 = "S1"
        feed = _make_feed(
            stops=[
                {
                    "stop_id": stop1,
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
            stop_times=[
                {
                    "trip_id": "t1",
                    "stop_id": "S1",
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                }
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["stop_id"] == stop1

    def test_multiple_parent_stations(self):
        """Each parent station is returned as a separate row with its own aggregated info."""
        # Arrange
        stop1 = "S1"
        stop2 = "S2"
        feed = _make_feed(
            stops=[
                {
                    "stop_id": stop1,
                    "stop_name": "Station 1",
                    "stop_lat": 34.0,
                    "stop_lon": -118.0,
                    "location_type": 1,
                    "parent_station": "",
                },
                {
                    "stop_id": stop2,
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
                {
                    "trip_id": "t1",
                    "stop_id": stop1,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
                {
                    "trip_id": "t2",
                    "stop_id": stop2,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 2
        assert set(result["stop_id"]) == {stop1, stop2}

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
                {
                    "trip_id": "t1",
                    "stop_id": PARENT_STOP,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },  # direct on parent
                {
                    "trip_id": "t2",
                    "stop_id": STOPA_W_PARENT,
                    "stop_sequence": 1,
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },  # via child
            ],
            calendar=CALENDAR_FULL_WEEK,
        )

        # Act
        result = parent_stations_from_gtfs(feed)

        # Assert
        assert len(result) == 1
        assert result.iloc[0]["n_arrivals"] == 2
