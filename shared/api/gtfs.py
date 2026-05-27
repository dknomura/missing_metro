import gtfs_kit
import pandas as pd


def parent_stations_from_gtfs(
    feed: gtfs_kit.Feed,
    days_of_week: list[int] | None = None,
) -> pd.DataFrame:
    """Return a DataFrame of parent stations with per-route aggregated info.

    Parameters
    ----------
    feed : gtfs_kit.Feed
        A GTFS feed object.
    days_of_week : list[int] | None, optional
        Days of the week to include, where 0=Monday, 6=Sunday.
        If None (default), all days in the feed's calendar are used.
        Examples: [0,1,2,3,4] for weekdays, [5,6] for weekends.

    Returns
    -------
    pd.DataFrame
        Parent stations with columns: stop_id, stop_name, stop_lat, stop_lon,
        route_ids (list), route_types (list), agencies (list),
        n_arrivals (list of avg trips/day per route).
    """
    all_dates = feed.get_dates(as_date_obj=True)
    if days_of_week is not None:
        all_dates = [d for d in all_dates if d.weekday() in days_of_week]
    if not all_dates:
        return pd.DataFrame()

    date_strings = [d.strftime("%Y%m%d") for d in all_dates]

    activity = feed.compute_trip_activity(date_strings)

    activity_long = activity.melt(
        id_vars=["trip_id"],
        var_name="date",
        value_name="active",
    )

    active_trips = activity_long[activity_long["active"] > 0]

    # Count trips per stop_id per route per date
    route_trip_counts = (
        active_trips[["trip_id", "date"]]
        .merge(feed.stop_times[["trip_id", "stop_id"]], on="trip_id")
        .merge(feed.trips[["trip_id", "route_id"]], on="trip_id")
        .merge(
            feed.routes[["route_id", "route_type"]],
            on="route_id",
        )
        .groupby(["stop_id", "route_id", "route_type", "date"])
        .size()
        .reset_index(name="num_trips")
    )

    if route_trip_counts.empty:
        return pd.DataFrame()

    route_avg = (
        route_trip_counts.groupby(["stop_id", "route_id", "route_type", "date"])["num_trips"]
        .sum()
        .groupby(["stop_id", "route_id", "route_type"])
        .mean()  # avg trips per day at this stop for this route
        .rename("avg_trips")
        .reset_index()
    )

    stops_df = feed.stops
    parent_stations = stops_df[stops_df["location_type"] == 1].copy()
    boarding_stops = stops_df[stops_df["location_type"] == 0].copy()

    if parent_stations.empty:
        parent_stations = boarding_stops.copy()
        boarding_stops = pd.DataFrame()

    # Build stop -> parent_id mapping
    self_map = parent_stations[["stop_id"]].assign(parent_id=parent_stations["stop_id"])
    child_map = (
        boarding_stops.dropna(subset=["parent_station"])[["stop_id", "parent_station"]].rename(
            columns={"parent_station": "parent_id"}
        )
        if not boarding_stops.empty
        else pd.DataFrame(columns=["stop_id", "parent_id"])
    )
    stop_to_parent = pd.concat([self_map, child_map], ignore_index=True)

    # Map each stop to its parent, deduplicate by (parent_id, route_id),
    # then aggregate into parallel lists per parent station.
    parent_route_info = (
        route_trip_counts[["stop_id", "route_id", "route_type"]]
        .drop_duplicates()
        .merge(stop_to_parent, on="stop_id")
        .merge(route_avg, on=["route_id", "route_type"])
        .drop_duplicates(subset=["parent_id", "route_id"])
        .groupby("parent_id")
        .agg(
            route_ids=("route_id", list),
            route_types=("route_type", lambda x: [str(t) for t in x]),
            n_arrivals=("avg_trips", list),
        )
        .reset_index()
        .rename(columns={"parent_id": "stop_id"})
    )

    return parent_stations.merge(parent_route_info, on="stop_id", how="inner")
