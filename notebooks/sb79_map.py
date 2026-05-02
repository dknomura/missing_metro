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
    import folium
    import geopandas as gpd
    from folium.plugins import MarkerCluster

    STOPS_LAYER_ID = "d6fc47eca97e48b7890c8fb7c9b69688"
    STOPS_URL = r"https://services3.arcgis.com/NaFf4UaPo3IgQXqn/ArcGIS/rest/services/sb79_transit_stops/FeatureServer/0/query?where=0%3D0&objectIds=&geometry=&geometryType=esriGeometryEnvelope&inSR=&spatialRel=esriSpatialRelIntersects&resultType=none&distance=0.0&units=esriSRUnit_Meter&outDistance=&relationParam=&returnGeodetic=false&outFields=*&returnHiddenFields=false&returnGeometry=true&featureEncoding=esriDefault&multipatchOption=xyFootprint&maxAllowableOffset=&geometryPrecision=&outSR=&defaultSR=&datumTransformation=&applyVCSProjection=false&returnIdsOnly=false&returnUniqueIdsOnly=false&returnCountOnly=false&returnExtentOnly=false&returnQueryGeometry=false&returnDistinctValues=false&cacheHint=false&collation=&orderByFields=&groupByFieldsForStatistics=&returnAggIds=false&outStatistics=&having=&resultOffset=&resultRecordCount=&returnZ=false&returnM=false&returnTrueCurves=false&returnExceededLimitFeatures=true&quantizationParameters=&sqlFormat=none&f=pgeojson"


@app.cell
def _():
    stops_df = gpd.read_file(STOPS_URL, driver="GeoJSON")
    stops_df.crs
    return (stops_df,)


@app.cell
def _(stops_df):
    m = folium.Map(
        location=[34.0617140033952, -118.314146442073], tiles="CartoDB Positron", zoom_start=10
    )

    cluster = MarkerCluster(disable_clustering_at_zoom=10).add_to(m)
    for _, _row in stops_df.iterrows():
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
                    Route type: {_row["routetypes"]}"""
                ),
                color=color,
                fill_color=color,
            ).add_to(m)
        )
    m
    return


if __name__ == "__main__":
    app.run()
