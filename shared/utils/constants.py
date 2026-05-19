SCAG_PARCELS_URL = "https://rdp.scag.ca.gov/mapping/rest/services/Housing/2020_Annual_Land_Use/MapServer/0/query"
CA_PARCELS_URL = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/ArcGIS/rest/services/"
    "CA_Statewide_Parcels_Public_view/FeatureServer/0/query"
)
SCAG_OUT_FIELDS = (
    "APN20,COUNTY,CITY,IL_RATIO,ZN19_CITY,ZN19_SCAG,TCAC_2024,"
    "APPAREL1MI,EDUC1MI,GROCERY1MI,HOSPIT1MI,RESTAUR1MI,JOBS_30MIN,YEAR"
)
HALF_MI_M = 804.7

ZONE_DENSITIES: dict[tuple[str, int], float] = {
    ("200ft", 1): 160,
    ("200ft", 2): 140,
    ("qtr_mi", 1): 120,
    ("qtr_mi", 2): 100,
    ("half_mi", 1): 100,
    ("half_mi", 2): 80,
}
