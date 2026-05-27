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

__generated_with = "0.23.6"
app = marimo.App()

with app.setup(hide_code=True):
    from pathlib import Path

    import folium
    import geopandas as gpd
    import mapclassify  # noqa: F401
    import marimo as mo
    import matplotlib  # noqa: F401
    from folium.plugins import MarkerCluster, VectorGridProtobuf

    from shared.api.arcgis import fetch_from_arcgis
    from shared.pipelines.sb79 import assign_tier_to_stops_from_gtfs, compute_dwelling_units, compute_scag_density
    from shared.utils.constants import HALF_MI_M, WGS84_GCS_CRS

    SCAG_PARCELS_URL = "https://rdp.scag.ca.gov/mapping/rest/services/Housing/2020_Annual_Land_Use/MapServer/0/query"
    PARCEL_TILES_URL = "https://vectortileservices3.arcgis.com/NaFf4UaPo3IgQXqn/arcgis/rest/services/sb79_transit_parcels/VectorTileServer/tile/{z}/{y}/{x}.pbf"
    STOPS_URL = (
        "https://services3.arcgis.com/NaFf4UaPo3IgQXqn/ArcGIS/rest/services/sb79_transit_stations/FeatureServer/0/query"
    )


@app.cell(hide_code=True)
def _():
    mo.md("""
# SB-79 Analysis
[Code for this notebook.](https://github.com/dknomura/missing_metro/blob/main/notebooks/sb79map.py)

## Instructions
1. If you do not have a GTFS zip file, download one of the following for a demo
        - [OC Streetcar GTFS](https://github.com/dknomura/missing_metro/raw/refs/heads/main/notebooks/public/oc-streetcar_gtfs.zip)
        - [LA Metro GTFS](https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip) this will take 5-10 min
2. GTFS file needs to be for a transit system in the SCAG region (LA, Orange, Riverside, San Bernardino, Ventura).


## Overview of SB-79 analysis
SB-79 is a California bill that promotes transit oriented development by allowing increased residential density
and reduced parking requirements within a half mile of qualifying transit stations.
The two tables below summarize the tier designations and the zoning allowances under SB-79.

| **Table 1: Tier designations** | | | |
|--------|----------------------|----|--------------------------------------------|
| Tier 1 | Heavy rail (subway) | or | Commuter rail with more than 72 trains/day |
| Tier 2 | Light rail | or | Commuter rail with more than 48 trains/day |

**Table 2: Permitted Zoning Based on Distance from Station (du/ac = dwelling units / acre)**

| | Within 200 feet of a station | Within ¼ mile of a station | Within ½ mile of a station |
|--------|------------------------------|---------------------------|---------------------------|
| Tier 1 | 9 stories (160 du/ac) | 7 stories (120 du/ac) | 6 stories (100 du/ac) |
| Tier 2 | 8 stories (140 du/ac) | 6 stories (100 du/ac) | 5 stories (80 du/ac) |

This notebook takes in a General Transit Feed Specification (GTFS) zip file and uses SCAG zoning parcel data to calculate
the density and dwelling units that are currently permitted and compares it with the potential under SB-79.
""")
    return


@app.cell(hide_code=True)
def _():
    file_input = mo.ui.file_browser(
        initial_path=str(Path.home() / "Downloads"),
        label="Select a GTFS zip file to initiate the SB-79 analysis",
        filetypes=[".zip"],
        multiple=False,
    )

    mo.hstack([file_input], justify="start")
    return (file_input,)


@app.cell
def _(file_input):
    mo.stop(not file_input.value, mo.md("⬆️ Upload a GTFS zip to continue"))

    new_stops = assign_tier_to_stops_from_gtfs(str(file_input.path()))
    return (new_stops,)


