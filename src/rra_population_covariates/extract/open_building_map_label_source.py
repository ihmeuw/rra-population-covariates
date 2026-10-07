"""Classify every Open Building Map footprint by where its occupancy label came from.

OBM labels an untagged footprint from the OSM land-use zone it sits in, but does not
record when it has done so. We reconstruct that here, in three stages:

1. Overture lookups (one task per Overture source file). The OSM IDs of Overture's
   buildings and building parts, with each building's own class, and the land-use
   zones that map to a parent building type. Both are written to the raw OBM
   directory, partitioned by OBM quadkey.
2. A table of the Overture land-use classes found, against the mapping in
   OBM_LAND_USE_PARENTS, so that a class missing from Overture is visible.
3. Classification (one task per OBM tile). Every footprint gets a label_source of
   unknown, tagged or inherited; see OBM_LABEL_SOURCES in constants.py.
"""

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click
import geopandas as gpd  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pyogrio  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]
from rra_tools import jobmon
from rra_tools.shell_tools import mkdir, touch

from rra_population_covariates import cli_options as clio
from rra_population_covariates import constants as pcc
from rra_population_covariates.data import CovariateData, RawCovariateData
from rra_population_covariates.process.open_building_map import (
    check_occupancy_codes,
    list_local_quadkeys,
)

# Overture source datasets read in stage 1, as (theme, type). The lookup each one
# feeds is named after the type: building and building_part both feed "buildings".
OVERTURE_TYPES = {
    "building": ("buildings", "building"),
    "building_part": ("buildings", "building_part"),
    "land_use": ("base", "land_use"),
}
LOOKUPS = {
    "building": "buildings",
    "building_part": "buildings",
    "land_use": "land_use",
}
BATCH_SIZE = 1_000_000
# Classification memory by OBM tile size on disk, as (minimum GiB, memory), largest
# first. Tiles range from kilobytes to 31 GB.
CLASSIFY_MEMORY = [(10, "60G"), (2, "30G"), (0, "15G")]
# Overture's OSM record IDs read "w<id>@<version>" for ways and "r<id>@<version>"
# for relations. OBM stores a relation's ID negated, so we do the same and the two
# join directly on OBM's `id`.
OSM_RECORD_PATTERN = r"^(?P<kind>[wr])(?P<osm_id>\d+)@"

LABEL_SOURCES = ["unknown", *pcc.OBM_LABEL_SOURCES]
LABEL_SOURCE_TYPE = pa.dictionary(pa.int8(), pa.string())
CLASSIFIED_SCHEMA = pa.schema(
    [
        ("fid", pa.int64()),
        ("id", pa.int64()),
        ("source_id", pa.int8()),
        ("occupancy", pa.string()),
        ("parent", pa.string()),
        ("has_own_class", pa.bool_()),
        ("zone_match", pa.bool_()),
        ("label_source", LABEL_SOURCE_TYPE),
    ]
)
PARENT_CODES = {parent: i for i, parent in enumerate(pcc.OBM_PARENT_BUILDING_TYPES)}


###########
# Tiling  #
###########


def _quadkey_grid(zoom: int = pcc.OBM_QUADKEY_ZOOM) -> np.ndarray:
    """Quadkey of every tile at a zoom, indexed [tile_y, tile_x]."""
    n = 2**zoom
    grid = np.empty((n, n), dtype=object)
    for y in range(n):
        for x in range(n):
            grid[y, x] = "".join(
                str(((x >> (zoom - 1 - i)) & 1) | (((y >> (zoom - 1 - i)) & 1) << 1))
                for i in range(zoom)
            )
    return grid


def _tile_x(lon: np.ndarray, n: int) -> np.ndarray:
    x = np.floor((np.asarray(lon, dtype=np.float64) + 180.0) / 360.0 * n)
    return np.clip(x, 0, n - 1).astype(np.int64)


def _tile_y(lat: np.ndarray, n: int) -> np.ndarray:
    # Web Mercator stops at about +/-85.05 degrees; clip so the poles land in the edge
    # rows instead of overflowing.
    max_lat = math.degrees(math.atan(math.sinh(math.pi)))
    lat_rad = np.radians(np.clip(np.asarray(lat, dtype=np.float64), -max_lat, max_lat))
    y = np.floor((1.0 - np.arcsinh(np.tan(lat_rad)) / math.pi) / 2.0 * n)
    return np.clip(y, 0, n - 1).astype(np.int64)


