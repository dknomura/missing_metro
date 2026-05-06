# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "auto-mix-prep>=0.2.0",
#     "folium>=0.20.0",
#     "geopandas>=1.1.3",
#     "marimo>=0.23.3",
# ]
# ///

import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    import folium
    import marimo as mo
    from folium.plugins import MarkerCluster, VectorGridProtobuf

    from arcgis_paginate import (
        fetch_from_arcgis,
    )

    PARCELS_URL = "https://vectortileservices3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/VectorTileServer/tile/{z}/{y}/{x}.pbf"
    STOPS_URL = (
        "https://services3.arcgis.com/NaFf4UaPo3IgQXqn/ArcGIS/rest/services/sb79_transit_stations/FeatureServer/0/query"
    )

    # https://vectortileservices3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/VectorTileServer/tile/12/704/1634.pbf

    # https://vectortileservices3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/VectorTileServer/tile/7/49/21.pbf
    return (
        MarkerCluster,
        PARCELS_URL,
        STOPS_URL,
        VectorGridProtobuf,
        fetch_from_arcgis,
        folium,
        mo,
    )


@app.cell
def _(STOPS_URL, fetch_from_arcgis):
    stops = fetch_from_arcgis(url=STOPS_URL)
    stops
    return (stops,)


@app.cell
def _(MarkerCluster, folium, stops):
    m = folium.Map(location=[34.0617140033952, -118.314146442073], tiles="CartoDB Positron", zoom_start=10)

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
                    Route type: {_row["routetypes"]}"""
                ),
                color=color,
                fill_color=color,
            ).add_to(m)
        )
    m
    return (m,)


@app.cell
def _(PARCELS_URL, VectorGridProtobuf, m):
    VectorGridProtobuf(PARCELS_URL, "folium_layer_name").add_to(m)
    m
    return


@app.cell
def _(m, mo):
    map_widget = mo.ui.map(m)
    map_widget
    return (map_widget,)


@app.cell
def _(map_widget):
    import requests
    from marimo import mo

    # This will run every time the map is clicked
    if map_widget.value:
        lat, lon = map_widget.value["lat"], map_widget.value["lon"]

        # Use the FeatureServer's /query endpoint (assuming geometryType=esriGeometryPoint)
        query_url = (
            "https://services3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/FeatureServer/0/query"
        )
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",  # get all attributes
            "returnGeometry": "false",  # we only need attributes
            "f": "geojson",
        }
        resp = requests.get(query_url, params=params)
        if resp.ok and resp.json()["features"]:
            parcel = resp.json()["features"][0]["properties"]
            mo.md(f"**Parcel info:**\n```json\n{parcel}\n```")
        else:
            mo.md("No parcel found at that point.")
    return (mo,)


if __name__ == "__main__":
    app.run()
