# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo>=0.23.3",
#     "polars==1.30.0",
#     "altair==4.2.0",
#     "pandas==2.3.0",
# ]
# ///

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")

with app.setup:
    import json
    import time

    import geopandas as gpd
    import numpy as np
    import pandas as pd
    import requests

    SCAG_PARCELS = "https://rdp.scag.ca.gov/mapping/rest/services/Housing/2020_Annual_Land_Use/MapServer/0/query"
    CA_PARCELS = "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/ArcGIS/rest/services/CA_Statewide_Parcels_Public_view/FeatureServer/0/query"
    HALF_MI_M = 804.7
    STOPS_URL = r"https://services3.arcgis.com/NaFf4UaPo3IgQXqn/ArcGIS/rest/services/sb79_transit_stops/FeatureServer/0/query?where=0%3D0&objectIds=&geometry=&geometryType=esriGeometryEnvelope&inSR=&spatialRel=esriSpatialRelIntersects&resultType=none&distance=0.0&units=esriSRUnit_Meter&outDistance=&relationParam=&returnGeodetic=false&outFields=*&returnHiddenFields=false&returnGeometry=true&featureEncoding=esriDefault&multipatchOption=xyFootprint&maxAllowableOffset=&geometryPrecision=&outSR=&defaultSR=&datumTransformation=&applyVCSProjection=false&returnIdsOnly=false&returnUniqueIdsOnly=false&returnCountOnly=false&returnExtentOnly=false&returnQueryGeometry=false&returnDistinctValues=false&cacheHint=false&collation=&orderByFields=&groupByFieldsForStatistics=&returnAggIds=false&outStatistics=&having=&resultOffset=&resultRecordCount=&returnZ=false&returnM=false&returnTrueCurves=false&returnExceededLimitFeatures=true&quantizationParameters=&sqlFormat=none&f=pgeojson"
    OUT_PATH = r"C:\Users\dknom\code\missing_metro\data"


@app.cell
def _():
    stops_df = gpd.read_file(STOPS_URL)
    stops_df
    return (stops_df,)


@app.cell
def _(stops_df):
    def shapely_to_esri_json(polygon, wkid=3857):
        if polygon.is_empty:
            return None
        # Exterior ring
        coords = list(polygon.exterior.coords)
        rings = [[[x, y] for x, y in coords]]
        return {"rings": rings, "spatialReference": {"wkid": wkid}}

    parcels = gpd.GeoDataFrame()
    buffer = stops_df.copy()
    buffer.geometry = stops_df.to_crs("EPSG:3310").geometry.buffer(HALF_MI_M, resolution=8)

    requests_per_second = 10
    delay = 1.0 / requests_per_second
    max_record_count = 2000

    for _, _row in buffer.iterrows():
        _esri_geom = shapely_to_esri_json(_row.geometry, wkid=3310)
        _offset = 0

        while True:
            _params = {
                "geometry": json.dumps(_esri_geom),
                "geometryType": "esriGeometryPolygon",
                "spatialRel": "esriSpatialRelIntersects",
                "inSR": 3310,
                "outFields": "APN20,COUNTY,CITY,IL_RATIO,ZN19_CITY,ZN19_SCAG,TCAC_2024,"
                "APPAREL1MI,EDUC1MI,GROCERY1MI,HOSPIT1MI,RESTAUR1MI,JOBS_30MIN,YEAR",
                "returnGeometry": "true",
                "f": "geojson",
                "where": "1=1",
                "resultOffset": _offset,
                "resultRecordCount": max_record_count,
            }

            _resp = requests.get(SCAG_PARCELS, params=_params)
            _resp.raise_for_status()
            _data = _resp.json()

            _features = _data.get("features", [])
            if not _features:
                break

            _parcels = gpd.GeoDataFrame.from_features(_features)
            parcels = gpd.GeoDataFrame(pd.concat([parcels, _parcels], ignore_index=True))

            _offset += max_record_count
            time.sleep(delay)

            # Check if the server indicated there are more results
            if not _data.get("exceededTransferLimit", False):
                break

    parcels = parcels.set_crs("EPSG:4326").to_crs("EPSG:3310")
    parcels
    return (buffer,)


@app.cell
def _():
    clipped_parcels = gpd.read_file(OUT_PATH + r"\clipped_parcels.geojson", driver="GeoJSON")
    clipped_parcels
    return (clipped_parcels,)