@app.cell
def _(new_stops):

    new_stops.explore("Tier", tiles="CartoDB positron", categorical=True)
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

    with mo.status.spinner(title="Loading...") as _spinner:
        scag_parcels = fetch_from_arcgis(
            url=SCAG_PARCELS_URL,
            geometries=buffers.geometry.tolist(),
            out_fields=(
                "APN20,COUNTY,CITY,IL_RATIO,ZN19_CITY,ZN19_SCAG,TCAC_2024,"
                "APPAREL1MI,EDUC1MI,GROCERY1MI,HOSPIT1MI,RESTAUR1MI,JOBS_30MIN,YEAR"
            ),
            wkid=3310,
        )
        scag_parcels = scag_parcels.groupby("APN20", as_index=False).first()

        scag_parcels = scag_parcels.set_crs(WGS84_GCS_CRS)
    return (scag_parcels,)


@app.cell
def _(file_input, scag_parcels):
    mo.stop(not file_input.value)

    page_size = 4000

    page = mo.ui.slider(
        start=0,
        stop=len(scag_parcels) // page_size,
        step=1,
        label="Page",
    )
    page

    return page, page_size


@app.cell
def _(file_input, page, page_size, scag_parcels):
    mo.stop(not file_input.value)
    start = page.value * page_size
    end = start + page_size
    mo.md("""Only showing a subset of 4000 parcels, but can paginate through to see all parcels.
     Calculations are done on all parcels.""") if scag_parcels is not None else None
    return end, start


@app.cell
def _(end, scag_parcels, start):
    scag_parcels[start:end].explore(tiles="CartoDB positron")
    return


@app.cell(hide_code=True)
def _(scag_parcels):
    scag_with_density = compute_scag_density(scag_parcels=scag_parcels)
    return (scag_with_density,)


@app.cell
def _(new_stops, scag_with_density):
    with mo.status.spinner(title="Loading...") as _spinner:
        scag_with_dwelling_units = compute_dwelling_units(stops_gdf=new_stops, scag_density=scag_with_density)  # noqa: F841
    return (scag_with_dwelling_units,)


@app.cell
def _(end, scag_with_dwelling_units, start):
    scag_with_dwelling_units[start:end].explore("buffer_zone_id", tiles="CartoDB positron")
    return


@app.cell
def _(end, scag_with_dwelling_units, start):
    scag_with_dwelling_units[start:end].explore("new_density_du_per_ac", tiles="CartoDB positron")
    return


@app.cell
def _(end, scag_with_dwelling_units, start):

    scag_with_dwelling_units[start:end].explore("current_density_du_per_ac", tiles="CartoDB positron")
    return


@app.cell
def _(end, scag_with_dwelling_units, start):
    scag_with_dwelling_units[start:end].explore("additional_du", tiles="CartoDB positron")
    return


@app.cell
def _(file_input, scag_parcels, scag_with_dwelling_units):
    mo.md(f"""
    Total potential dwelling units for {file_input.name()}: {int(scag_with_dwelling_units["additional_du"].sum())}

    SCAG has some unclassified, non-specific zoning categories that are parks, shopping centers, etc that likely would not
    be developable. Also this does not take into account any recent construction that would similarly not have
    further development. All current zoning is estimated from
    [these SCAG designations](https://scag-spm-documentation.readthedocs.io/en/latest/scag_lu_codes_description/).
    This is just an initial estimate at the potential dwelling units and areas for potential development.
    """) if scag_parcels is not None else None
    return


@app.cell(hide_code=True)
def _():
    stops = fetch_from_arcgis(url=STOPS_URL)
    stops = stops.set_crs(WGS84_GCS_CRS)
    return (stops,)


@app.cell(hide_code=True)
def _(stops):
    m = folium.Map(location=[34.0617140033952, -118.314146442073], tiles="CartoDB Positron", zoom_start=5)

    VectorGridProtobuf(PARCEL_TILES_URL, "folium_layer_name").add_to(m)
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
    m
    return


@app.cell
def _(scag_parcels):
    mo.md("""
    The map above shows all elligible stations for SB-79 in California. Parcels are served as vector tiles and are visible
    at higher zoom levels, but they do not contain any zoning information. Our analysis only covers the SCAG region
    (LA, Orange, Riverside, San Bernardino, Ventura counties), but the overall impact can be extrapolated to the
    other regions in California.
    """) if scag_parcels is not None else None
    return


if __name__ == "__main__":
    app.run()
