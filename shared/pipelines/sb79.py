from pathlib import Path
from typing import Any

import geopandas as gpd
import gtfs_kit
import numpy as np
import pandas as pd
from shapely import unary_union

from shared.api.gtfs import parent_stations_from_gtfs
from shared.utils.constants import SCAG_FT_CRS, WGS84_GCS_CRS
from shared.utils.geoprocessing import clip_to_buffer_rings


def assign_tier_to_stops_from_gtfs(
    gtfs_path: str | Path,
    tier_overrides: dict[str, int] | None = None,
) -> gpd.GeoDataFrame:
    """Parse a GTFS zip and return a GeoDataFrame of Tier-1/Tier-2 parent stations."""
    feed = gtfs_kit.read_feed(gtfs_path, dist_units="km")
    result_df = parent_stations_from_gtfs(feed, days_of_week=[0, 1, 2, 3, 4])
    if result_df.empty:
        return _empty_result()

    exploded = (
        result_df[["stop_id", "route_types", "n_arrivals"]]
        .explode(["route_types", "n_arrivals"])
        .assign(route_type_int=lambda df: df["route_types"].astype(int))
    )
    train = exploded[exploded["route_type_int"].isin([0, 1, 2])]

    train_arrivals = train.groupby("stop_id")["n_arrivals"].sum()
    type_sets = train.groupby("stop_id")["route_type_int"].agg(set)

    result_df = result_df.assign(
        total_train_arrivals=result_df["stop_id"].map(train_arrivals).fillna(0),
        _types=result_df["stop_id"].map(type_sets).fillna("").apply(lambda s: s if isinstance(s, set) else set()),
    )

    arrivals = result_df["total_train_arrivals"]
    has_subway = result_df["_types"].map(lambda s: 1 in s)
    has_commuter = result_df["_types"].map(lambda s: 2 in s)
    has_light_rail = result_df["_types"].map(lambda s: 0 in s)

    result_df["Tier"] = pd.Series(pd.NA, index=result_df.index).case_when(
        [
            (has_subway, 1),
            (has_commuter & (arrivals >= 72), 1),
            (has_commuter & (arrivals >= 48), 2),
            (has_light_rail, 2),
        ]
    )

    result_df = result_df.dropna(subset=["Tier"]).assign(
        Tier=lambda df: df["Tier"].astype(int),
        n_arrivals=lambda df: df["total_train_arrivals"],
        route_ids=lambda df: df["route_ids"].apply(",".join),
        route_types=lambda df: df["route_types"].apply(",".join),
    )[["stop_id", "stop_name", "stop_lat", "stop_lon", "route_ids", "route_types", "Tier", "n_arrivals"]]

    if result_df.empty:
        return _empty_result()

    if tier_overrides:
        for rid, tier in tier_overrides.items():
            result_df.loc[result_df["route_ids"].str.contains(rid, regex=False), "Tier"] = tier

    return gpd.GeoDataFrame(
        result_df,
        geometry=gpd.points_from_xy(result_df["stop_lon"], result_df["stop_lat"], crs=WGS84_GCS_CRS),
    ).drop(columns=["stop_lat", "stop_lon"])


def _empty_result() -> gpd.GeoDataFrame:
    """Return an empty GeoDataFrame with the expected schema."""
    return gpd.GeoDataFrame(
        {
            "stop_id": pd.Series(dtype=str),
            "stop_name": pd.Series(dtype=str),
            "Tier": pd.Series(dtype=int),
            "route_ids": pd.Series(dtype=str),
            "route_types": pd.Series(dtype=str),
            "n_arrivals": pd.Series(dtype=int),
        },
        geometry=pd.Series(dtype=object),
        crs=WGS84_GCS_CRS,
    )