@app.cell
def _(clipped_parcels):
    clipped_parcels
    return


@app.cell
def _():
    # Load the deduped CA parcels (already deduped by APN + clipped to buffer)
    ca_parcels = gpd.read_file(OUT_PATH + r"\deduped_parcels.geojson", driver="GeoJSON")
    ca_parcels
    return (ca_parcels,)


@app.cell
def _(clipped_parcels):
    # Deduplicate SCAG parcels by APN20 (same fix as CA parcels)
    scag_deduped = clipped_parcels.groupby("APN20", as_index=False).first().set_crs(clipped_parcels.crs)
    print(f"SCAG parcels: {len(clipped_parcels)} -> {len(scag_deduped)} after APN dedup")
    return (scag_deduped,)


@app.cell
def _(scag_deduped):
    # Compute density_limit on the deduped SCAG parcels
    la_density = scag_deduped.to_crs(epsg=2229)["ZN19_CITY"].case_when(
        [
            (scag_deduped["ZN19_CITY"].str.contains("RD1.5"), 43560 / 1500),
            (scag_deduped["ZN19_CITY"].str.contains("RD2"), 43560 / 2000),
            (scag_deduped["ZN19_CITY"].str.contains("RD3"), 43560 / 3000),
            (scag_deduped["ZN19_CITY"].str.contains("RD4"), 43560 / 4000),
            (scag_deduped["ZN19_CITY"].str.contains("RD5"), 43560 / 5000),
            (scag_deduped["ZN19_CITY"].str.contains("RD6"), 43560 / 6000),
            (scag_deduped["ZN19_CITY"].str.contains("RMP"), 43560 / 20000),
            (scag_deduped["ZN19_CITY"].str.contains("R3"), 43560 / 800),
            (scag_deduped["ZN19_CITY"].str.contains("RAS3"), 43560 / 800),
            (scag_deduped["ZN19_CITY"].str.contains("R4"), 43560 / 400),
            (scag_deduped["ZN19_CITY"].str.contains("RAS4"), 43560 / 400),
            (scag_deduped["ZN19_CITY"].str.contains("R5"), 43560 / 200),
            (scag_deduped["ZN19_CITY"].str.contains("RE40"), 43560 / 40000),
            (scag_deduped["ZN19_CITY"].str.contains("RE20"), 43560 / 20000),
            (scag_deduped["ZN19_CITY"].str.contains("RE15"), 43560 / 15000),
            (scag_deduped["ZN19_CITY"].str.contains("RE11"), 43560 / 11000),
            (scag_deduped["ZN19_CITY"].str.contains("RE9"), 43560 / 9000),
            (scag_deduped["ZN19_CITY"].str.contains("RS"), 43560 / 7500),
            (scag_deduped["ZN19_CITY"].str.contains("R1"), 43560 / 5000),
            (scag_deduped["ZN19_CITY"].str.contains("RU"), 43560 / 3500),
            (scag_deduped["ZN19_CITY"].str.contains("RZ2.5"), 43560 / 2500),
            (scag_deduped["ZN19_CITY"].str.contains("RZ3"), 43560 / 3000),
            (scag_deduped["ZN19_CITY"].str.contains("RZ4"), 43560 / 4000),
            (scag_deduped["ZN19_CITY"].str.contains("RW1"), 43560 / 2300),
            (scag_deduped["ZN19_CITY"].str.contains("R2"), 43560 / 2500),
            (scag_deduped["ZN19_CITY"].str.contains("RW2"), 43560 / 1150),
            (scag_deduped["ZN19_CITY"] != "Los Angeles", 0),
        ]
    )

    non_la_density = scag_deduped["ZN19_SCAG"].case_when(
        [
            (scag_deduped["ZN19_SCAG"] == 1111, 10),
            (scag_deduped["ZN19_SCAG"] == 1112, 8),
            (scag_deduped["ZN19_SCAG"] == 1113, 2),
            (scag_deduped["ZN19_SCAG"] == 1121, 3 / (scag_deduped.area / 4047)),
            (scag_deduped["ZN19_SCAG"] == 1122, 3 / (scag_deduped.area / 4047)),
            (scag_deduped["ZN19_SCAG"] == 1140, 3 / (scag_deduped.area / 4047)),
            (scag_deduped["ZN19_SCAG"] == 1123, 18),
            (scag_deduped["ZN19_SCAG"] == 1124, 60),
            (scag_deduped["ZN19_SCAG"] == 1125, 80),
            (scag_deduped["ZN19_SCAG"] == 1131, 6),
            (scag_deduped["ZN19_SCAG"] == 1150, 1),
            (scag_deduped["ZN19_SCAG"] != 0, 0),
        ]
    )

    scag_deduped["density_limit"] = np.where(scag_deduped["CITY"] == "Los Angeles", la_density, non_la_density)
    scag_deduped
    return


