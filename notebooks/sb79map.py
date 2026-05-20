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

from shared.pipelines.sb79 import assign_tier_to_stops_from_gtfs, compute_scag_density

__generated_with = "0.23.5"
app = marimo.App()

with app.setup(hide_code=True):
    import tempfile
    from typing import Optional

    import folium
    import geopandas as gpd
    import mapclassify  # noqa: f401
    import marimo as mo
    import matplotlib  # noqa: f401
    import numpy as np
    import pandas as pd
    import requests
    import shapely
    from folium.plugins import MarkerCluster, VectorGridProtobuf
    from shapely import unary_union

    from shared.api.arcgis import fetch_from_arcgis
    from shared.utils.constants import CA_PARCELS_URL, HALF_MI_M, SCAG_OUT_FIELDS, SCAG_PARCELS_URL, ZONE_DENSITIES

    __generated_with = "0.23.5"

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

    PARCELS_URL = "https://vectortileservices3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/VectorTileServer/tile/{z}/{y}/{x}.pbf"
    STOPS_URL = (
        "https://services3.arcgis.com/NaFf4UaPo3IgQXqn/ArcGIS/rest/services/sb79_transit_stations/FeatureServer/0/query"
    )


@app.cell(hide_code=True)
def _():
    mo.md("""
    # Instructions for GTFS SB-79 analysis:
    1. If you do not have your own GTFS zip file [click here](https://github.com/dknomura/missing_metro/raw/refs/heads/main/notebooks/public/oc-streetcar_gtfs.zip) to download the GTFS zip for the OC streetcar
    2. GTFS file needs to be for a transit system in the SCAG region (LA, Orange, Riverside, San Bernardino, Ventura).
    """)  # noqa: E501
    return


@app.cell(hide_code=True)
def _():
    file_input = mo.ui.file(
        label="Upload a GTFS zip file",
        filetypes=[".zip"],
        multiple=False,
    )
    url_input = mo.ui.text(
        label="Or paste a URL to a GTFS zip",
        placeholder="https://example.com/gtfs.zip",
    )

    mo.hstack([file_input, url_input], justify="start")
    return file_input, url_input


@app.cell
def _(file_input, url_input):
    # --- React to user input ---
    mo.stop(not file_input.value, mo.md("⬆️ Upload a GTFS zip to continue"))

    def get_gtfs_bytes() -> bytes | None:
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
        new_stops = assign_tier_to_stops_from_gtfs(tmp.name, tier_overrides={"route-mourbghe-3": 2})
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
    buffers = gpd.GeoDataFrame(
        {"stop_id": new_stops["stop_id"].values},
        geometry=new_stops.to_crs("EPSG:3310").geometry.buffer(HALF_MI_M, resolution=8),
        crs="EPSG:3310",
    )
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
def _():
    stops = fetch_from_arcgis(url=STOPS_URL)
    stops = stops.set_crs("EPSG:4326")
    return (stops,)


@app.cell(hide_code=True)
def _(stops):
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
