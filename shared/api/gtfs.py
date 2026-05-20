import gtfs_kit
import pandas as pd


def parent_stations_from_gtfs(feed: gtfs_kit.Feed) -> pd.DataFrame:
    """Return a DataFrame of parent stations with aggregated route info and trip counts."""
    trip_counts = feed.compute_stop_stats([feed.get_first_week()[0]])[["stop_id", "num_trips"]].assign(
        num_trips=lambda df: df["num_trips"].astype(int)
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