@app.cell
def _(scag_deduped, stops_df):
    def trim_around_stations(buffer_distance: int) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        buffer_df = stops_df.to_crs(epsg=2229)  # NAD83 / California zone 5 (feet)
        buffer_df.geometry = buffer_df.buffer(buffer_distance).to_crs(epsg=4326)

        temp_residential_df = scag_deduped.to_crs(epsg=4326)

        residential_in_buffer = gpd.sjoin(
            temp_residential_df, buffer_df, how="inner", rsuffix="_right", predicate="intersects"
        )
        trim_df = gpd.overlay(residential_in_buffer, buffer_df, how="intersection")
        print(f"Total residential parcels: {len(temp_residential_df)}")
        print(f"Residential parcels within {buffer_distance} feet of stations: {len(residential_in_buffer)}")
        return buffer_df, trim_df

    return (trim_around_stations,)


@app.cell
def _(trim_around_stations):
    # %%
    buffer_200ft_df, trim_200ft_df = trim_around_stations(200)
    trim_200ft_df.explore()
    return buffer_200ft_df, trim_200ft_df


@app.cell
def _(trim_around_stations):
    # %%
    buffer_halfmile_df, trim_halfmile_df = trim_around_stations(2640)
    trim_halfmile_df[:1000].explore()
    return (trim_halfmile_df,)


@app.cell
def _(trim_around_stations):

    # %%
    buffer_qtrmile_df, trim_qtrmile_df = trim_around_stations(1320)
    trim_qtrmile_df[:1000].explore()
    return buffer_qtrmile_df, trim_qtrmile_df


@app.cell
def _(buffer_qtrmile_df, trim_halfmile_df):
    # %%
    halfmile_donut = gpd.overlay(trim_halfmile_df, buffer_qtrmile_df, how="difference")
    halfmile_donut[:1000].explore()
    return (halfmile_donut,)


@app.cell
def _(buffer_200ft_df, trim_qtrmile_df):
    # %%
    qtrmile_donut = gpd.overlay(trim_qtrmile_df, buffer_200ft_df, how="difference")
    qtrmile_donut
    return (qtrmile_donut,)


@app.cell
def _(halfmile_donut):
    halfmile_donut
    return


