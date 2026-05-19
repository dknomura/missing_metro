import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd


def assign_tier_to_stops_from_gtfs(
    gtfs_path: str | Path,
    tier_overrides: dict[str, int] | None = None,
) -> gpd.GeoDataFrame:
    """Parse a GTFS zip and return a GeoDataFrame of Tier-1/Tier-2 parent stations.

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
    z = zipfile.ZipFile(gtfs_path)

    routes = list(csv.DictReader(io.StringIO(z.read("routes.txt").decode("utf-8"))))
    route_info: dict[str, dict[str, Any]] = {}
    for r in routes:
        route_info[r["route_id"]] = r

    cal = list(csv.DictReader(io.StringIO(z.read("calendar.txt").decode("utf-8"))))
    weekday_services = {c["service_id"] for c in cal if c["monday"] == "1"}

    trips = list(csv.DictReader(io.StringIO(z.read("trips.txt").decode("utf-8"))))
    trip_to_route: dict[str, str] = {}
    route_dir_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"0": 0, "1": 0})
    for t in trips:
        trip_to_route[t["trip_id"]] = t["route_id"]
        if t["service_id"] in weekday_services:
            route_dir_counts[t["route_id"]][t["direction_id"]] += 1

    num_wkday = len(weekday_services)

    route_trains_per_day: dict[str, int] = {}
    for rid, dirs in route_dir_counts.items():
        route_trains_per_day[rid] = round((dirs.get("0", 0) + dirs.get("1", 0)) / num_wkday)

    stops = list(csv.DictReader(io.StringIO(z.read("stops.txt").decode("utf-8"))))
    stops_df = pd.DataFrame(stops)

    parent_stations = stops_df[stops_df["location_type"] == "1"].copy()
    boarding_stops = stops_df[stops_df["location_type"] == "0"].copy()

    # If there are no parent stations (location_type=1), treat all boarding
    # stops (location_type=0) as individual stations.  Some GTFS feeds
    # (e.g. OC Streetcar) only have location_type=0 stops with no parent
    # station hierarchy.
    if parent_stations.empty:
        parent_stations = boarding_stops.copy()
        boarding_stops = gpd.GeoDataFrame()  # no routes to propagate

    stop_times = list(csv.DictReader(io.StringIO(z.read("stop_times.txt").decode("utf-8"))))
    stop_to_routes: dict[str, set[str]] = defaultdict(set)
    for st in stop_times:
        rid = trip_to_route.get(st["trip_id"])
        if rid:
            stop_to_routes[st["stop_id"]].add(rid)

    parent_to_routes: dict[str, set[str]] = defaultdict(set)
    for _, bs in boarding_stops.iterrows():
        parent = bs["parent_station"] if "parent_station" in bs else None
        if parent and parent in set(parent_stations["stop_id"]):
            parent_to_routes[parent].update(stop_to_routes.get(bs["stop_id"], set()))

    for _, ps in parent_stations.iterrows():
        parent_to_routes[ps["stop_id"]].update(stop_to_routes.get(ps["stop_id"], set()))

    # --- Build the output GeoDataFrame ---
    records: list[dict[str, Any]] = []
    for _, ps in parent_stations.iterrows():
        pid = ps["stop_id"]
        served_routes = sorted(parent_to_routes.get(pid, set()))
        if not served_routes:
            continue

        # Collect route types and count arrivals
        types: set[str] = set()
        total_arrivals = 0
        agencies: set[str] = set()
        for rid in served_routes:
            ri = route_info.get(rid, {})
            types.add(ri.get("route_type", ""))
            total_arrivals += route_trains_per_day.get(rid, 0)
            agencies.add(ri.get("agency_id", ""))

        routetypes_str = ",".join(sorted(types, key=int))

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
                "agency": ",".join(sorted(agencies)),
                "route_ids_served": ",".join(served_routes),
                "routetypes": routetypes_str,
                "n_arrivals": total_arrivals,
            }
        )

    if not records:
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
