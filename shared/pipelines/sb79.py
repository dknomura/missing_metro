from pathlib import Path
from typing import Any

import geopandas as gpd
import gtfs_kit
import pandas as pd


def assign_tier_to_stops_from_gtfs(
    gtfs_path: str | Path,
    tier_overrides: dict[str, int] | None = None,
) -> gpd.GeoDataFrame:
    """Parse a GTFS zip and return a GeoDataFrame of Tier-1/Tier-2 parent stations.

    Uses ``gtfs_kit`` to read the feed and compute stop-level statistics.

    Parameters
    ----------
    gtfs_path:
        Path to a ``.zip`` file containing standard GTFS tables.
    tier_overrides:
        Optional mapping ``{route_id: tier}`` to force a specific tier for
        a route regardless of the automatic logic.

    Returns
    -------
    gpd.GeoDataFrame
        Only stops that qualify as Tier 1 or Tier 2 are returned.
    """
    feed = gtfs_kit.read_feed(gtfs_path, dist_units="km")

    # --- Pick a representative weekday ---
    first_week = feed.get_first_week()
    if not first_week:
        return _empty_result()
    weekday = first_week[0]  # Monday

    # --- Stop-level trip counts on that weekday ---
    stop_stats = feed.compute_stop_stats([weekday])
    # stop_stats columns: date, stop_id, num_trips, num_routes, ...
    trip_counts: dict[str, int] = {}
    for _, row in stop_stats.iterrows():
        trip_counts[row["stop_id"]] = int(row["num_trips"])

    # --- Build a mapping from stop_id → set of (route_id, route_type, agency_id) ---
    # Merge stop_times → trips → routes
    st = feed.stop_times
    trips = feed.trips[["trip_id", "route_id"]]
    routes = feed.routes[["route_id", "route_type", "agency_id"]]

    stop_route_info = (
        st.merge(trips, on="trip_id")
        .merge(routes, on="route_id")
        .groupby("stop_id")
        .agg(
            route_ids=("route_id", lambda x: ",".join(sorted(set(x)))),
            route_types=("route_type", lambda x: ",".join(sorted(set(str(t) for t in x)))),
            agencies=("agency_id", lambda x: ",".join(sorted(set(x)))),
        )
        .reset_index()
    )

    # --- Identify parent stations and child stops ---
    stops_df = feed.stops
    parent_stations = stops_df[stops_df["location_type"] == 1].copy()
    boarding_stops = stops_df[stops_df["location_type"] == 0].copy()

    # If there are no parent stations (location_type=1), treat all boarding
    # stops (location_type=0) as individual stations.
    if parent_stations.empty:
        parent_stations = boarding_stops.copy()
        boarding_stops = pd.DataFrame()  # no routes to propagate

    # --- Propagate routes from child stops up to parent stations ---
    # Build a lookup: stop_id → route info
    stop_info_lookup: dict[str, dict[str, Any]] = {}
    for _, row in stop_route_info.iterrows():
        stop_info_lookup[row["stop_id"]] = {
            "route_ids": row["route_ids"],
            "route_types": row["route_types"],
            "agencies": row["agencies"],
        }

    # For each parent station, collect routes from itself and its children
    parent_route_info: dict[str, dict[str, set[str]]] = {}
    for _, ps in parent_stations.iterrows():
        pid = ps["stop_id"]
        parent_route_info[pid] = {"route_ids": set(), "route_types": set(), "agencies": set()}

        # Routes directly serving the parent station
        if pid in stop_info_lookup:
            info = stop_info_lookup[pid]
            parent_route_info[pid]["route_ids"].update(info["route_ids"].split(","))
            parent_route_info[pid]["route_types"].update(info["route_types"].split(","))
            parent_route_info[pid]["agencies"].update(info["agencies"].split(","))

        # Routes from child stops
        if not boarding_stops.empty:
            child_stops = boarding_stops[boarding_stops["parent_station"] == pid]
            for _, cs in child_stops.iterrows():
                cid = cs["stop_id"]
                if cid in stop_info_lookup:
                    info = stop_info_lookup[cid]
                    parent_route_info[pid]["route_ids"].update(info["route_ids"].split(","))
                    parent_route_info[pid]["route_types"].update(info["route_types"].split(","))
                    parent_route_info[pid]["agencies"].update(info["agencies"].split(","))

    # --- Build the output GeoDataFrame ---
    records: list[dict[str, Any]] = []
    for _, ps in parent_stations.iterrows():
        pid = ps["stop_id"]
        info = parent_route_info.get(pid)
        if not info or not info["route_ids"]:
            continue

        served_routes = sorted(info["route_ids"])
        types = info["route_types"]
        agencies = sorted(info["agencies"])
        routetypes_str = ",".join(sorted(types, key=int))

        # Total arrivals for this stop on the representative weekday
        total_arrivals = trip_counts.get(pid, 0)

        # Tier assignment
        has_type_1 = "1" in types
        has_type_0 = "0" in types
        has_type_2 = "2" in types

        tier: int | None = None
        if has_type_1:
            tier = 1
        elif (has_type_0 or has_type_2) and total_arrivals >= 72:
            tier = 1
        elif has_type_2 and 48 <= total_arrivals < 72:
            tier = 2
        elif has_type_0 and total_arrivals < 72:
            tier = 2

        # Apply overrides
        if tier_overrides:
            for rid in served_routes:
                if rid in tier_overrides:
                    tier = tier_overrides[rid]
                    break

        if tier is None:
            continue  # skip stops that don't qualify

        records.append(
            {
                "stop_id": pid,
                "stop_name": ps.get("stop_name", ""),
                "stop_lat": float(ps["stop_lat"]),
                "stop_lon": float(ps["stop_lon"]),
                "Tier": tier,
                "agency": ",".join(agencies),
                "route_ids_served": ",".join(served_routes),
                "routetypes": routetypes_str,
                "n_arrivals": total_arrivals,
            }
        )

    if not records:
        return _empty_result()

    result = gpd.GeoDataFrame(
        records,
        geometry=gpd.points_from_xy(
            [r["stop_lon"] for r in records],
            [r["stop_lat"] for r in records],
            crs="EPSG:4326",
        ),
    )
    result.drop(columns=["stop_lat", "stop_lon"], inplace=True)
    return result


def _empty_result() -> gpd.GeoDataFrame:
    """Return an empty GeoDataFrame with the expected schema."""
    return gpd.GeoDataFrame(
        {
            "stop_id": pd.Series(dtype=str),
            "stop_name": pd.Series(dtype=str),
            "Tier": pd.Series(dtype=int),
            "agency": pd.Series(dtype=str),
            "route_ids_served": pd.Series(dtype=str),
            "routetypes": pd.Series(dtype=str),
            "n_arrivals": pd.Series(dtype=int),
        },
        geometry=pd.Series(dtype=object),
        crs="EPSG:4326",
    )