@app.cell
def _(halfmile_donut, qtrmile_donut, trim_200ft_df):
    # %%
    trim_200ft_df["buffer_zone"] = "200ft"

    qtrmile_donut["buffer_zone"] = "qtr_mi"

    halfmile_donut["buffer_zone"] = "half_mi"

    # Concat all three zones
    residential_around_metro = gpd.GeoDataFrame(pd.concat([trim_200ft_df, qtrmile_donut, halfmile_donut]))

    # Calculate area in sqft per row (EPSG:2229 is in feet)
    residential_around_metro["area_sqft"] = residential_around_metro.to_crs(epsg=2229).area

    # Assign zone-specific density (du/acre) based on Tier and buffer zone
    # 200ft: Tier 1=160 du/ac, Tier 2=140 du/ac
    # Qtr mi: Tier 1=120 du/ac, Tier 2=100 du/ac
    # Half mi: Tier 1=100 du/ac, Tier 2=80 du/ac
    zone_densities = pd.Series(
        {
            ("200ft", 1): 160,
            ("200ft", 2): 140,
            ("qtr_mi", 1): 120,
            ("qtr_mi", 2): 100,
            ("half_mi", 1): 100,
            ("half_mi", 2): 80,
        }
    )
    residential_around_metro["zone_density"] = residential_around_metro.set_index(["buffer_zone", "Tier_1"]).index.map(
        zone_densities
    )

    # Now group by APN20 to get one row per parcel
    # Union geometries, sum area per zone, calculate weighted density
    from shapely import unary_union

    def aggregate_parcel(group):
        """Aggregate a single parcel's pieces across buffer zones."""
        apn = group.name
        total_area = group["area_sqft"].sum()
        total_area_acres = total_area / 43560

        # Weighted density = sum(zone_area * zone_density) / total_area
        weighted_density = (group["area_sqft"] * group["zone_density"]).sum() / total_area

        # Union all geometry pieces back together
        unioned_geom = unary_union(group.geometry.tolist())

        # Take first value for non-varying attributes
        first = group.iloc[0]

        return pd.Series(
            {
                "APN20": apn,
                "geometry": unioned_geom,
                "area_sqft": total_area,
                "area_acres": total_area_acres,
                "weighted_density": weighted_density,
                "dwelling_units_new": weighted_density * total_area_acres,
                "dwelling_units_current": first["density_limit"] * total_area_acres,
                "density_limit": first["density_limit"],
                "ZN19_CITY": first["ZN19_CITY"],
                "ZN19_SCAG": first["ZN19_SCAG"],
                "COUNTY": first["COUNTY"],
                "CITY": first["CITY"],
                "Tier_1": first["Tier_1"],
            }
        )

    residential_by_apn = (
        residential_around_metro.groupby("APN20", as_index=True)
        .apply(aggregate_parcel, include_groups=False)
        .reset_index(drop=True)
    )

    residential_by_apn = gpd.GeoDataFrame(residential_by_apn, geometry="geometry", crs=residential_around_metro.crs)

    print(f"Before dedup: {len(residential_around_metro)} rows")
    print(f"After APN dedup: {len(residential_by_apn)} rows")
    print(f"Total dwelling units: {residential_by_apn['dwelling_units_new'].sum():.0f}")

    residential_by_apn.to_file(OUT_PATH + r"\zoning_around_metro.json", driver="GeoJSON")
    return residential_by_apn, unary_union


@app.cell
def _(ca_parcels, residential_by_apn):
    # Join residential_by_apn (SCAG zoning data) onto CA parcels by APN
    # residential_by_apn has APN20, CA parcels have PARCEL_APN
    scag_cols_to_join = [
        "APN20",
        "density_limit",
        "weighted_density",
        "dwelling_units_new",
        "dwelling_units_current",
        "ZN19_CITY",
        "ZN19_SCAG",
        "area_acres",
        "CITY",
        "COUNTY",
        "TIER_1",
        "geometry",
    ]
    # Only keep columns that exist in residential_by_apn
    existing_cols = [c for c in scag_cols_to_join if c in residential_by_apn.columns]
    ca_parcels_with_zoning = ca_parcels.merge(
        residential_by_apn[existing_cols], left_on="PARCEL_APN", right_on="APN20", how="outer"
    )
    matched = ca_parcels_with_zoning["APN20"].notna().sum()
    print(f"CA parcels: {len(ca_parcels)}")
    print(f"Matched with SCAG zoning: {matched} ({matched / len(ca_parcels) * 100:.1f}%)")

    # Audit non-matches: which SCAG APN20s didn't find a CA parcel match?
    matched_apns = set(ca_parcels_with_zoning.dropna(subset=["APN20"])["APN20"].unique())
    all_scag_apns = set(residential_by_apn["APN20"].unique())
    unmatched_apns = all_scag_apns - matched_apns
    print(f"SCAG parcels not matched to CA parcels: {len(unmatched_apns)}")

    if len(unmatched_apns) > 0:
        unmatched = residential_by_apn[residential_by_apn["APN20"].isin(unmatched_apns)]
        print(f"  Total dwelling units in unmatched: {unmatched['dwelling_units_new'].sum():.0f}")
        print(f"  Counties in unmatched: {unmatched['COUNTY'].value_counts().to_dict()}")

    ca_parcels_with_zoning
    return (ca_parcels_with_zoning,)


