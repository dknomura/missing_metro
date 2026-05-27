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

    exploded = result_df[["stop_id", "route_types", "n_arrivals"]].explode(["route_types", "n_arrivals"])
    train_arrivals = exploded[exploded["route_types"].astype(int).isin([0, 1, 2])].groupby("stop_id")["n_arrivals"].sum()
    result_df["total_train_arrivals"] = result_df["stop_id"].map(train_arrivals).fillna(0)

    # --- Tier assignment (priority order: subway > high-freq rail > medium commuter > low light rail) ---
    has_subway = result_df["route_types"].apply(lambda rts: "1" in rts)
    has_commuter = result_df["route_types"].apply(lambda rts: "2" in rts)
    has_light_rail = result_df["route_types"].apply(lambda rts: "0" in rts)
    arrivals = result_df["total_train_arrivals"]

    result_df["Tier"] = pd.Series(pd.NA, index=result_df.index).case_when(
        [
            (has_subway, 1),
            ((has_commuter) & (arrivals >= 72), 1),
            ((has_commuter) & (arrivals >= 48), 2),
            (has_light_rail, 2),
        ]
    )

    # --- Apply route-level overrides ---
    if tier_overrides:
        for rid, tier in tier_overrides.items():
            mask = result_df["route_ids"].apply(lambda rids, rid=rid: rid in rids)
            result_df.loc[mask, "Tier"] = tier

    result_df = result_df.dropna(subset=["Tier"]).assign(
        Tier=lambda df: df["Tier"].astype(int),
        n_arrivals=lambda df: df["total_train_arrivals"],
        route_ids=lambda df: df["route_ids"].apply(",".join),
        route_types=lambda df: df["route_types"].apply(",".join),
    )[["stop_id", "stop_name", "stop_lat", "stop_lon", "route_ids", "route_types", "Tier", "n_arrivals"]]

    if result_df.empty:
        return _empty_result()

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

    zn19_scag = df["ZN19_SCAG"].astype(str)
    area_acres = df.to_crs(SCAG_FT_CRS).area / 43560
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

    is_la = df["CITY"] == "Los Angeles"

    df["current_density_du_per_ac"] = scag_density
    df.loc[is_la, "current_density_du_per_ac"] = la_density
    df["current_density_du_per_ac"] = pd.to_numeric(df["current_density_du_per_ac"], errors="coerce")
    return df


def compute_dwelling_units(
    stops_gdf: gpd.GeoDataFrame,
    scag_density: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    stops_trimmed = stops_gdf[["stop_id", "Tier", "geometry"]]

    scag_clean = scag_density.drop(columns=["Tier"], errors="ignore")

    buffer_distances = [200, 1320, 2640]
    rings = clip_to_buffer_rings(
        features_gdf=scag_clean,
        sources_gdf=stops_trimmed,
        buffer_distances=buffer_distances,
        buffer_crs=SCAG_FT_CRS,
        donut_how="difference",
    )

    for ring, distance in zip(rings, buffer_distances, strict=False):
        ring["buffer_zone_id"] = f"{distance} ft"

    buffed_parcels = gpd.GeoDataFrame(pd.concat(rings, ignore_index=True))

    buffed_parcels["area_sqft"] = buffed_parcels.area

    zone_densities_series = pd.Series(
        {
            ("200 ft", 1): 160,
            ("200 ft", 2): 140,
            ("1320 ft", 1): 120,
            ("1320 ft", 2): 100,
            ("2640 ft", 1): 100,
            ("2640 ft", 2): 80,
        }
    )
    buffed_parcels["zone_density"] = buffed_parcels.set_index(["buffer_zone_id", "Tier"]).index.map(zone_densities_series)

    area_agg = buffed_parcels.groupby("APN20")["area_sqft"].sum().rename("area_sqft_total")
    area_acres = area_agg / 43560

    buffed_parcels["area_x_density"] = buffed_parcels["area_sqft"] * buffed_parcels["zone_density"]
    weighted_sum = buffed_parcels.groupby("APN20")["area_x_density"].sum()

    geom_agg = buffed_parcels.groupby("APN20")["geometry"].agg(unary_union)

    first_agg = buffed_parcels.groupby("APN20").first()[
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
        crs=buffed_parcels.crs,
    )
    by_apn["additional_du"] = np.maximum(
        0,
        by_apn.get("new_dwelling_units", 0) - by_apn.get("current_dwelling_units", 0),
    )
    return by_apn
