import geopandas as gpd
from shapely.geometry import Point, box


def _make_parcels_gdf(parcels_data: list[dict]) -> gpd.GeoDataFrame:
    records = []
    for p in parcels_data:
        rec = {
            "APN20": p["APN20"],
            "current_density_du_per_ac": p.get("current_density_du_per_ac", 0),
            "ZN19_CITY": p.get("ZN19_CITY", ""),
            "ZN19_SCAG": p.get("ZN19_SCAG", 0),
            "CITY": p.get("CITY", ""),
            "COUNTY": p.get("COUNTY", ""),
            "Tier": p.get("Tier", 1),
            "geometry": box(*p["bbox"]),
        }
        # Include any extra columns passed in the dict
        for k, v in p.items():
            if k not in rec and k != "bbox":
                rec[k] = v
        records.append(rec)
    return gpd.GeoDataFrame(records, crs="EPSG:4326")


def _make_stops_gdf(stops_data: list[dict]) -> gpd.GeoDataFrame:
    """Create a stops GeoDataFrame from a list of dicts."""
    records = []
    for s in stops_data:
        records.append(
            {
                "stop_id": s["stop_id"],
                "stop_name": s.get("stop_name", ""),
                "Tier": s["Tier"],
                "geometry": Point(s["lon"], s["lat"]),
            }
        )
    return gpd.GeoDataFrame(records, crs="EPSG:4326")
