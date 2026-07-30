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

# Parent grouping used to split the buildings into rasters. The MIX codes are
# grouped by whether the taxonomy's dominant use pair includes residential:
# MIX1, MIX2, MIX4 and MIX6 do, MIX3 and MIX5 (commercial/industrial) do not.
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
        "RES3",
        "RES4",
        "RES5",
        "MIX1",
        "MIX2",
        "MIX4",
        "MIX6",
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
    ],
    "mixed_nr": ["MIX3", "MIX5"],
    "industrial": ["IND", "IND1", "IND2"],
    "agriculture": ["AGR", "AGR1", "AGR2", "AGR3"],
    "government": ["GOV", "GOV1", "GOV2"],
    "education": ["EDU", "EDU1", "EDU2", "EDU3", "EDU4"],
    "assembly": ["ASS", "ASS1", "ASS2", "ASS3", "ASS4"],
    "unknown": ["UNK"],
}

# Inverted for per-building assignment.
OBM_OCCUPANCY_PARENTS = {
    code: parent
    for parent, codes in OBM_PARENT_BUILDING_TYPES.items()
    for code in codes
}