def bbox_quadkeys(
    bbox: pa.StructArray,
    zoom: int = pcc.OBM_QUADKEY_ZOOM,
) -> tuple[np.ndarray, np.ndarray]:
    """Get every quadkey each bounding box touches.

    A feature is filed under every tile its bounding box touches rather than one, so
    that whichever tile OBM put the matching footprint in, the feature is there too.
    Features crossing a tile edge are the only ones duplicated.

    Returns the row index of each (feature, quadkey) pair and the quadkey.
    """
    n = 2**zoom
    grid = _quadkey_grid(zoom)
    x0 = _tile_x(pc.struct_field(bbox, "xmin").to_numpy(), n)
    x1 = _tile_x(pc.struct_field(bbox, "xmax").to_numpy(), n)
    # Tile rows count down from the north.
    y0 = _tile_y(pc.struct_field(bbox, "ymax").to_numpy(), n)
    y1 = _tile_y(pc.struct_field(bbox, "ymin").to_numpy(), n)

    nx = x1 - x0 + 1
    counts = nx * (y1 - y0 + 1)
    rows = np.repeat(np.arange(len(counts)), counts)
    offset = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    tile_x = x0[rows] + offset % nx[rows]
    tile_y = y0[rows] + offset // nx[rows]
    return rows, grid[tile_y, tile_x]


def write_partitions(
    rcov_data: RawCovariateData,
    lookup: str,
    source_stem: str,
    table: pa.Table,
    quadkeys: np.ndarray,
) -> pd.Series:
    """Write one part per quadkey and return the row count of each."""
    if not len(quadkeys):
        return pd.Series([], dtype=np.int64, name="n_rows")
    order = np.argsort(quadkeys, kind="stable")
    quadkeys = quadkeys[order]
    table = table.take(pa.array(order))
    unique, starts = np.unique(quadkeys, return_index=True)
    ends = [*starts[1:], len(quadkeys)]
    for quadkey, start, end in zip(unique, starts, ends, strict=True):
        path = rcov_data.obm_overture_lookup_part_path(lookup, quadkey, source_stem)
        mkdir(path.parent, exist_ok=True, parents=True)
        touch(path, clobber=True)
        pq.write_table(table.slice(start, end - start), path)
    return pd.Series(np.subtract(ends, starts), index=unique, name="n_rows")


#####################
# Overture lookups  #
#####################


def osm_records(sources: pa.ListArray) -> tuple[np.ndarray, np.ndarray, int]:
    """Get the OSM records among a batch's sources.

    Returns the row each record belongs to, its signed OSM ID, and the number of OSM
    records whose ID could not be parsed. A row can have several OSM records.
    """
    flat = pc.list_flatten(sources)
    rows = pc.list_parent_indices(sources)
    is_osm = pc.equal(pc.struct_field(flat, "dataset"), "OpenStreetMap")
    record_ids = pc.struct_field(flat, "record_id").filter(is_osm)
    rows = rows.filter(is_osm)

    parsed = pc.extract_regex(record_ids, OSM_RECORD_PATTERN)
    valid = parsed.is_valid()
    n_unparsed = len(parsed) - pc.sum(valid).as_py() if len(parsed) else 0
    parsed = parsed.filter(valid)
    rows = rows.filter(valid).to_numpy()

    osm_id = pc.cast(pc.struct_field(parsed, "osm_id"), pa.int64()).to_numpy()
    is_relation = pc.equal(pc.struct_field(parsed, "kind"), "r").to_numpy(
        zero_copy_only=False
    )
    return rows, np.where(is_relation, -osm_id, osm_id), n_unparsed


