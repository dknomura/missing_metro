from pathlib import Path
from typing import Any

import geopandas as gpd
import gtfs_kit
import numpy as np
import pandas as pd

from shared.api.gtfs import parent_stations_from_gtfs


def assign_tier_to_stops_from_gtfs(
    gtfs_path: str | Path,
    tier_overrides: dict[str, int] | None = None,
) -> gpd.GeoDataFrame:
    """Parse a GTFS zip and return a GeoDataFrame of Tier-1/Tier-2 parent stations."""
    feed = gtfs_kit.read_feed(gtfs_path, dist_units="km")

    if not feed.get_first_week():
        return _empty_result()

    result_df = (
        parent_stations_from_gtfs(feed)
        .assign(
            n_arrivals=lambda df: df["n_arrivals"].fillna(0).astype(int),
            served_routes=lambda df: df["route_ids"].map(sorted),
            Tier=lambda df: pd.Series(pd.NA, index=df.index).case_when(
                [
                    (df["route_types"].map(lambda t: "1" in t), 1),
                    (df["route_types"].map(lambda t: "0" in t or "2" in t) & (df["n_arrivals"] >= 72), 1),
                    (df["route_types"].map(lambda t: "2" in t) & df["n_arrivals"].between(48, 71), 2),
                    (df["route_types"].map(lambda t: "0" in t) & (df["n_arrivals"] < 72), 2),
                ]
            ),
        )
        .dropna(subset=["Tier"])
        .assign(
            Tier=lambda df: df["Tier"].astype(int),
            agency=lambda df: df["agencies"].map(lambda s: ",".join(sorted(s))),
            route_ids_served=lambda df: df["served_routes"].map(",".join),
            routetypes=lambda df: df["route_types"].map(lambda s: ",".join(sorted(s, key=int))),
        )[["stop_id", "stop_name", "stop_lat", "stop_lon", "Tier", "agency", "route_ids_served", "routetypes", "n_arrivals"]]
    )

    if result_df.empty:
        return _empty_result()

    if tier_overrides:
        for rid, tier in tier_overrides.items():
            mask = result_df["route_ids_served"].str.contains(rid, regex=False)
            result_df.loc[mask, "Tier"] = tier

    return gpd.GeoDataFrame(
        result_df,
        geometry=gpd.points_from_xy(result_df["stop_lon"], result_df["stop_lat"], crs="EPSG:4326"),
    ).drop(columns=["stop_lat", "stop_lon"])


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