def compute_scag_density(scag_parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    is_la = scag_parcels["CITY"] == "Los Angeles"

    SCAG_FIXED = {
        "1123": 18,
        "1124": 60,
        "1125": 80,
        "1131": 6,
        "1150": 1,
        "1600": 40,
        "1610": 40,
        "1620": 30,
        "1220": 47,
        "1221": 47,
        "1222": 47,
        "2000": 0.2,
        "2100": 0.2,
        "2200": 0.2,
        "2300": 0.2,
        "2400": 0.2,
        "2500": 0.2,
        "2600": 0.2,
        "2700": 0.2,
        "1900": 0,
        "7777": 0,
        "1500": 0,
        "1233": 0,
        "1210": 0,
        "1211": 0,
        "1212": 0,
        "1213": 0,
        "1247": 0,
    }
    SCAG_AREA_BASED = {"1110": 1, "1111": 1, "1112": 1, "1113": 1, "1121": 3, "1122": 3, "1140": 3}

    zn19_scag = scag_parcels["ZN19_SCAG"].astype(str)
    scag_density = zn19_scag.map(SCAG_FIXED)

    area_mask = zn19_scag.isin(SCAG_AREA_BASED)
    if area_mask.any():
        area_acres = scag_parcels.loc[area_mask].to_crs(SCAG_FT_CRS).area / 43560
        area_acres = area_acres.replace(0, np.nan)
        scag_density.loc[area_mask] = zn19_scag[area_mask].map(SCAG_AREA_BASED) / area_acres

    LA_ZONES = [
        ("RD1.5", 43560 / 1500),
        ("RD2", 43560 / 2000),
        ("RD3", 43560 / 3000),
        ("RD4", 43560 / 4000),
        ("RD5", 43560 / 5000),
        ("RD6", 43560 / 6000),
        ("RMP", 43560 / 20000),
        ("RAS3", 43560 / 800),
        ("RAS4", 43560 / 400),
        ("R3", 43560 / 800),
        ("R4", 43560 / 400),
        ("R5", 43560 / 200),
        ("RE40", 43560 / 40000),
        ("RE20", 43560 / 20000),
        ("RE15", 43560 / 15000),
        ("RE11", 43560 / 11000),
        ("RE9", 43560 / 9000),
        ("RS", 43560 / 7500),
        ("R1", 43560 / 5000),
        ("RU", 43560 / 3500),
        ("RZ2.5", 43560 / 2500),
        ("RZ3", 43560 / 3000),
        ("RZ4", 43560 / 4000),
        ("RW1", 43560 / 2300),
        ("R2", 43560 / 2500),
        ("RW2", 43560 / 1150),
        ("C1", 100),
        ("C2", 43560 / 400),
        ("C3", 110),
        ("C4", 200),
    ]

    if is_la.any():
        la_zones = scag_parcels.loc[is_la, "ZN19_CITY"]
        la_density = pd.Series(np.nan, index=la_zones.index)
        for code, density in LA_ZONES:
            unmatched = la_density.isna()
            if not unmatched.any():
                break
            mask = unmatched & la_zones.str.contains(code, na=False, regex=False)
            la_density.loc[mask] = density
        scag_density.loc[is_la] = la_density

    scag_parcels["current_density_du_per_ac"] = pd.to_numeric(scag_density, errors="coerce")
    return scag_parcels


_ZONE_DENSITIES = pd.Series(
    {
        ("200 ft", 1): 160,
        ("200 ft", 2): 140,
        ("1320 ft", 1): 120,
        ("1320 ft", 2): 100,
        ("2640 ft", 1): 100,
        ("2640 ft", 2): 80,
    }
)

_BUFFER_DISTANCES = [200, 1320, 2640]

_KEEP_COLS = ["APN20", "current_density_du_per_ac", "ZN19_CITY", "ZN19_SCAG", "COUNTY", "CITY", "geometry"]


def compute_dwelling_units(
    stops_gdf: gpd.GeoDataFrame,
    scag_density: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    stops_trimmed = stops_gdf[["stop_id", "Tier", "geometry"]]

    scag_clean = scag_density.drop(columns=["Tier"], errors="ignore")[[c for c in _KEEP_COLS if c in scag_density.columns]]

    rings = clip_to_buffer_rings(
        features_gdf=scag_clean,
        sources_gdf=stops_trimmed,
        buffer_distances=_BUFFER_DISTANCES,
        buffer_crs=SCAG_FT_CRS,
        donut_how="difference",
    )

    for ring, distance in zip(rings, _BUFFER_DISTANCES, strict=False):
        ring["buffer_zone_id"] = f"{distance} ft"

    buffed_parcels = gpd.GeoDataFrame(pd.concat(rings, ignore_index=True))

    buffed_parcels["area_sqft"] = buffed_parcels.area
    buffed_parcels["zone_density"] = buffed_parcels.set_index(["buffer_zone_id", "Tier"]).index.map(_ZONE_DENSITIES)
    buffed_parcels["area_x_density"] = buffed_parcels["area_sqft"] * buffed_parcels["zone_density"]

    agg = buffed_parcels.groupby("APN20").agg(
        area_sqft_total=("area_sqft", "sum"),
        area_x_density_sum=("area_x_density", "sum"),
        current_density_du_per_ac=("current_density_du_per_ac", "first"),
        Tier=("Tier", "first"),
        ZN19_CITY=("ZN19_CITY", "first"),
        ZN19_SCAG=("ZN19_SCAG", "first"),
        COUNTY=("COUNTY", "first"),
        CITY=("CITY", "first"),
        buffer_zone_id=("buffer_zone_id", "first"),
        geometry=("geometry", unary_union),
    )

    area_acres = agg["area_sqft_total"] / 43560
    weighted_density = np.where(
        (agg["area_sqft_total"] > 0) & (agg["current_density_du_per_ac"].notna()),
        agg["area_x_density_sum"] / agg["area_sqft_total"],
        np.nan,
    )

    by_apn = gpd.GeoDataFrame(
        {
            "APN20": agg.index,
            "geometry": agg["geometry"].values,
            "area_acres": area_acres.values,
            "new_density_du_per_ac": weighted_density,
            "new_dwelling_units": weighted_density * area_acres.values,
            "current_dwelling_units": agg["current_density_du_per_ac"].values * area_acres.values,
            "current_density_du_per_ac": agg["current_density_du_per_ac"].values,
            "Tier": agg["Tier"].values,
            "ZN19_CITY": agg["ZN19_CITY"].values,
            "ZN19_SCAG": agg["ZN19_SCAG"].values,
            "COUNTY": agg["COUNTY"].values,
            "CITY": agg["CITY"].values,
            "buffer_zone_id": agg["buffer_zone_id"].values,
        },
        geometry="geometry",
        crs=buffed_parcels.crs,
    )

    by_apn["additional_du"] = np.maximum(
        0,
        by_apn.get("new_dwelling_units", 0) - by_apn.get("current_dwelling_units", 0),
    )
    return by_apn
