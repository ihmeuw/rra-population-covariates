RAW_COVARIATES_ROOT = (
    "/mnt/team/rapidresponse/pub/population/data/01-raw-data/covariates/"
)
COVARIATES_ROOT = (
    "/mnt/team/rapidresponse/pub/population/data/02-processed-data/covariates/"
)

OVERTURE_OUTPUT_PATH_TEMPLATE = "/mnt/team/rapidresponse/pub/population/data/02-processed-data/covariates/overture/{covariate}/{class_key}.parquet"

DRIVABLE_CLASS_MAP = {
    "motorway": "motorway",  # Controlled-access highways or freeways for high-speed traffic
    "trunk": "trunk",  # Major roads intended for fast, long-distance traffic (below motorway)
    "primary": "primary",  # Major roads within cities or between towns; high capacity
    "secondary": "secondary",  # Roads connecting towns or neighborhoods; lower capacity than primary
    "tertiary": "tertiary",  # Roads connecting local roads or smaller settlements
    "residential": "residential,living_street",  # Residential roads + shared-use streets with pedestrian priority
    "service": "service",  # Access roads for properties, businesses, alleys, etc.
    "unclassified": "unclassified",  # Minor roads without a more specific classification
    "track": "track",  # Unpaved or rough roads, often rural or agricultural
    "unknown": "unknown",  # Class not identified; could be drivable, but should be verified
}

WATER_CLASS_MAP = {
    "stream_water": "stream",
    "river_water": "river",
    "inland_water": "lake,pond,oxbow,spring",
    "manmade_freshwater": "canal,basin,fishpond,reservoir",
    "waste_and_sewage": "wastewater,sewage,ditch,drain",
    "coastal_ocean": "ocean,sea",
    "coastal_inlet": "bay,strait,lagoon,tidal_channel",
}

OVERTURE_CLASS_MAPS = {
    "roads": DRIVABLE_CLASS_MAP,
    "water": WATER_CLASS_MAP,
}

# Open Building Map (Oostwegel et al. 2025), DOI 10.5880/GFZ.LKUT.2025.002.
OBM_ROOT_URL = (
    "https://datapub.gfz.de/download/10.5880.GFZ.LKUT.2025.002-Caweb/"
    "2025-002_Oostwegel-et-al_data/"
)
# The upstream release date; used to version the raw data directory.
OBM_VERSION = "2025-04-04"
# Building packages are published one per zoom-6 quadkey (1271 tiles). We fan the
# download out over zoom-2 quadkey prefixes, giving 16 groups of ~80 tiles each.
OBM_QUADKEY_PREFIXES = [f"{first}{second}" for first in "0123" for second in "0123"]
