# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "auto-mix-prep>=0.2.0",
#     "folium>=0.12",
#     "geopandas>=1.1.3",
#     "mapclassify>=2.10.0",
#     "marimo>=0.23.3",
#     "matplotlib>=3.10.9",
# ]
# ///

import marimo

__generated_with = "0.23.5"
app = marimo.App()

with app.setup(hide_code=True):
    import csv
    import io
    import json
    import tempfile
    import time
    import zipfile
    from collections import defaultdict
    from collections.abc import Generator
    from pathlib import Path
    from typing import Any

    import geopandas as gpd
    import numpy as np
    import pandas as pd
    import requests
    import shapely
    from shapely import unary_union
    from shapely.geometry import Polygon

    __generated_with = "0.23.5"
    SCAG_PARCELS_URL = "https://rdp.scag.ca.gov/mapping/rest/services/Housing/2020_Annual_Land_Use/MapServer/0/query"
    CA_PARCELS_URL = (
        "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/ArcGIS/rest/services/"
        "CA_Statewide_Parcels_Public_view/FeatureServer/0/query"
    )

    SCAG_OUT_FIELDS = (
        "APN20,COUNTY,CITY,IL_RATIO,ZN19_CITY,ZN19_SCAG,TCAC_2024,"
        "APPAREL1MI,EDUC1MI,GROCERY1MI,HOSPIT1MI,RESTAUR1MI,JOBS_30MIN,YEAR"
    )

    HALF_MI_M = 804.7  # 0.5 mile in metres

    # Zone densities (du/ac) by buffer zone and Tier
    ZONE_DENSITIES: dict[tuple[str, int], float] = {
        ("200ft", 1): 160,
        ("200ft", 2): 140,
        ("qtr_mi", 1): 120,
        ("qtr_mi", 2): 100,
        ("half_mi", 1): 100,
        ("half_mi", 2): 80,
    }

    def shapely_to_esri_json(polygon: Polygon, wkid: int = 3857) -> dict | None:
        """Convert a Shapely polygon to an ESRI JSON geometry object."""
        if not polygon or polygon.is_empty:
            return None
        coords = list(polygon.exterior.coords)
        rings = [[[x, y] for x, y in coords]]
        return {"rings": rings, "spatialReference": {"wkid": wkid}}

    def paginate_arcgis(
        url: str,
        geometry: str = None,
        geometry_type: str = "esriGeometryPolygon",
        spatial_rel: str = "esriSpatialRelIntersects",
        in_sr: int = 3310,
        out_fields: str = "*",
        return_geometry: str = "true",
        f: str = "geojson",
        where: str = "1=1",
        max_record_count: int = 2000,
        delay: float = 0,
    ) -> Generator[gpd.GeoDataFrame, None, None]:
        """Paginate through an ArcGIS REST API query and yield GeoDataFrames."""
        offset = 0
        while True:
            params = {
                "geometry": geometry,
                "geometryType": geometry_type,
                "spatialRel": spatial_rel,
                "inSR": str(in_sr),
                "outFields": out_fields,
                "returnGeometry": return_geometry,
                "f": f,
                "where": where,
                "resultOffset": str(offset),
                "resultRecordCount": str(max_record_count),
            }
            resp = requests.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])
            if not features:
                break
            page = gpd.GeoDataFrame.from_features(features)
            yield page
            offset += max_record_count
            time.sleep(delay)
            if not data.get("properties", {}).get("exceededTransferLimit", False):
                break

    def fetch_from_arcgis(
        url: str,
        geometries: list[Polygon] = None,
        out_fields: str = "*",
        wkid: int = 3310,
        max_record_count: int = 2000,
        delay: float = 0,
    ) -> gpd.GeoDataFrame:
        """Query an ArcGIS parcel endpoint for multiple geometries and concatenate results."""
        if geometries is None:
            geometries = [None]
        all_geometries: list[gpd.GeoDataFrame] = []
        for geom in geometries:
            esri_geom = shapely_to_esri_json(geom, wkid=wkid)
            geometry_json = json.dumps(esri_geom) if esri_geom else None
            for page in paginate_arcgis(
                url=url,
                geometry=geometry_json,
                geometry_type="esriGeometryPolygon",
                spatial_rel="esriSpatialRelIntersects",
                in_sr=wkid,
                out_fields=out_fields,
                return_geometry="true",
                f="geojson",
                where="1=1",
                max_record_count=max_record_count,
                delay=delay,
            ):
                all_geometries.append(page)
        if not all_geometries:
            return gpd.GeoDataFrame()
        return gpd.GeoDataFrame(pd.concat(all_geometries, ignore_index=True))

    def parse_gtfs_zip(
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

    def create_half_mi_buffers(stops_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Create 0.5-mile (804.7 m) circular buffers around each stop.

        Returns a GeoDataFrame in EPSG:3310 with the same ``stop_id`` index.
        """
        buffers = gpd.GeoDataFrame(
            {"stop_id": stops_gdf["stop_id"].values},
            geometry=stops_gdf.to_crs("EPSG:3310").geometry.buffer(HALF_MI_M, resolution=8),
            crs="EPSG:3310",
        )
        return buffers

    def fetch_scag_parcels(
        buffers_gdf: gpd.GeoDataFrame,
        url: str = SCAG_PARCELS_URL,
        out_fields: str = SCAG_OUT_FIELDS,
        wkid: int = 3310,
        max_record_count: int = 2000,
        delay: float = 0.1,
    ) -> gpd.GeoDataFrame:
        """Query the SCAG parcel endpoint for all parcels intersecting the buffers.

        Returns a GeoDataFrame in EPSG:3310.
        """
        parcels = fetch_from_arcgis(
            url=url,
            geometries=buffers_gdf.geometry.tolist(),
            out_fields=out_fields,
            wkid=wkid,
            max_record_count=max_record_count,
            delay=delay,
        )
        if parcels.empty:
            return parcels
        if "APN20" in parcels.columns:
            parcels = parcels.groupby("APN20", as_index=False).first()

        return parcels.set_crs("EPSG:4326").to_crs(f"EPSG:{wkid}")

    def fetch_ca_parcels(
        buffers_gdf: gpd.GeoDataFrame,
        url: str = CA_PARCELS_URL,
        wkid: int = 3310,
        max_record_count: int = 2000,
        delay: float = 0.1,
    ) -> gpd.GeoDataFrame:
        """Query the CA statewide parcel endpoint for all parcels intersecting the buffers.

        Returns a GeoDataFrame in EPSG:3310, deduplicated by ``PARCEL_APN``.
        """
        parcels = fetch_from_arcgis(
            url=url,
            geometries=buffers_gdf.geometry.tolist(),
            out_fields="*",
            wkid=wkid,
            max_record_count=max_record_count,
            delay=delay,
        )
        if parcels.empty:
            return parcels

        # Deduplicate by APN
        if "PARCEL_APN" in parcels.columns:
            before = len(parcels)  # noqa: F841
            parcels = parcels.groupby("PARCEL_APN", as_index=False).first()
        return parcels.set_crs("EPSG:4326").to_crs(f"EPSG:{wkid}")

    def compute_scag_density(scag_parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Add ``current_density_du_per_ac`` to SCAG parcels using LA / non-LA logic.

        For LA parcels, first tries ``ZN19_CITY`` zone codes. If no match is found,
        falls through to try ``ZN19_SCAG`` codes. Parcels that don't match any code
        get ``NaN`` (excluded from downstream calculations).

        Parameters
        ----------
        scag_parcels:
            Must have columns ``ZN19_CITY``, ``ZN19_SCAG``, ``CITY``, and a valid
            geometry for area calculations.

        Returns
        -------
        gpd.GeoDataFrame
            Same as input with an added ``current_density_du_per_ac`` column.
        """
        df = scag_parcels.copy()

        # --- LA density: map ZN19_CITY zone codes → du/ac ---
        la_density = df["ZN19_CITY"].case_when(
            [
                (df["ZN19_CITY"].str.contains("RD1.5", na=False), 43560 / 1500),
                (df["ZN19_CITY"].str.contains("RD2", na=False), 43560 / 2000),
                (df["ZN19_CITY"].str.contains("RD3", na=False), 43560 / 3000),
                (df["ZN19_CITY"].str.contains("RD4", na=False), 43560 / 4000),
                (df["ZN19_CITY"].str.contains("RD5", na=False), 43560 / 5000),
                (df["ZN19_CITY"].str.contains("RD6", na=False), 43560 / 6000),
                (df["ZN19_CITY"].str.contains("RMP", na=False), 43560 / 20000),
                (df["ZN19_CITY"].str.contains("R3", na=False), 43560 / 800),
                (df["ZN19_CITY"].str.contains("RAS3", na=False), 43560 / 800),
                (df["ZN19_CITY"].str.contains("R4", na=False), 43560 / 400),
                (df["ZN19_CITY"].str.contains("RAS4", na=False), 43560 / 400),
                (df["ZN19_CITY"].str.contains("R5", na=False), 43560 / 200),
                (df["ZN19_CITY"].str.contains("RE40", na=False), 43560 / 40000),
                (df["ZN19_CITY"].str.contains("RE20", na=False), 43560 / 20000),
                (df["ZN19_CITY"].str.contains("RE15", na=False), 43560 / 15000),
                (df["ZN19_CITY"].str.contains("RE11", na=False), 43560 / 11000),
                (df["ZN19_CITY"].str.contains("RE9", na=False), 43560 / 9000),
                (df["ZN19_CITY"].str.contains("RS", na=False), 43560 / 7500),
                (df["ZN19_CITY"].str.contains("R1", na=False), 43560 / 5000),
                (df["ZN19_CITY"].str.contains("RU", na=False), 43560 / 3500),
                (df["ZN19_CITY"].str.contains("RZ2.5", na=False), 43560 / 2500),
                (df["ZN19_CITY"].str.contains("RZ3", na=False), 43560 / 3000),
                (df["ZN19_CITY"].str.contains("RZ4", na=False), 43560 / 4000),
                (df["ZN19_CITY"].str.contains("RW1", na=False), 43560 / 2300),
                (df["ZN19_CITY"].str.contains("R2", na=False), 43560 / 2500),
                (df["ZN19_CITY"].str.contains("RW2", na=False), 43560 / 1150),
                (df["ZN19_CITY"].str.contains("C1", na=False), 100),
                (df["ZN19_CITY"].str.contains("C2", na=False), 43560 / 400),
                (df["ZN19_CITY"].str.contains("C3", na=False), 110),
                (df["ZN19_CITY"].str.contains("C4", na=False), 200),
            ]
        )

        # --- Non-LA / fallback density: map ZN19_SCAG codes → du/ac ---
        zn19_scag = df["ZN19_SCAG"].astype(str)
        area_acres = df.to_crs("EPSG:2229").area / 43560
        scag_density = zn19_scag.case_when(
            [
                (zn19_scag == "1110", 1 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1111", 1 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1112", 1 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1113", 1 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1121", 3 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1122", 3 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1140", 3 / area_acres.replace(0, np.nan)),
                (zn19_scag == "1123", 18),
                (zn19_scag == "1124", 60),
                (zn19_scag == "1125", 80),
                (zn19_scag == "1131", 6),
                (zn19_scag == "1150", 1),
                (zn19_scag == "1150", 1),
                (zn19_scag == "1600", 40),
                (zn19_scag == "1610", 40),
                (zn19_scag == "1620", 30),
                (zn19_scag.isin(["1220", "1221", "1222"]), 47),
                (zn19_scag.isin(["2000", "2100", "2200", "2300", "2400", "2500", "2600", "2700"]), 1 / 5),
                (zn19_scag.isin(["1900", "7777", "1500", "1233", "1210", "1211", "1212", "1213", "1247"]), 0),
                (zn19_scag == zn19_scag, np.nan),
            ]
        )

        # For LA parcels: use ZN19_CITY density if matched, otherwise fall through to SCAG
        is_la = df["CITY"] == "Los Angeles"

        df["current_density_du_per_ac"] = scag_density
        df.loc[is_la, "current_density_du_per_ac"] = la_density
        df["current_density_du_per_ac"] = pd.to_numeric(df["current_density_du_per_ac"], errors="coerce")
        return df

    def trim_around_stations(
        parcels_gdf: gpd.GeoDataFrame,
        stops_gdf: gpd.GeoDataFrame,
        buffer_dist_ft: int,
    ) -> gpd.GeoDataFrame:
        """Buffer stops by ``buffer_dist_ft`` and trim parcels to the buffer.

        Parameters
        ----------
        parcels_gdf:
            Parcels with ``current_density_du_per_ac`` and ``Tier`` columns.
        stops_gdf:
            Stops with ``stop_id``, ``Tier``, and geometry.
        buffer_dist_ft:
            Buffer distance in feet (e.g. 200, 1320, 2640).

        Returns
        -------
        gpd.GeoDataFrame
            Parcel pieces clipped to the buffer, with ``buffer_zone_id`` set to
            ``"200ft"``, ``"qtr_mi"``, or ``"half_mi"`` depending on the distance.
        """
        # Map distance to zone name
        zone_map = {200: "200ft", 1320: "qtr_mi", 2640: "half_mi"}
        zone_name = zone_map.get(buffer_dist_ft, f"{buffer_dist_ft}ft")

        # Buffer stops in feet (EPSG:2229)
        buffer_df = gpd.GeoDataFrame(
            {"stop_id": stops_gdf["stop_id"].values, "Tier": stops_gdf["Tier"].values},
            geometry=stops_gdf.to_crs("EPSG:2229").geometry.buffer(buffer_dist_ft),
            crs="EPSG:2229",
        )

        # Spatial join: parcels intersecting buffers
        parcels_4326 = parcels_gdf.to_crs("EPSG:4326")
        buffer_4326 = buffer_df.to_crs("EPSG:4326")
        joined = gpd.sjoin(
            parcels_4326,
            buffer_4326[["stop_id", "Tier", "geometry"]],
            how="inner",
            predicate="intersects",
            rsuffix="_stop",
        )

        # Normalize Tier column name after sjoin
        # - If parcels had Tier, sjoin renames buffer's Tier to Tier__stop
        #   and parcels' Tier to Tier_left (geopandas 1.x behavior)
        # - If parcels didn't have Tier, sjoin keeps buffer's Tier as-is
        if "Tier_left" in joined.columns:
            # Parcels had Tier; use the buffer's Tier (Tier__stop)
            joined["Tier"] = joined["Tier__stop"]
            joined.drop(columns=["Tier_left", "Tier__stop"], inplace=True)
        elif "Tier__stop" in joined.columns:
            # Parcels didn't have Tier; rename buffer's Tier back
            joined.rename(columns={"Tier__stop": "Tier"}, inplace=True)

        # Clip to buffer boundary
        trimmed = gpd.overlay(joined, buffer_4326[["stop_id", "geometry"]], how="intersection")
        trimmed["buffer_zone_id"] = zone_name
        return trimmed

    def create_buffer_donuts(
        stops_gdf: gpd.GeoDataFrame,
        scag_density: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Create 200ft / ¼mi / ½mi donut zones and compute weighted density per parcel.

        Steps
        -----
        1. ``trim_around_stations`` for 200 ft, 1320 ft, 2640 ft.
        2. Create donuts: ¼mi \\\\ 200ft, ½mi \\\\ ¼mi.
        3. Concat all three zones.
        4. Assign zone density by ``(buffer_zone_id, Tier)``.
        5. Group by ``APN20`` → weighted ``new_density_du_per_ac``,
        ``new_dwelling_units``, ``current_dwelling_units``.

        Returns
        -------
        gpd.GeoDataFrame
            One row per APN20 with columns: ``APN20``, ``geometry``, ``area_acres``,
            ``new_density_du_per_ac``, ``new_dwelling_units``,
            ``current_dwelling_units``, ``current_density_du_per_ac``, ``Tier``,
            ``ZN19_CITY``, ``ZN19_SCAG``, ``COUNTY``, ``CITY``.
        """
        # --- 1. Trim for each buffer distance ---
        trim_200 = trim_around_stations(scag_density, stops_gdf, 200)
        trim_qtr = trim_around_stations(scag_density, stops_gdf, 1320)
        trim_half = trim_around_stations(scag_density, stops_gdf, 2640)

        # --- 2. Create donuts ---
        # ¼mi buffer in EPSG:4326 for overlay
        buffer_qtr_4326 = gpd.GeoDataFrame(
            {"stop_id": stops_gdf["stop_id"].values},
            geometry=stops_gdf.to_crs("EPSG:2229").geometry.buffer(1320).to_crs("EPSG:4326"),
            crs="EPSG:4326",
        )

        buffer_200_4326 = gpd.GeoDataFrame(
            {"stop_id": stops_gdf["stop_id"].values},
            geometry=stops_gdf.to_crs("EPSG:2229").geometry.buffer(200).to_crs("EPSG:4326"),
            crs="EPSG:4326",
        )

        qtrmile_donut = gpd.overlay(trim_qtr, buffer_200_4326, how="difference")
        halfmile_donut = gpd.overlay(trim_half, buffer_qtr_4326, how="difference")

        qtrmile_donut["buffer_zone_id"] = "qtr_mi"
        halfmile_donut["buffer_zone_id"] = "half_mi"

        # --- 3. Concat ---
        residential = gpd.GeoDataFrame(pd.concat([trim_200, qtrmile_donut, halfmile_donut], ignore_index=True))

        # --- 4. Area in sq ft ---
        residential["area_sqft"] = residential.to_crs("EPSG:2229").area

        # --- 5. Assign zone density ---
        zone_densities_series = pd.Series(ZONE_DENSITIES)
        residential["zone_density"] = residential.set_index(["buffer_zone_id", "Tier"]).index.map(zone_densities_series)

        # --- 6. Aggregate by APN20 (vectorized) ---
        # Total area per APN
        area_agg = residential.groupby("APN20")["area_sqft"].sum().rename("area_sqft_total")
        area_acres = area_agg / 43560

        # Weighted density: sum(area_sqft * zone_density) / total_area
        residential["area_x_density"] = residential["area_sqft"] * residential["zone_density"]
        weighted_sum = residential.groupby("APN20")["area_x_density"].sum()

        # Union geometry per APN
        geom_agg = residential.groupby("APN20")["geometry"].agg(unary_union)

        # First values for other columns
        first_agg = residential.groupby("APN20").first()[
            [
                "current_density_du_per_ac",
                "Tier",
                "ZN19_CITY",
                "ZN19_SCAG",
                "COUNTY",
                "CITY",
                "buffer_zone_id",
            ]
        ]
        weighted_density = np.where(
            (area_agg > 0) & (first_agg["current_density_du_per_ac"].notna()), weighted_sum / area_agg, np.nan
        )

        # Build result
        by_apn = gpd.GeoDataFrame(
            {
                "APN20": area_agg.index,
                "geometry": geom_agg.values,
                "area_acres": area_acres.values,
                "new_density_du_per_ac": weighted_density,
                "new_dwelling_units": weighted_density * area_acres.values,
                "current_dwelling_units": first_agg["current_density_du_per_ac"].values * area_acres.values,
                "current_density_du_per_ac": first_agg["current_density_du_per_ac"].values,
                "Tier": first_agg["Tier"].values,
                "ZN19_CITY": first_agg["ZN19_CITY"].values,
                "ZN19_SCAG": first_agg["ZN19_SCAG"].values,
                "COUNTY": first_agg["COUNTY"].values,
                "CITY": first_agg["CITY"].values,
                "buffer_zone_id": first_agg["buffer_zone_id"].values,
            },
            geometry="geometry",
            crs=residential.crs,
        )
        by_apn["additional_du"] = np.maximum(
            0,
            by_apn.get("new_dwelling_units", 0) - by_apn.get("current_dwelling_units", 0),
        )
        return by_apn

    def join_scag_ca_parcels(
        scag_by_apn: gpd.GeoDataFrame,
        ca_parcels: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Outer-join SCAG zoning data onto CA parcels by APN.

        Returns a GeoDataFrame with combined geometry, city, county, and APN.
        Columns are renamed: ``current_density_du_per_ac`` → ``current_density``,
        ``new_density_du_per_ac`` → ``new_density``.
        """
        scag_cols = [
            "APN20",
            "ZN19_CITY",
            "ZN19_SCAG",
            "new_dwelling_units",
            "current_dwelling_units",
            "new_density_du_per_ac",
            "current_density_du_per_ac",
            "area_acres",
            "CITY",
            "COUNTY",
            "Tier",
            "geometry",
        ]
        existing = [c for c in scag_cols if c in scag_by_apn.columns]

        merged = ca_parcels.merge(
            scag_by_apn[existing],
            left_on="PARCEL_APN",
            right_on="APN20",
            how="outer",
            suffixes=("_ca", "_scag"),
        )

        matched = merged["APN20"].notna().sum()
        print(f"CA parcels: {len(ca_parcels)}")
        print(f"Matched with SCAG zoning: {matched} ({matched / len(ca_parcels) * 100:.1f}%)" if len(ca_parcels) else "")

        # Combine geometries
        geom_cols = [c for c in merged.columns if c.startswith("geometry")]
        if geom_cols:
            merged["geometry"] = merged[geom_cols[0]]
            for c in geom_cols[1:]:
                merged["geometry"] = merged["geometry"].combine_first(merged[c])

        # Combine city / county / apn
        merged["city"] = merged.get("SITE_CITY", pd.Series(dtype=str)).combine_first(
            merged.get("CITY", pd.Series(dtype=str)).str.upper()
        )
        merged["county"] = merged.get("COUNTYNAME", pd.Series(dtype=str)).combine_first(
            merged.get("COUNTY", pd.Series(dtype=str)).str.upper()
        )
        merged["apn"] = merged["PARCEL_APN"].combine_first(merged["APN20"])

        return gpd.GeoDataFrame(merged, geometry="geometry", crs=ca_parcels.crs)

    def assign_nearest_stop(
        parcels_gdf: gpd.GeoDataFrame,
        stops_gdf: gpd.GeoDataFrame,
        buffers_gdf: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Spatially join parcels to stop buffers, then deduplicate multi‑buffer parcels
        by assigning the nearest stop (by centroid distance).

        Parcels are clipped to their assigned buffer so only the intersecting
        portion is counted.

        Returns
        -------
        gpd.GeoDataFrame
            One row per ``(stop_id, APN)`` with ``clipped_geom`` as the active geometry.
        """
        # Reproject once
        parcels_3310 = parcels_gdf.to_crs("EPSG:3310")
        buffers_3310 = buffers_gdf.to_crs("EPSG:3310")

        # Spatial join: parcels → buffers
        joined = gpd.sjoin(
            parcels_3310,
            buffers_3310[["stop_id", "geometry"]],
            how="left",
            predicate="intersects",
        )

        # Identify parcels intersecting multiple stops
        multi = joined.groupby(joined.index).size()
        multi_ids = multi[multi > 1].index

        # Deduplicate: keep first stop_id as fallback
        deduped = joined[~joined.index.duplicated(keep="first")].copy()

        if len(multi_ids) > 0:
            # Vectorized nearest‑stop assignment
            multi_rows = joined.loc[multi_ids].copy()
            centroids_3310 = parcels_3310.loc[multi_ids].geometry.centroid
            stops_3310 = stops_gdf.set_index("stop_id").to_crs("EPSG:3310").geometry

            stop_points_for_rows = stops_3310.loc[multi_rows["stop_id"]].values
            centroid_array = centroids_3310.loc[multi_rows.index].values

            multi_rows["distance"] = shapely.distance(centroid_array, stop_points_for_rows)

            nearest_idx = multi_rows.groupby(multi_rows.index)["distance"].idxmin()
            nearest = multi_rows.loc[nearest_idx, ["stop_id"]]

            # Assign the nearest stop back
            deduped.loc[nearest.index, "stop_id"] = nearest["stop_id"]

        # Clip parcels to assigned buffer
        buffer_indexed = buffers_3310.set_index("stop_id")[["geometry"]]
        deduped["buffer_geom"] = deduped["stop_id"].map(buffer_indexed["geometry"])
        deduped["clipped_geom"] = deduped.geometry.intersection(deduped["buffer_geom"])

        # Ensure exactly one row per (stop_id, APN)
        apn_col = "PARCEL_APN" if "PARCEL_APN" in deduped.columns else "APN20"
        deduped = deduped.drop_duplicates(subset=["stop_id", apn_col], keep="first")

        # Set active geometry to clipped_geom and drop other geometry columns
        deduped = deduped.set_geometry("clipped_geom", crs="EPSG:3310")
        deduped = deduped.drop(columns=["geometry", "buffer_geom"], errors="ignore")

        return deduped

    def aggregate_parcels_to_stops(
        parcels_with_stops: gpd.GeoDataFrame,
        stops_gdf: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Sum parcel area and dwelling units per stop.

        For each stop:
        * ``parcel_acres`` — total clipped parcel area (using ``unary_union`` to
        avoid double-counting overlapping geometries).
        * ``additional_dwelling_units`` — sum of ``max(0, new_dwelling_units -
        current_dwelling_units)`` across parcels where new > current.
        * ``city``, ``county`` — mode (most common value) from associated parcels.

        Returns
        -------
        gpd.GeoDataFrame
            Stops with added columns ``parcel_acres``, ``additional_dwelling_units``,
            ``city``, ``county``.
        """
        # Parcel area per stop
        area_per_stop = (
            parcels_with_stops.groupby("stop_id")["clipped_geom"]
            .apply(lambda g: unary_union(g.tolist()).area / 4046.86)
            .reset_index()
        )
        area_per_stop.columns = ["stop_id", "parcel_acres"]

        # Additional dwelling units per stop (only where new > current)
        du_per_stop = (
            parcels_with_stops.groupby("stop_id")["additional_du"]
            .sum()
            .reset_index()
            .rename(columns={"additional_du": "additional_dwelling_units"})
        )

        # City / county from parcels (mode per stop)
        def _mode(series: pd.Series) -> str:
            return series.mode().iloc[0] if not series.mode().empty else ""

        city_per_stop = (
            parcels_with_stops.groupby("stop_id")["CITY"].agg(_mode).reset_index().rename(columns={"CITY": "city"})
        )
        county_per_stop = (
            parcels_with_stops.groupby("stop_id")["COUNTY"].agg(_mode).reset_index().rename(columns={"COUNTY": "county"})
        )

        # Merge back to stops
        result = stops_gdf.merge(area_per_stop, on="stop_id", how="left").fillna({"parcel_acres": 0})
        result = result.merge(du_per_stop, on="stop_id", how="left").fillna({"additional_dwelling_units": 0})
        result = result.merge(city_per_stop, on="stop_id", how="left").fillna({"city": ""})
        result = result.merge(county_per_stop, on="stop_id", how="left").fillna({"county": ""})

        return result

    def full_pipeline(
        gtfs_zip_path: str | Path,
        tier_overrides: dict[str, int] | None = None,
        out_path: str | Path | None = None,
        scag_url: str = SCAG_PARCELS_URL,
        ca_url: str = CA_PARCELS_URL,
        scag_out_fields: str = SCAG_OUT_FIELDS,
    ) -> dict[str, gpd.GeoDataFrame]:
        """Run the complete GTFS-to-parcels pipeline.

        Parameters
        ----------
        gtfs_zip_path:
            Path to a GTFS ``.zip`` file.
        tier_overrides:
            Optional ``{route_id: tier}`` overrides.
        out_path:
            If provided, save stops and parcels to GeoJSON files here.
        scag_url:
            SCAG parcel endpoint URL.
        ca_url:
            CA statewide parcel endpoint URL.
        scag_out_fields:
            Fields to request from the SCAG endpoint.

        Returns
        -------
        dict
            ``{"stops": stops_gdf, "parcels": ca_parcels_output}``
        """
        print("=== Step 1: Parse GTFS ===")
        stops = parse_gtfs_zip(gtfs_zip_path, tier_overrides=tier_overrides)
        print(f"  {len(stops)} stops with Tier 1 or 2")

        print("=== Step 2: Create 0.5-mi buffers ===")
        buffers = create_half_mi_buffers(stops)

        print("=== Step 3: Fetch SCAG parcels ===")
        scag = fetch_scag_parcels(buffers, url=scag_url, out_fields=scag_out_fields)
        print(f"  {len(scag)} SCAG parcels")

        print("=== Step 4: Compute SCAG density ===")
        scag_density = compute_scag_density(scag)

        print("=== Step 5: Create buffer donuts & weighted density ===")
        scag_by_apn = create_buffer_donuts(stops, scag_density)

        print("=== Step 6: Fetch CA parcels ===")
        ca = fetch_ca_parcels(buffers, url=ca_url)
        print(f"  {len(ca)} CA parcels")

        print("=== Step 7: Join SCAG → CA parcels ===")
        combined = join_scag_ca_parcels(scag_by_apn, ca)

        print("=== Step 8: Trim to 0.5-mi buffer & assign nearest stop ===")
        parcels_with_stops = assign_nearest_stop(combined, stops, buffers)

        print("=== Step 9: Aggregate parcels to stops ===")
        stops_result = aggregate_parcels_to_stops(parcels_with_stops, stops)

        result = {"stops": stops_result, "parcels": combined}

        if out_path:
            out = Path(out_path)
            out.mkdir(parents=True, exist_ok=True)
            stops_result.to_file(out / "stops.geojson", driver="GeoJSON")
            combined.to_file(out / "parcels.geojson", driver="GeoJSON")
            print(f"  Saved to {out}")

        return result


@app.cell(hide_code=True)
def _():
    from typing import Optional

    import folium
    import marimo as mo
    from folium.plugins import MarkerCluster, VectorGridProtobuf

    PARCELS_URL = "https://vectortileservices3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/VectorTileServer/tile/{z}/{y}/{x}.pbf"
    STOPS_URL = (
        "https://services3.arcgis.com/NaFf4UaPo3IgQXqn/ArcGIS/rest/services/sb79_transit_stations/FeatureServer/0/query"
    )
    print("https://github.com/dknomura/missing_metro/raw/refs/heads/main/notebooks/public/oc-streetcar_gtfs.zip")
    return (
        MarkerCluster,
        Optional,
        PARCELS_URL,
        STOPS_URL,
        VectorGridProtobuf,
        folium,
        mo,
    )


@app.cell(hide_code=True)
def _(mo):

    file_input = mo.ui.file(
        label="Upload a GTFS zip file",
        filetypes=[".zip"],
        multiple=False,
    )
    url_input = mo.ui.text(
        label="Or paste a URL to a GTFS zip",
        placeholder="https://example.com/gtfs.zip",
        value="https://github.com/dknomura/missing_metro/raw/refs/heads/main/notebooks/public/oc-streetcar_gtfs.zip",
    )

    mo.hstack([file_input, url_input], justify="start")
    return file_input, url_input


@app.cell(hide_code=True)
def _(Optional, file_input, url_input):
    # --- React to user input ---
    print("Getting GTFS zip file from github")

    def get_gtfs_bytes() -> Optional[bytes]:
        """Return GTFS bytes from whichever input the user used."""
        if file_input.value:
            # Uploaded file -> file_input.value is a list of named tuples (name, contents)
            return file_input.value[0].contents
        elif url_input.value.strip():
            url = url_input.value.strip()
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                return resp.content
            except Exception as e:
                raise ValueError(f"Failed to download GTFS from URL: {e}") from None
        return None

    gtfs_data = get_gtfs_bytes()

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(gtfs_data)
        tmp.flush()  # Ensure data is written to disk

        # Optional: Read back from the start
        new_stops = parse_gtfs_zip(tmp.name, tier_overrides={"route-mourbghe-3": 2})
    return (new_stops,)


@app.cell
def _(new_stops, prestops_gdf):
    # Creating GIS from GTFS and assigning a Tier
    # Tier 1: All subways and light rail or regional trains with more than 72 trains/day
    # Tier 2: All light rail < 72 trains/day or regional trains > 48 trains/day
    # All other transit stops are not covered by SB-79
    # OC street car stops are Tier 2
    def assign_tier_to_stops():
        Tier_1 = prestops_gdf[
            ((prestops_gdf["routetypes"].str.contains("2")) & (prestops_gdf["n_arrivals"] >= 72))
            | (prestops_gdf["routetypes"].str.contains("1"))
        ]
        Tier_2 = prestops_gdf[
            (
                (prestops_gdf["routetypes"].str.contains("2"))
                & ((prestops_gdf["n_arrivals"] < 72) & (prestops_gdf["n_arrivals"] >= 48))
            )
            | (prestops_gdf["routetypes"].str.contains("0"))
        ]
        Tier_1["Tier"] = 1
        Tier_2["Tier"] = 2
        stops_df = pd.concat([Tier_1, Tier_2])  # noqa: F841

    new_stops.explore("Tier", tiles="CartoDB positron")
    return


@app.cell
def _(new_stops):
    buffers = create_half_mi_buffers(new_stops)
    buffers.explore(tiles="CartoDB positron")
    return (buffers,)


@app.cell(hide_code=True)
def _(buffers):
    scag_parcels = fetch_scag_parcels(buffers_gdf=buffers)
    return (scag_parcels,)


@app.cell
def _(scag_parcels):
    # There are > 6000 parcels, which is too much to draw, can only show a subset, but calculations done on all parcels
    scag_parcels[2000:6000].explore(tiles="CartoDB positron")
    return


@app.cell(hide_code=True)
def _(new_stops, scag_parcels):
    scag_with_density = compute_scag_density(scag_parcels=scag_parcels)

    scag_with_dwelling_units = create_buffer_donuts(stops_gdf=new_stops, scag_density=scag_with_density)
    return (scag_with_dwelling_units,)


@app.cell
def _(
    buffer_200_4326,
    buffer_dist_ft,
    buffer_qtr_4326,
    parcels_gdf,
    scag_density,
    scag_with_dwelling_units,
    stops_gdf,
):
    # Create 200ft, quarter mi, and half mi zones around the stations
    def create_donuts():
        def trim_around_stations():
            buffer_df = stops_gdf.to_crs("EPSG:2229").geometry.buffer(buffer_dist_ft)
            joined = gpd.sjoin(  # noqa: F841
                parcels_gdf.to_crs("EPSG:4326"),
                buffer_df.to_crs("EPSG:4326"),
                predicate="intersects",
            )

        trim_200 = trim_around_stations(scag_density, stops_gdf, 200)
        trim_qtr = trim_around_stations(scag_density, stops_gdf, 1320)
        trim_half = trim_around_stations(scag_density, stops_gdf, 2640)
        qtrmile_donut = gpd.overlay(trim_qtr, buffer_200_4326, how="difference")
        halfmile_donut = gpd.overlay(trim_half, buffer_qtr_4326, how="difference")

        residential = gpd.GeoDataFrame(pd.concat([trim_200, qtrmile_donut, halfmile_donut], ignore_index=True))  # noqa: F841

    scag_with_dwelling_units[2000:6000].explore("buffer_zone_id", tiles="CartoDB positron")
    return


@app.cell
def _(scag_with_dwelling_units):
    # The new densities will be
    # 200ft: 120 du/ac
    # 1/4 mi: 100 du/ac
    # 1/2 mi: 80 du/ac
    scag_with_dwelling_units[2000:6000].explore("new_density_du_per_ac", tiles="CartoDB positron")
    return


@app.cell
def _(area_acres, df, scag_with_dwelling_units):
    # Translating zoning code to density units to get the density units
    # https://scag-spm-documentation.readthedocs.io/en/latest/scag_lu_codes_description/
    def calc_current_density():
        la_density = df["ZN19_CITY"].case_when(  # noqa: F841
            [
                (df["ZN19_CITY"] == "1122", 43560 / 1500),
                (df["ZN19_CITY"] == "1140", 3 / area_acres.replace(0, np.nan)),
                (df["ZN19_CITY"] == "1123", 18),
                (df["ZN19_CITY"] == "1124", 60),
            ]
        )

    scag_with_dwelling_units[2000:6000].explore("current_density_du_per_ac", tiles="CartoDB positron")
    return


@app.cell(hide_code=True)
def _(buffers, new_stops, scag_with_dwelling_units):
    parcels_with_stops = assign_nearest_stop(scag_with_dwelling_units, new_stops, buffers)

    stops_result = aggregate_parcels_to_stops(parcels_with_stops, new_stops)
    return parcels_with_stops, stops_result


@app.cell
def _(parcels_with_stops):
    # Showing the potential new dwelling units = new_du - current_du
    parcels_with_stops[2000:6000].explore("additional_du", tiles="CartoDB positron")
    return


@app.cell
def _(stops_result):
    print(f"Total potential dwelling units for OC street car: {int(stops_result['additional_dwelling_units'].sum())}")
    return


@app.cell(hide_code=True)
def _(STOPS_URL):
    stops = fetch_from_arcgis(url=STOPS_URL)
    stops = stops.set_crs("EPSG:4326")
    return (stops,)


@app.cell(hide_code=True)
def _(MarkerCluster, PARCELS_URL, VectorGridProtobuf, folium, stops):
    m = folium.Map(location=[34.0617140033952, -118.314146442073], tiles="CartoDB Positron", zoom_start=5)

    VectorGridProtobuf(PARCELS_URL, "folium_layer_name").add_to(m)
    cluster = MarkerCluster(disable_clustering_at_zoom=10).add_to(m)

    for _, _row in stops.iterrows():
        color = "blue" if _row["Tier"] == 2 else "red"
        cluster.add_child(
            folium.CircleMarker(
                location=[_row.geometry.y, _row.geometry.x],
                radius=5,
                tooltip=folium.Tooltip(
                    f"""
                    Stop Name: {_row["stop_name"]}<br>
                    Tier: {_row["Tier"]}<br>
                    Routes: {_row["route_ids_served"]}<br>
                    City: {_row["city"]}<br>
                    County: {_row["county"]}<br>
                    Route type: {_row["routetypes"]}<br>
                    Parcel acres: {_row["parcel_acres"]}"""
                ),
                color=color,
                fill_color=color,
            ).add_to(m)
        )
    return (m,)


@app.cell
def _(m):
    # We only need the GTFS transit schedules and this notebook will calculate
    # the potential that SB 79 has for California housing crisis
    m
    return


if __name__ == "__main__":
    app.run()