@app.cell
def _(ca_parcels_with_zoning):
    ca_parcels_with_zoning["geometry"] = ca_parcels_with_zoning["geometry_x"].combine_first(
        ca_parcels_with_zoning["geometry_y"]
    )
    ca_parcels_with_zoning["city"] = ca_parcels_with_zoning["SITE_CITY"].combine_first(
        ca_parcels_with_zoning["CITY"].str.upper()
    )
    ca_parcels_with_zoning["county"] = ca_parcels_with_zoning["COUNTYNAME"].combine_first(
        ca_parcels_with_zoning["COUNTY"].str.upper()
    )
    ca_parcels_with_zoning["apn"] = ca_parcels_with_zoning["PARCEL_APN"].combine_first(ca_parcels_with_zoning["APN20"])
    ca_parcels_output = ca_parcels_with_zoning.rename(
        columns={"density_limit": "current_density", "weighted_density": "new_density"}
    )
    ca_parcels_output
    return (ca_parcels_output,)


@app.cell
def _():
    return


@app.cell
def _(ca_parcels_output):
    ca_parcels_output[
        [
            "geometry",
            "city",
            "county",
            "apn",
            "current_density",
            "new_density",
            "dwelling_units_current",
            "dwelling_units_new",
            "ZN19_CITY",
            "ZN19_SCAG",
            "area_acres",
        ]
    ].to_file(OUT_PATH + r"\parcels_with_zoning.json", driver="GeoJSON")
    return


@app.cell
def _(stops_df):
    stops_df["district_name"].value_counts()
    return


@app.cell
def _(stops_df):
    # from urllib.parse import urlencode
    # import json
    # import pandas as pd
    # import time
    # CA_PARCELS = 'https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/ArcGIS/rest/services/CA_Statewide_Parcels_Public_view/FeatureServer/0/query?'

    # def shapely_to_esri_json(polygon, wkid=3857):
    #     if polygon.is_empty:
    #         return None
    #     # Exterior ring
    #     coords = list(polygon.exterior.coords)
    #     rings = [[[x, y] for x, y in coords]]
    #     return {
    #         "rings": rings,
    #         "spatialReference": {"wkid": wkid}
    #     }

    HALF_MI_M = 804.7
    buffer = stops_df.copy()
    buffer.geometry = stops_df.to_crs("EPSG:3310").geometry.buffer(HALF_MI_M, resolution=8)

    # requests_per_second = 10
    # delay = 1.0 / requests_per_second
    # parcels = gpd.GeoDataFrame()

    # for _, _row in buffer.iterrows():
    #     _esri_geom = shapely_to_esri_json(_row.geometry, wkid=3310)
    #     _params = {
    #         'geometry': json.dumps(_esri_geom).replace(' ', ''),
    #         'geometryType': 'esriGeometryPolygon',
    #         'spatialRel': 'esriSpatialRelIntersects',
    #         'inSR': 3310,
    #         'outFields': '*',
    #         'returnGeometry': 'true',  # set true if you need parcel shapes
    #         'f': 'geojson',
    #         'where': '1=1',
    #     }
    #     _url = CA_PARCELS + urlencode(_params, safe='{}:,[]*')
    #     print(_url)
    #     _parcels = gpd.read_file(_url, driver='GeoJSON')
    #     if len(_parcels) > 2000:
    #         print(len(_parcels), _row['stop_name'], _row['district_name'])

    #     parcels = gpd.GeoDataFrame(pd.concat([parcels, _parcels], ignore_index=True))
    #     time.sleep(delay)
    # parcels
    return HALF_MI_M, buffer


@app.cell
def _():
    parcel_file = gpd.read_file(r"C:\Users\dknom\code\missing_metro\data\parcels.geojson")
    parcel_file
    return (parcel_file,)


@app.cell
def _(parcel_file):
    parcel_file["COUNTYNAME"].value_counts()
    return


@app.cell
def _(buffer):
    buffer
    return


@app.cell
def _(stops_df):
    stops_df
    return


@app.cell
def _():
    parcels_clipped = gpd.read_file(r"C:\Users\dknom\code\missing_metro\data\parcels_clipped.geojson", driver="GeoJSON")
    parcels_clipped
    return (parcels_clipped,)


@app.cell
def _(buffer, parcels_clipped):
    parcels_with_stops = gpd.sjoin(
        parcels_clipped.to_crs("EPSG:3310"),
        buffer[["stop_id", "geometry"]],
        how="left",
        predicate="intersects",
    )

    # 2. Identify parcels that intersect multiple stops
    multi = parcels_with_stops.groupby(parcels_with_stops.index).size()
    multi_parcel_ids = multi[multi > 1].index
    multi_parcel_ids
    return multi_parcel_ids, parcels_with_stops


