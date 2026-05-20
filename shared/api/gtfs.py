import gtfs_kit
import pandas as pd


def parent_stations_from_gtfs(
    feed: gtfs_kit.Feed,
    days_of_week: list[int] | None = None,
) -> pd.DataFrame:
    """Return a DataFrame of parent stations with aggregated route info and trip counts.

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
        route_ids, route_types, agencies, n_arrivals (avg trips/day).
    """
    all_dates = feed.get_dates(as_date_obj=True)
    if days_of_week is not None:
        all_dates = [d for d in all_dates if d.weekday() in days_of_week]
    if not all_dates:
        return pd.DataFrame()

    date_strings = [d.strftime("%Y%m%d") for d in all_dates]

    stop_stats = feed.compute_stop_stats(date_strings)[["date", "stop_id", "num_trips"]]

    n_active_dates = stop_stats["date"].nunique()
    if n_active_dates == 0:
        return pd.DataFrame()

    trip_counts = (
        stop_stats.assign(num_trips=lambda df: df["num_trips"].astype(int))
        .groupby("stop_id")["num_trips"]
        .sum()
        .div(n_active_dates)
        .reset_index(name="num_trips")
    )

    stop_info = (
        feed.stop_times.merge(feed.trips[["trip_id", "route_id"]], on="trip_id")
        .merge(feed.routes[["route_id", "route_type", "agency_id"]], on="route_id")
        .groupby("stop_id")
        .agg(
            route_ids=("route_id", lambda x: set(x)),
            route_types=("route_type", lambda x: set(str(t) for t in x)),
            agencies=("agency_id", lambda x: set(x)),
        )
        .reset_index()
    )

    stops_df = feed.stops
    parent_stations = stops_df[stops_df["location_type"] == 1].copy()
    boarding_stops = stops_df[stops_df["location_type"] == 0].copy()

    if parent_stations.empty:
        parent_stations = boarding_stops.copy()
        boarding_stops = pd.DataFrame()

    self_rows = parent_stations[["stop_id"]].assign(parent_id=parent_stations["stop_id"])
    child_rows = (
        boarding_stops.dropna(subset=["parent_station"])[["stop_id", "parent_station"]].rename(
            columns={"parent_station": "parent_id"}
        )
        if not boarding_stops.empty
        else pd.DataFrame(columns=["stop_id", "parent_id"])
    )
    membership = pd.concat([self_rows, child_rows], ignore_index=True)

    parent_route_info = (
        membership.merge(stop_info, on="stop_id", how="left")
        .merge(trip_counts, on="stop_id", how="left")
        .dropna(subset=["route_ids"])
        .groupby("parent_id")
        .agg(
            route_ids=("route_ids", lambda x: set().union(*x)),
            route_types=("route_types", lambda x: set().union(*x)),
            agencies=("agencies", lambda x: set().union(*x)),
            n_arrivals=("num_trips", "sum"),
        )
        .reset_index()
        .rename(columns={"parent_id": "stop_id"})
    )

    return parent_stations.merge(parent_route_info, on="stop_id", how="inner")
