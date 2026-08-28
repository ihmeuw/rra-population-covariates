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

OBM_ADDITIONAL_FILES_URL = (
    "https://datapub.gfz.de/download/10.5880.GFZ.LKUT.2025.002-Caweb/"
    "2025-002_Oostwegel-et-al_additional-files/"
)
# Reference tables published alongside the building data. C_occupancy_types.csv is
# the authoritative occupancy code list; D_overriding_occupancies.csv lists the
# codes assigned from an explicit source tag rather than inferred, and so marks
# the higher-confidence labels.
OBM_REFERENCE_FILES = [
    "A_Osmium_mapping.yaml",
    "B_building_and_POI_tags.csv",
    "C_occupancy_types.csv",
    "D_overriding_occupancies.csv",
    "E_KulbackLeibler_cities.csv",
]
OBM_OCCUPANCY_TYPES_FILE = "C_occupancy_types.csv"
OBM_OVERRIDING_OCCUPANCIES_FILE = "D_overriding_occupancies.csv"

# Occupancy labels from the GEM Building Taxonomy v2.0, Table 6 (Occupancy), which
# C_occupancy_types.csv reproduces. Note that the legend on
# https://www.openbuildingmap.org/map.html disagrees with the taxonomy for RES3-RES5,
# COM4-COM11 and the MIX codes; the taxonomy is authoritative. Open Building Map
# collapses the taxonomy's "*99, unknown type" codes into the bare parent code and
# uses its own "UNK" in place of the taxonomy's "OC99".
OBM_OCCUPANCY_LABELS = {
    "RES": "Residential, unknown type",
    "RES1": "Single dwelling",
    "RES2": "Multi-unit, unknown type",
    "RES2A": "2 Units (duplex)",
    "RES2B": "3-4 Units",
    "RES2C": "5-9 Units",
    "RES2D": "10-19 Units",
    "RES2E": "20-49 Units",
    "RES2F": "50+ Units",
    "RES3": "Temporary lodging",
    "RES4": "Institutional housing",
    "RES5": "Mobile home",
    "COM": "Commercial and public, unknown type",
    "COM1": "Retail trade",
    "COM2": "Wholesale trade and storage (warehouse)",
    "COM3": "Offices, professional/technical services",
    "COM4": "Hospital/medical clinic",
    "COM5": "Entertainment",
    "COM6": "Public building",
    "COM7": "Covered parking garage",
    "COM8": "Bus station",
    "COM9": "Railway station",
    "COM10": "Airport",
    "COM11": "Recreation and leisure",
    "MIX1": "Mostly residential and commercial",
    "MIX2": "Mostly commercial and residential",
    "MIX3": "Mostly commercial and industrial",
    "MIX4": "Mostly residential and industrial",
    "MIX5": "Mostly industrial and commercial",
    "MIX6": "Mostly industrial and residential",
    "IND": "Industrial, unknown type",
    "IND1": "Heavy industrial",
    "IND2": "Light industrial",
    "AGR": "Agriculture, unknown type",
    "AGR1": "Produce storage",
    "AGR2": "Animal shelter",
    "AGR3": "Agricultural processing",
    "ASS": "Assembly, unknown type",
    "ASS1": "Religious gathering",
    "ASS2": "Arena",
    "ASS3": "Cinema or concert hall",
    "ASS4": "Other gatherings",
    "GOV": "Government, unknown type",
    "GOV1": "Government, general services",
    "GOV2": "Government, emergency response",
    "EDU": "Education, unknown type",
    "EDU1": "Pre-school facility",
    "EDU2": "School",
    "EDU3": "College/university, offices and/or classrooms",
    "EDU4": "College/university, research facilities and/or labs",
    "UNK": "Unknown",
}

# Parent types the buildings are split into, one raster per type per block. Codes
# follow their taxonomy prefix apart from RES3, which is placed by what it means for
# resident population rather than by prefix:
#
# - RES3 is grouped with commercial. Despite the RES prefix it is GEM "Temporary
#   lodging" (hotels, motels, guest lodges, cabins), which houses transient
#   occupants rather than residents. Note that the legend on
#   https://www.openbuildingmap.org/map.html mislabels RES3 as "Mixed residential".
# - RES4 ("Institutional housing": dormitories, barracks, care homes) stays with
#   residential, since those are permanent homes even though they are group quarters.
#
# The mixed-use codes are not listed here; they contribute fractionally to two parents
# each, see OBM_MIXED_USE_SPLITS.
OBM_PARENT_BUILDING_TYPES = {
    "residential_mu": [
        "RES",
        "RES1",
        "RES2",
        "RES2A",
        "RES2B",
        "RES2C",
        "RES2D",
        "RES2E",
        "RES2F",
        "RES4",
        "RES5",
    ],
    "commercial": [
        "COM",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "COM10",
        "COM11",
        "RES3",
    ],
    "industrial": ["IND", "IND1", "IND2"],
    "agriculture": ["AGR", "AGR1", "AGR2", "AGR3"],
    "government": ["GOV", "GOV1", "GOV2"],
    "education": ["EDU", "EDU1", "EDU2", "EDU3", "EDU4"],
    "assembly": ["ASS", "ASS1", "ASS2", "ASS3", "ASS4"],
    "unknown": ["UNK"],
}