def overture_buildings_main(
    rcov_data: RawCovariateData,
    overture_type: str,
    path: Path,
    local_quadkeys: set[str],
) -> pd.DataFrame:
    """Extract the OSM ID and own class of every OSM-sourced Overture building.

    Overture's class is OSM's building=* value, and null for building=yes, which
    counts as no own class. Building parts carry no class in Overture; they are kept
    so the ID match rate counts them, and never give a footprint an own class.
    """
    is_part = overture_type == "building_part"
    columns = ["sources", "bbox"] + ([] if is_part else ["class"])

    frames, n_rows, n_unparsed = [], 0, 0
    for batch in pq.ParquetFile(path).iter_batches(BATCH_SIZE, columns=columns):
        n_rows += batch.num_rows
        rows, osm_id, unparsed = osm_records(batch.column("sources"))
        n_unparsed += unparsed
        if is_part:
            building_class = pa.nulls(len(rows), pa.string())
        else:
            building_class = batch.column("class").take(pa.array(rows))
        bbox = batch.column("bbox").take(pa.array(rows))
        pair_rows, quadkeys = bbox_quadkeys(bbox)
        frames.append(
            pd.DataFrame(
                {
                    "quadkey": quadkeys,
                    "osm_id": osm_id[pair_rows],
                    "class": building_class.take(pa.array(pair_rows)).to_pandas(),
                    "is_part": is_part,
                }
            )
        )

    records = pd.concat(frames, ignore_index=True)
    records = records[records["quadkey"].isin(local_quadkeys)]
    table = pa.Table.from_pandas(
        records.drop(columns="quadkey"),
        schema=pa.schema(
            [("osm_id", pa.int64()), ("class", pa.string()), ("is_part", pa.bool_())]
        ),
        preserve_index=False,
    )
    counts = write_partitions(
        rcov_data, "buildings", path.stem, table, records["quadkey"].to_numpy()
    )
    click.echo(
        f"{path.name}: {n_rows:,} rows, {len(records):,} OSM records filed in "
        f"{len(counts)} tiles, {n_unparsed:,} unparsed OSM record IDs."
    )
    return pd.DataFrame(
        {
            "source": path.name,
            "quadkey": counts.index,
            "n_rows": counts.to_numpy(),
            "n_source_rows": n_rows,
            "n_unparsed": n_unparsed,
        }
    )


def overture_land_use_main(
    rcov_data: RawCovariateData,
    path: Path,
    local_quadkeys: set[str],
) -> pd.DataFrame:
    """Extract the Overture land-use zones that map to a parent building type.

    Returns a census of every class in the file, mapped or not, with how many of
    its zones come from a source other than OpenStreetMap.
    """
    columns = ["class", "geometry", "bbox", "sources"]
    frames, census = [], []
    for batch in pq.ParquetFile(path).iter_batches(BATCH_SIZE, columns=columns):
        sources = batch.column("sources")
        non_osm = pc.not_equal(
            pc.struct_field(pc.list_flatten(sources), "dataset"), "OpenStreetMap"
        )
        non_osm_rows = np.unique(
            pc.list_parent_indices(sources).filter(non_osm).to_numpy()
        )
        is_non_osm = np.zeros(batch.num_rows, dtype=bool)
        is_non_osm[non_osm_rows] = True
        census.append(
            pd.DataFrame(
                {"class": batch.column("class").to_pandas(), "non_osm": is_non_osm}
            )
        )

        keep = pc.is_in(batch.column("class"), pa.array(list(pcc.OBM_LAND_USE_PARENTS)))
        zones = batch.filter(keep)
        pair_rows, quadkeys = bbox_quadkeys(zones.column("bbox"))
        zone_class = zones.column("class").take(pa.array(pair_rows)).to_pandas()
        frames.append(
            pd.DataFrame(
                {
                    "quadkey": quadkeys,
                    "class": zone_class,
                    "parent": zone_class.map(pcc.OBM_LAND_USE_PARENTS),
                    "geometry": zones.column("geometry")
                    .take(pa.array(pair_rows))
                    .to_pandas(),
                }
            )
        )

    records = pd.concat(frames, ignore_index=True)
    records = records[records["quadkey"].isin(local_quadkeys)]
    # Geometry stays as WKB, so these are plain parquet, read back with shapely.
    table = pa.Table.from_pandas(
        records.drop(columns="quadkey"),
        schema=pa.schema(
            [("class", pa.string()), ("parent", pa.string()), ("geometry", pa.binary())]
        ),
        preserve_index=False,
    )
    counts = write_partitions(
        rcov_data, "land_use", path.stem, table, records["quadkey"].to_numpy()
    )
    census_counts = (
        pd.concat(census, ignore_index=True)
        .groupby("class", dropna=False)["non_osm"]
        .agg(n_zones="size", n_non_osm="sum")
        .reset_index()
    )
    click.echo(
        f"{path.name}: {len(records):,} mapped zones filed in {len(counts)} tiles."
    )
    return census_counts.assign(source=path.name)


