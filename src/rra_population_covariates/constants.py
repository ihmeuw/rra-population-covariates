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
    "coastal_marine": "ocean,sea,bay,strait,lagoon,tidal_channel",
}

OVERTURE_CLASS_MAPS = {
    "roads": DRIVABLE_CLASS_MAP,
    "water": WATER_CLASS_MAP,
}