# A mixed-use building is part one use and part another, so rather than assigning it
# wholly to one parent we split its footprint between the two the taxonomy names,
# giving 75% to the dominant use and 25% to the secondary one. Area is conserved
# because the weights sum to 1.
#
# MIX3 and MIX6 never occur in the 2025-04-04 release (a census of all 1271 tiles
# found zero of each) and are defensive entries only. MIX2 occurs 9524 times, 98.8%
# of it in East/Southern Africa where GEM-aligned field surveys recorded mixed use;
# it is real but too rare to shift a raster. The splits that carry weight are MIX1
# (4.07M buildings) and MIX4 (1.83M), both mostly residential, and MIX5 (724k).
OBM_MIXED_USE_SPLITS = {
    "MIX1": {"residential_mu": 0.75, "commercial": 0.25},
    "MIX2": {"commercial": 0.75, "residential_mu": 0.25},
    "MIX3": {"commercial": 0.75, "industrial": 0.25},
    "MIX4": {"residential_mu": 0.75, "industrial": 0.25},
    "MIX5": {"industrial": 0.75, "commercial": 0.25},
    "MIX6": {"industrial": 0.75, "residential_mu": 0.25},
}

# Pixel values are the fraction of the pixel covered by footprints of that type, so
# they normally fall in [0, 1]. A few can exceed 1, and it is worth knowing why rather
# than clipping them away:
#
# - Within a single layer this needs overlapping source polygons, because all the
#   whole-contribution codes for a parent are rasterized in one pass and therefore
#   unioned. Measured on the densest test block (B-0007X-0002Y at 100m, 29.1M land
#   pixels): zero pixels above 1 in any of the eight layers.
# - Summing the layers can exceed 1 where footprints of *different* types overlap,
#   which happens when an OSM building and an ML-derived footprint describe the same
#   structure. Measured on the same block: 22 of 1,554,305 covered pixels, 0.0014%,
#   maximum 1.610.
#
# A value above 1 therefore flags duplicated geometry in the source, not building
# density; multiple buildings in one pixel is the ordinary case and stays within
# [0, 1]. We leave the values unclipped so the artifact stays visible in QA, which
# means consumers must not assume a hard upper bound of 1.

# Every occupancy code mapped to the parent types it contributes to and by how much.
# This is what the rasterization uses.
OBM_OCCUPANCY_WEIGHTS = {
    **{
        code: {parent: 1.0}
        for parent, codes in OBM_PARENT_BUILDING_TYPES.items()
        for code in codes
    },
    **OBM_MIXED_USE_SPLITS,
}

# The single parent each code belongs to most, for labelling and summaries. The
# rasters are built from OBM_OCCUPANCY_WEIGHTS, not from this.
OBM_OCCUPANCY_PARENTS = {
    code: max(weights, key=lambda parent: weights[parent])
    for code, weights in OBM_OCCUPANCY_WEIGHTS.items()
}

# Resolutions of the population model modeling frame, in meters. Passed without the
# trailing "m"; the population model data loaders append it.
OBM_RESOLUTIONS = ["100", "40"]

# Each block raster is written onto the grid of a building density tile, which also
# supplies the land mask. Any provider or measure yields the same grid and mask;
# these match the choice made in the population model's feature generation.
OBM_TEMPLATE_PROVIDER = "microsoft_v4"
OBM_TEMPLATE_MEASURE = "density"
OBM_TEMPLATE_TIME_POINT = "2023q4"

# Building footprints are far smaller than a pixel (median 127 m2, against 10000 m2
# at 100m and 1600 m2 at 40m), so a binary rasterization either inflates every
# building to a whole pixel or drops it entirely. Instead we rasterize onto a grid
# this many times finer than the target and average down, giving the fraction of each
# pixel covered by footprints. Area is preserved exactly and precision is 1/100.
OBM_SUPERSAMPLE_FACTOR = 10

# Two measures are written per parent building type:
#   density  the fraction of the pixel covered by footprints of that type (0-1). This is
#            the direct analogue of GHSL's built-up fraction (BUFRAC).
#   volume   height * density, in meters of built volume per m2 of ground. Same units and
#            convention as GHSL's built-up volume, which is ANBH * BUSURF.
OBM_MEASURES = ["density", "volume"]

# Height for the volume measure comes from GHSL rather than from OBM's own height
# column. OBM's heights are themselves derived from GHSL R2023A (Oostwegel et al. 2025,
# section 3), and OBM records them as GEM taxonomy storey *ranges* - about 88% of
# buildings carry a range such as "HBET:1-5" rather than a value. Reading GHSL directly
# follows OBM's own convention while avoiding a guessed range midpoint and a guessed
# meters-per-storey factor. The consequence is that our volume is not independent of
# GHSL in the height dimension; density remains fully independent.
OBM_HEIGHT_PROVIDER = "ghsl_r2023a"
OBM_HEIGHT_MEASURE = "height"
OBM_HEIGHT_TIME_POINT = "2025q1"

# Where GHSL reports no height we substitute a single storey. GHSL has no concept of a
# storey - its height (ANBH) is a continuous value in meters - but it has a hard
# empirical floor: the smallest non-zero ANBH measured is 2.4861 m in the Sao Paulo
# block and 2.4864 m in Tokyo, with the low-rise mass sitting just above 2.50 m. The
# population model uses HEIGHT_MIN = 2.4384 m (8 ft) for the same crosswalk. Those
# agree to within 5 cm, so 2.5 m is the defensible one-storey height.
#
# This only ever applies where GHSL sees no building at all: measured across two blocks,
# no pixel has GHSL density > 0 with height == 0, so GHSL never omits a height where it
# reports surface. The affected pixels are those where OBM has footprints and GHSL has
# nothing, and the per-block count is logged so the total is reportable.
OBM_ONE_STOREY_HEIGHT_M = 2.5