def open_building_map_overture_lookup_main(
    overture_type: str,
    overture_file: str,
    raw_covariate_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    rcov_data = RawCovariateData(raw_covariate_dir)
    path = rcov_data.overture.joinpath(
        *(
            f"{k}={v}"
            for k, v in zip(
                ["theme", "type"], OVERTURE_TYPES[overture_type], strict=True
            )
        ),
        overture_file,
    )
    lookup = LOOKUPS[overture_type]
    marker = rcov_data.obm_overture_lookup_marker_path(lookup, path.stem)
    if marker.exists() and not overwrite:
        click.echo(f"{marker} already exists; skipping.")
        return

    local_quadkeys = set(list_local_quadkeys(rcov_data))
    if lookup == "buildings":
        summary = overture_buildings_main(
            rcov_data, overture_type, path, local_quadkeys
        )
    else:
        summary = overture_land_use_main(rcov_data, path, local_quadkeys)

    # Written last: its presence means every part for this source file is complete.
    mkdir(marker.parent, exist_ok=True, parents=True)
    touch(marker, clobber=True)
    summary.to_parquet(marker, index=False)


def write_land_use_class_table(rcov_data: RawCovariateData) -> pd.DataFrame:
    """Tabulate the Overture land-use classes against OBM_LAND_USE_PARENTS.

    A mapped class that Overture doesn't have silently turns its zones' footprints
    into tagged, so it is reported loudly here rather than discovered later.
    """
    census = pd.concat(
        [
            pd.read_parquet(p)
            for p in rcov_data.list_obm_overture_lookup_markers("land_use")
        ],
        ignore_index=True,
    )
    table = (
        census.groupby("class", dropna=False)[["n_zones", "n_non_osm"]]
        .sum()
        .reset_index()
    )
    mapped = pd.DataFrame(
        {
            "class": list(pcc.OBM_LAND_USE_PARENTS),
            "parent": list(pcc.OBM_LAND_USE_PARENTS.values()),
        }
    )
    table = table.merge(mapped, on="class", how="outer")
    table[["n_zones", "n_non_osm"]] = (
        table[["n_zones", "n_non_osm"]].fillna(0).astype(int)
    )
    table = table.sort_values(["parent", "n_zones"], ascending=[True, False])

    path = rcov_data.obm_land_use_classes_path
    touch(path, clobber=True)
    table.to_csv(path, index=False)

    missing = sorted(
        table.loc[table["parent"].notna() & (table["n_zones"] == 0), "class"]
    )
    if missing:
        click.echo(
            f"WARNING: mapped land-use classes absent from Overture: {missing}. "
            "Footprints in these zones will be classified as tagged."
        )
    n_non_osm = int(table.loc[table["parent"].notna(), "n_non_osm"].sum())
    click.echo(f"Mapped zones with a non-OSM source: {n_non_osm:,}. Table: {path}")
    return table


###################
# Classification  #
###################


def load_overture_ids(
    rcov_data: RawCovariateData, quadkey: str
) -> tuple[np.ndarray, np.ndarray]:
    """Get the sorted OSM IDs Overture knows in a tile, and those with an own class."""
    parts = rcov_data.list_obm_overture_lookup_parts("buildings", quadkey)
    if not parts:
        empty = np.array([], dtype=np.int64)
        return empty, empty
    records = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    own_class = records["class"].notna() & ~records["is_part"]
    return (
        np.unique(records["osm_id"].to_numpy()),
        np.unique(records.loc[own_class, "osm_id"].to_numpy()),
    )


def load_zones(
    rcov_data: RawCovariateData, quadkey: str
) -> tuple[shapely.STRtree, np.ndarray]:
    """Get a spatial index of a tile's land-use zones and each zone's parent code."""
    parts = rcov_data.list_obm_overture_lookup_parts("land_use", quadkey)
    if not parts:
        return shapely.STRtree([]), np.array([], dtype=np.int8)
    zones = pd.concat(
        [pd.read_parquet(p, columns=["parent", "geometry"]) for p in parts],
        ignore_index=True,
    )
    geometry = shapely.from_wkb(zones["geometry"].to_numpy())
    parent = zones["parent"].map(PARENT_CODES).to_numpy(dtype=np.int8)
    return shapely.STRtree(geometry), parent


def zone_match(
    centroids: np.ndarray,
    parent: np.ndarray,
    tree: shapely.STRtree,
    zone_parent: np.ndarray,
) -> np.ndarray:
    """True where a centroid lies in any zone of its own parent type.

    Zones may overlap, so a centroid can be in several; one match is enough.
    """
    matched = np.zeros(len(centroids), dtype=bool)
    if not len(zone_parent) or not len(centroids):
        return matched
    point_idx, zone_idx = tree.query(centroids, predicate="within")
    same = zone_parent[zone_idx] == parent[point_idx]
    matched[point_idx[same]] = True
    return matched


def classify_chunk(
    buildings: gpd.GeoDataFrame,
    known_ids: np.ndarray,
    own_class_ids: np.ndarray,
    tree: shapely.STRtree,
    zone_parent: np.ndarray,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Classify a chunk of footprints.

    Returns the classified table, each footprint's area in m2 and whether its OSM ID
    is known to Overture, the last two for the summary only.
    """
    occupancy = buildings["occupancy"]
    check_occupancy_codes(occupancy)
    parent = occupancy.map(pcc.OBM_OCCUPANCY_PARENTS)
    source_id = buildings["source_id"].to_numpy()
    osm_id = buildings["id"].to_numpy()

    # Google and Microsoft outlines are ML-derived and never carry an OSM tag.
    is_osm = source_id == 0
    known = is_osm & np.isin(osm_id, known_ids, assume_unique=False)
    has_own_class = is_osm & np.isin(osm_id, own_class_ids, assume_unique=False)

    equal_area = buildings.geometry.to_crs(pcc.OBM_EQUAL_AREA_CRS)
    area = equal_area.area.to_numpy()
    centroids = equal_area.centroid.to_crs("EPSG:4326").to_numpy()

    unknown = (occupancy == "UNK").to_numpy()
    labelled = np.flatnonzero(~unknown)
    in_zone = np.zeros(len(buildings), dtype=bool)
    in_zone[labelled] = zone_match(
        centroids[labelled],
        parent.iloc[labelled].map(PARENT_CODES).to_numpy(dtype=np.int8),
        tree,
        zone_parent,
    )
    mixed = occupancy.isin(pcc.OBM_MIXED_USE_SPLITS).to_numpy()

    # Order matters: an own class or a mixed-use code wins over a matching zone.
    label_source = np.select(
        [unknown, mixed, has_own_class, in_zone],
        ["unknown", "tagged", "tagged", "inherited"],
        default="tagged",
    )
    classified = pd.DataFrame(
        {
            "fid": buildings.index.to_numpy(dtype=np.int64),
            "id": osm_id.astype(np.int64),
            "source_id": source_id.astype(np.int8),
            "occupancy": occupancy.to_numpy(),
            "parent": parent.to_numpy(),
            "has_own_class": has_own_class,
            "zone_match": in_zone,
            "label_source": label_source,
        }
    )
    return classified, area, known


def to_table(classified: pd.DataFrame) -> pa.Table:
    # Encode label_source against a fixed dictionary so every chunk agrees.
    codes = pd.Categorical(classified["label_source"], categories=LABEL_SOURCES).codes
    label_source = pa.DictionaryArray.from_arrays(
        pa.array(codes, type=pa.int8()), pa.array(LABEL_SOURCES)
    )
    arrays = [
        pa.array(classified[name].to_numpy(), type=CLASSIFIED_SCHEMA.field(name).type)
        for name in CLASSIFIED_SCHEMA.names[:-1]
    ]
    return pa.Table.from_arrays([*arrays, label_source], schema=CLASSIFIED_SCHEMA)


def open_building_map_label_source_main(
    quadkey: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)

    out_path = cov_data.open_building_map_classified_path(quadkey)
    summary_path = cov_data.open_building_map_classified_summary_path(quadkey)
    if out_path.exists() and summary_path.exists() and not overwrite:
        click.echo(f"{out_path} already exists; skipping.")
        return

    tile_path = rcov_data.open_building_map_path(quadkey)
    n_features = pyogrio.read_info(tile_path, layer="building")["features"]
    known_ids, own_class_ids = load_overture_ids(rcov_data, quadkey)
    tree, zone_parent = load_zones(rcov_data, quadkey)
    click.echo(
        f"{quadkey}: {n_features:,} footprints, {len(known_ids):,} Overture OSM IDs "
        f"({len(own_class_ids):,} with a class), {len(zone_parent):,} zones."
    )

    # Write to a temporary path and rename, so an interrupted task never leaves a
    # truncated table that looks complete.
    mkdir(out_path.parent, exist_ok=True, parents=True)
    tmp_path = out_path.with_suffix(".parquet.tmp")
    summaries, osm_ids, n_written = [], [], 0
    with pq.ParquetWriter(tmp_path, CLASSIFIED_SCHEMA) as writer:
        for skip in range(0, n_features, pcc.OBM_CLASSIFY_CHUNK_SIZE):
            buildings = gpd.read_file(
                tile_path,
                layer="building",
                columns=["id", "source_id", "occupancy"],
                skip_features=skip,
                max_features=pcc.OBM_CLASSIFY_CHUNK_SIZE,
                fid_as_index=True,
            )
            classified, area, known = classify_chunk(
                buildings, known_ids, own_class_ids, tree, zone_parent
            )
            writer.write_table(to_table(classified))
            n_written += len(classified)
            osm_ids.append(
                classified.loc[classified["source_id"] == 0, "id"].to_numpy()
            )
            summaries.append(
                classified[["source_id", "label_source", "zone_match"]]
                .assign(area_m2=area, overture_match=known)
                .groupby(["source_id", "label_source"])
                .agg(
                    n_footprints=("area_m2", "size"),
                    area_m2=("area_m2", "sum"),
                    n_overture_match=("overture_match", "sum"),
                    n_zone_match=("zone_match", "sum"),
                )
                .reset_index()
            )
            click.echo(f"{quadkey}: classified {n_written:,} / {n_features:,}")

    # Totals are preserved: every footprint in the tile is classified exactly once.
    if n_written != n_features:
        tmp_path.unlink(missing_ok=True)
        msg = f"{quadkey}: classified {n_written:,} rows of {n_features:,}."
        raise RuntimeError(msg)
    touch(out_path, clobber=True)
    tmp_path.replace(out_path)

    summary = (
        pd.concat(summaries, ignore_index=True)
        .groupby(["source_id", "label_source"], as_index=False)
        .sum()
        .assign(quadkey=quadkey)
    )
    mkdir(summary_path.parent, exist_ok=True, parents=True)
    touch(summary_path, clobber=True)
    summary.to_parquet(summary_path, index=False)

    # The join key is fid; OBM's id is only checked, since it is not guaranteed unique.
    osm = np.concatenate(osm_ids) if osm_ids else np.array([], dtype=np.int64)
    n_duplicate = len(osm) - len(np.unique(osm))
    osm_summary = summary[summary["source_id"] == 0]
    match_rate = osm_summary["n_overture_match"].sum() / max(
        osm_summary["n_footprints"].sum(), 1
    )
    click.echo(
        f"{quadkey}: OSM ID match rate {match_rate:.4f}, {n_duplicate:,} duplicate "
        f"OSM ids.\n{summary.to_string(index=False)}"
    )


#########
# Tasks #
#########


@click.command()
@clio.with_choice(
    "overture_type",
    allow_all=False,
    choices=list(OVERTURE_TYPES),
    help="Overture dataset the source file belongs to.",
)
@click.option(
    "--overture-file",
    type=click.STRING,
    required=True,
    help="Name of the Overture parquet file to extract.",
)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_overwrite()
def open_building_map_overture_lookup_task(
    overture_type: str,
    overture_file: str,
    raw_covariate_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    """Extract the Overture lookup for one Overture source file."""
    open_building_map_overture_lookup_main(
        overture_type, overture_file, raw_covariate_dir, overwrite=overwrite
    )


@click.command()
@clio.with_obm_quadkey()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_overwrite()
def open_building_map_label_source_task(
    obm_quadkey: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    """Classify the footprints of one Open Building Map tile by label source."""
    open_building_map_label_source_main(
        obm_quadkey, raw_covariate_dir, output_dir, overwrite=overwrite
    )


def _run(  # noqa: PLR0913
    task_name: str,
    node_arg_names: tuple[str, ...],
    jobs: Sequence[tuple[str, ...]],
    task_args: dict[str, Any],
    resources: dict[str, Any],
    log_root: Path,
    per_task_resources: Any = None,
) -> None:
    if not jobs:
        click.echo(f"{task_name}: nothing to run.")
        return
    click.echo(f"{task_name}: {len(jobs)} tasks.")
    status = jobmon.run_parallel(
        runner="pctask extract",
        task_name=task_name,
        flat_node_args=(node_arg_names, jobs),
        task_args=task_args,
        task_resources={"project": "proj_rapidresponse", **resources},
        per_task_resources=per_task_resources,
        max_attempts=3,
        log_root=log_root,
    )
    if status != "D":
        msg = f"{task_name} workflow finished with status {status}."
        raise RuntimeError(msg)


@click.command()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_overwrite()
@clio.with_queue()
def open_building_map_label_source(
    raw_covariate_dir: str,
    output_dir: str,
    queue: str,
    *,
    overwrite: bool = False,
) -> None:
    """Classify every Open Building Map footprint by label source.

    Builds the Overture lookups first, then classifies every tile. Completed work is
    skipped unless --overwrite is given, so a failed run can simply be restarted.
    """
    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)
    flag: dict[str, Any] = {"overwrite": None} if overwrite else {}

    # Stage 1: Overture lookups, one task per Overture source file.
    lookup_jobs = []
    for overture_type, (theme, theme_type) in OVERTURE_TYPES.items():
        for path in sorted(rcov_data.list_overture_paths(theme, theme_type)):
            marker = rcov_data.obm_overture_lookup_marker_path(
                LOOKUPS[overture_type], path.stem
            )
            if overwrite or not marker.exists():
                lookup_jobs.append((overture_type, path.name))
    _run(
        "open_building_map_overture_lookup",
        ("overture-type", "overture-file"),
        lookup_jobs,
        {"raw-covariate-dir": raw_covariate_dir, **flag},
        {"queue": queue, "memory": "10G", "runtime": "2h"},
        rcov_data.log_dir("extract_open_building_map_overture_lookup"),
    )

    # Stage 2: the land-use class table.
    write_land_use_class_table(rcov_data)

    # Stage 3: classification, one task per OBM tile. Tiles range from kilobytes to
    # 31 GB, so memory scales with the tile.
    tile_sizes = {
        quadkey: rcov_data.open_building_map_path(quadkey).stat().st_size
        for quadkey in list_local_quadkeys(rcov_data)
    }
    classify_jobs = [
        (quadkey,)
        for quadkey in sorted(tile_sizes)
        if overwrite
        or not cov_data.open_building_map_classified_path(quadkey).exists()
        or not cov_data.open_building_map_classified_summary_path(quadkey).exists()
    ]

    def classify_resources(args: tuple[str, ...]) -> dict[str, Any]:
        size_gb = tile_sizes[args[0]] / 1024**3
        memory = next(mem for min_gb, mem in CLASSIFY_MEMORY if size_gb >= min_gb)
        return {"memory": memory}

    _run(
        "open_building_map_label_source",
        ("obm-quadkey",),
        classify_jobs,
        {"raw-covariate-dir": raw_covariate_dir, "output-dir": output_dir, **flag},
        {"queue": queue, "memory": "15G", "runtime": "12h"},
        cov_data.log_dir("extract_open_building_map_label_source"),
        per_task_resources=classify_resources,
    )

    summary = pd.concat(
        [
            pd.read_parquet(cov_data.open_building_map_classified_summary_path(q))
            for q in sorted(tile_sizes)
        ],
        ignore_index=True,
    )
    path = cov_data.open_building_map_classified / "summary.parquet"
    touch(path, clobber=True)
    summary.to_parquet(path, index=False)
    click.echo(f"Summary of {summary['quadkey'].nunique()} tiles written to {path}.")
