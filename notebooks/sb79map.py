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
    import tempfile
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
    # SB 79 Analysis
    ## Overview of SB 79
    SB 79, a California bill promoting transit-oriented development, goes into effect in July 2026. By increasing
    housing density within a half mile of qualifying transit stations, the law encourages
    more development near transit to reduce car dependency, congestion, and vehicle miles traveled.
    The two tables below summarize the qualifying transit stations and the new zoning under SB 79.

    #### Table 1: Tier designations
    | | | | |
    |--------|----------------------|----|--------------------------------------------|
    | Tier 1 | Heavy rail | or | Commuter rail with more than 72 trains/day |
    | Tier 2 | Light rail/BRT | or | Commuter rail with more than 48 trains/day |

    #### Table 2: Permitted Zoning Density (dwelling units / acre)
    | | Within 200 ft of a station | Within ¼ mi of a station | Within ½ mi of a station |
    |--------|------------------------------|---------------------------|---------------------------|
    | Tier 1 | 160 | 120 | 100 |
    | Tier 2 | 140 | 100 | 80 |

    This notebook takes in a transit schedule zip file (GTFS format) and uses Southern California
    Association of Governments (SCAG) zoning parcel data to calculate the density and dwelling units that are
    currently permitted and compares it with the increase under SB 79.

    ## Instructions
    1. If you do not have a GTFS zip file, download one of the following for a demo
        - [Mock OC Streetcar GTFS](https://github.com/dknomura/missing_metro/raw/refs/heads/main/notebooks/public/oc-streetcar_gtfs.zip).
          For the OC Streetcar that is due to open in 2026. This is the fastest option, may take a few minutes to analyze
        - [LA Metro GTFS](https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip). For the current LA Metro system.
          This will take ~5-10 min
        - [Mock Pacific Electric Red Trolley Car GTFS](https://github.com/dknomura/missing_metro/raw/refs/heads/main/notebooks/public/mock_pacific_electric_gtfs.zip).
          A what-if scenario where Los Angeles develops a similar level of transit infrastructure as it had in the 1920s
          when it had the [largest electric railway system with the red trolley cars](https://en.wikipedia.org/wiki/Pacific_Electric).
          Note: only main stations are included. This will take 5-10 min
    2. ⚠️ The GTFS file must be from a transit system in the SCAG region
        (LA, Orange, Riverside, San Bernardino, or Ventura County).
    3. This notebook is hosted on a free tier service, so the first load may take a few minutes as the server warms up and
       the server may crash on bigger GTFS files.
    4. Once the notebook loads, there will be a button to upload the GTFS zip file.
    """)
    return


@app.cell(hide_code=True)
def _():
    file_input = mo.ui.file(
        label="Select a GTFS zip file to initiate the SB 79 analysis",
        filetypes=[".zip"],
    )

    file_input
    return (file_input,)


@app.cell
def _(file_input):
    mo.stop(not file_input.value)
    with mo.status.spinner(title=f"Loading {file_input.value[0].name}...") as _spinner:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(file_input.value[0].contents)
            tmp.flush()
            new_stops = assign_tier_to_stops_from_gtfs(tmp.name)
    return (new_stops,)


@app.cell
def _(new_stops):
    new_stops.explore("Tier", tiles="CartoDB positron", categorical=True)
    return


@app.cell
def _(file_input):
    mo.stop(not file_input.value)
    mo.md("""
    Half mile buffers around eligible stops
    """)
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

    with mo.status.spinner(title="Downloading SCAG parcels...") as _spinner:
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
        label="Parcel page",
    )
    page
    return page, page_size


@app.cell
def _(file_input, page, page_size):
    mo.stop(not file_input.value)
    start = page.value * page_size
    end = start + page_size
    mo.md("""Only showing a subset of 4000 parcels, use the slider above to see different parcel subsets.
     Final calculations are done on all parcels. Grey parcels are ineligible for residential development.""")
    return end, start


@app.cell
def _(end, scag_parcels, start):
    scag_parcels[start:end].explore(tiles="CartoDB positron")
    return


@app.cell
def _(new_stops, scag_parcels):
    with mo.status.spinner(title="Calculating housing density...") as _spinner:
        scag_with_density = compute_scag_density(scag_parcels=scag_parcels)
        scag_with_dwelling_units = compute_dwelling_units(stops_gdf=new_stops, scag_density=scag_with_density)  # noqa: F841
    return (scag_with_dwelling_units,)


@app.cell
def _(end, scag_with_dwelling_units, start):
    scag_with_dwelling_units[start:end].explore("buffer_zone_id", tiles="CartoDB positron")
    return


@app.cell
def _(end, scag_with_dwelling_units, start):

    scag_with_dwelling_units[start:end].explore("current_density_du_per_ac", tiles="CartoDB positron")
    return


@app.cell
def _(end, scag_with_dwelling_units, start):
    scag_with_dwelling_units[start:end].explore("new_density_du_per_ac", tiles="CartoDB positron")
    return


@app.cell
def _(end, scag_with_dwelling_units, start):
    scag_with_dwelling_units[start:end].explore("additional_du", tiles="CartoDB positron")
    return


@app.cell
def _(file_input, scag_with_dwelling_units):
    mo.stop(scag_with_dwelling_units is None)
    mo.md(f"""
    Total potential dwelling units for {file_input.name()}: {int(scag_with_dwelling_units["additional_du"].sum())}

    Some SCAG zoning codes are non-specific and can represent a mix of residential, commercial, park, or institutional uses
    and it is not always possible to determine from the data alone whether a parcel is a viable housing development site.
    These parcels are included in the calculations and may inflate the overall estimates.
    Zoning is just one hurdle in the housing crisis and there are many other factors that determine
    whether a parcel would be developed or not, but this gives a general idea of the potential for increased density
    and dwelling units under SB 79.
    """)
    return


@app.cell(hide_code=True)
def _():
    stops = fetch_from_arcgis(url=STOPS_URL)
    stops = stops.set_crs(WGS84_GCS_CRS)
    return (stops,)


@app.cell(hide_code=True)
def _(scag_with_dwelling_units, stops):
    mo.stop(scag_with_dwelling_units is None)
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
    m
    return


@app.cell
def _(scag_with_dwelling_units):
    mo.stop(scag_with_dwelling_units is None)
    mo.md("""
    The map above shows the number and location of all eligible stations for SB 79 in California. Parcels are served
    as vector tiles and are visible when you zoom into the individual stations,
    but they do not contain any zoning information. Our analysis only covers the SCAG region
    (LA, Orange, Riverside, San Bernardino, Ventura counties), but the overall impact can be extrapolated to the
    other regions in California.
    """)
    return


if __name__ == "__main__":
    app.run()