@app.cell
def _(
    buffer,
    multi_parcel_ids,
    parcels_clipped,
    parcels_with_stops,
    stops_df,
    unary_union,
):
    print("collect all intersecting stop_ids and assign nearest")

    # 1. Deduplicate parcels_with_stops to one row per parcel
    #    (keep first stop_id as fallback for single-buffer parcels)
    parcels_deduped = parcels_with_stops[~parcels_with_stops.index.duplicated(keep="first")].copy()

    # 2. For multi-buffer parcels, find the nearest stop
    multi_parcels = parcels_with_stops.loc[multi_parcel_ids, ["stop_id"]]
    multi_exploded = multi_parcels.explode("stop_id").reset_index()
    multi_exploded.columns = ["parcel_idx", "stop_id"]

    # 3. Attach geometries in projected CRS (EPSG:3310) for meaningful distances
    stop_points_3310 = stops_df.set_index("stop_id").to_crs("EPSG:3310").geometry
    parcel_centroids_3310 = parcels_clipped.to_crs("EPSG:3310").geometry.centroid

    multi_exploded["distance"] = multi_exploded.apply(
        lambda r: parcel_centroids_3310[r["parcel_idx"]].distance(stop_points_3310[r["stop_id"]]),
        axis=1,
    )

    # 4. Keep only the closest stop for each multi-buffer parcel
    nearest = multi_exploded.loc[
        multi_exploded.groupby("parcel_idx")["distance"].idxmin(),
        ["parcel_idx", "stop_id"],
    ]
    nearest = nearest.set_index("parcel_idx")["stop_id"]

    # 5. Override stop_id for multi-buffer parcels with their nearest stop
    parcels_deduped.loc[nearest.index, "stop_id"] = nearest

    print("clipping parcels to buffer boundaries")
    # Clip each parcel to its assigned stop's buffer so we only count
    # the portion of the parcel that actually falls within the buffer
    buffer_indexed = buffer.set_index("stop_id")[["geometry"]]
    parcels_deduped["buffer_geom"] = parcels_deduped["stop_id"].map(buffer_indexed["geometry"])
    parcels_deduped["clipped_geom"] = parcels_deduped.geometry.intersection(parcels_deduped["buffer_geom"])

    print("deduplicating by APN to remove duplicate parcel rows")
    # The same APN can appear many times (same parcel fetched from multiple
    # buffer queries). Group by stop_id + APN to keep one row per unique parcel.
    parcels_deduped = parcels_deduped.groupby(["stop_id", "PARCEL_APN"], as_index=False).first()

    print("summing all parcels areas (using union to avoid double-counting overlapping geometries)")
    # Use unary_union to merge any remaining overlapping geometries
    # (e.g., different APNs sharing the same parcel shape like condos)
    area_per_stop = (
        parcels_deduped.groupby("stop_id")["clipped_geom"]
        .apply(lambda g: unary_union(g.tolist()).area / 4046.86)
        .reset_index()
    )
    area_per_stop.columns = ["stop_id", "parcel_acres"]

    print("merging back to stops")
    stops_with_parcel_area_df = stops_df.merge(area_per_stop, on="stop_id", how="left").fillna(0)
    return parcels_deduped, stops_with_parcel_area_df


@app.cell
def _(stops_with_parcel_area_df):
    stops_with_parcel_area_df
    return


@app.cell
def _(stops_with_parcel_area_df):
    stops_with_parcel_area_df.to_file(
        r"C:\Users\dknom\code\missing_metro\data\stops_with_parcel_area.geojson", driver="GeoJSON"
    )
    return


@app.cell
def _(parcels_deduped):
    parcels_deduped.set_geometry("clipped_geom").drop(columns=["geometry", "buffer_geom"]).set_crs("EPSG:3310").to_file(
        r"C:\Users\dknom\code\missing_metro\data\deduped_parcels.geojson", driver="GeoJSON"
    )
    return


@app.cell
def _():
    scag_parcels = gpd.read_file(r"C:\Users\dknom\code\missing_metro\data\zoning_around_metro.json", driver="GeoJSON")
    scag_parcels
    return


if __name__ == "__main__":
    app.run()
