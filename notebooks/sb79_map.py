# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "arcgis>=2.4.3",
#     "folium>=0.20.0",
#     "geopandas>=1.1.3",
#     "marimo>=0.23.3",
#     "pandas>=3.0.2",
# ]
# ///

import marimo

__generated_with = "0.23.4"
app = marimo.App()

with app.setup:
    import marimo as mo
    import geopandas as gpd
    import folium
    import arcgis
    import json
    from collections import defaultdict
    import pandas as pd
    from folium.plugins import MarkerCluster
    STOPS_LAYER_ID = "d6fc47eca97e48b7890c8fb7c9b69688"


@app.cell
def _():
    arc_api = arcgis.GIS()
    stops = arc_api.content.get(STOPS_LAYER_ID).layers[0].query()
    # Use from_features with GeoJSON instead of .sdf to avoid arcgis pandas import issues in sandbox
    stops_df = gpd.GeoDataFrame.from_features(features=json.loads(stops.to_geojson))
    stops_df = stops_df.set_crs("EPSG:3857")
    stops_df = stops_df.to_crs("EPSG:4326")
    stops_df
    return (stops_df,)


@app.cell
def _(stops_df):
    m = folium.Map(location=[34.0617140033952, -118.314146442073], tiles="CartoDB Positron", zoom_start=10)

    cluster = MarkerCluster(disable_clustering_at_zoom=10).add_to(m)
    for _, _row in stops_df.iterrows():
        color = 'blue' if _row['Tier'] == 2 else 'red'
        cluster.add_child(
            folium.CircleMarker(
                location=[_row.geometry.y, _row.geometry.x], 
                radius=5, 
                tooltip=folium.Tooltip(f"Stop Name: {_row['stop_name']}<br>Tier: {_row['Tier']}<br>Routes: {_row['route_ids_served']}<br>City: {_row['city']}<br>County: {_row['county']}<br>Route type: {_row['routetypes']}"),
                color=color,
                fill_color=color
            ).add_to(m)
        )
    m
    return


if __name__ == "__main__":
    app.run()
