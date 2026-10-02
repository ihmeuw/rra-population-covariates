"""Correct OBM's occupancy labels where better evidence exists, per footprint.

OBM's label is kept unless one of these rules applies, first match wins:

1. Group-quarters class. The footprint's own OSM building class (via Overture) is a
   dormitory, presbytery or monastery: institutional housing (RES4).
2. Group-quarters site. Its centroid lies in an OSM prison, barracks, monastery,
   nursing-home or social-facility site polygon: RES4. Every source.
3. Group-quarters place inside it. It contains an Overture group-quarters place
   (prison, care home, shelter, convent, ...): RES4. Every source.
4. Group-quarters name. Its OSM name names a prison, barracks, residence hall or
   care home: RES4. OSM footprints only.
5. Group-quarters place nearby. It is the nearest footprint, within
   OBM_GROUP_QUARTERS_POI_RADIUS_M and unambiguously, to such a place: RES4.
6. Residential tag overridden. Its own class is residential but OBM's label is one
   of OBM's overriding occupancies (a ground-floor clinic, school, office, ...): mixed
   use, residential plus the override's parent, split by floors. Whole-site
   overrides and temporary lodging are left alone.
7. Mixed use. Every MIX code is re-expressed as two weighted shares. MIX1 and MIX4,
   mostly residential, are split by floors; the others keep OBM's 75/25.

Rules 2-5 skip a footprint whose own class is explicit and incompatible with group
quarters (see OBM_GROUP_QUARTERS_COMPATIBLE_CLASSES).

The floor split gives the non-residential use the ground floor: a residential share
of 1 - 1/floors, OBM_SINGLE_STOREY_RESIDENTIAL_SHARE for one storey, and
OBM_UNKNOWN_FLOORS_RESIDENTIAL_SHARE where floors are unknown.

Each share also carries a label source. A share backed by the footprint's own tag or
a POI is tagged. The dominant share of a MIX code is inherited when the footprint
sits in a zone of that use and its own tag does not support it, since OBM then took
that use from the zone.

The output is sparse: one row per footprint a rule touched, keyed on the GeoPackage
fid. Step B applies it with `--effective-occupancy`.
"""

import math
import re
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
from rra_tools.shell_tools import mkdir, touch

from rra_population_covariates import cli_options as clio
from rra_population_covariates import constants as pcc
from rra_population_covariates.data import CovariateData, RawCovariateData
from rra_population_covariates.extract.open_building_map_label_source import (
    BATCH_SIZE,
    _run,
    bbox_quadkeys,
    osm_records,
    write_partitions,
)
from rra_population_covariates.process.open_building_map import list_local_quadkeys

# Overture source datasets for this step's per-file lookups, as lookup: (theme, type).
LOOKUP_SOURCES = {
    "building_attributes": ("buildings", "building"),
    "group_quarters_places": ("places", "place"),
}
# The OSM site polygons come from one pass over the planet file.
OSM_SITES_LOOKUP = "osm_group_quarters_sites"
OSM_SITES_SOURCE = "planet"
# Only 38-47% of group-quarters places fall inside a footprint; most of the rest sit
# within 10-25 m, at an entrance or in a courtyard (measured on all 185,577 such places
# in OBM tiles). A place outside every footprint goes to the nearest footprint within
# this radius, unless the next nearest is less than POI_AMBIGUITY_RATIO times as far,
# in which case it is ambiguous and left unassigned. 25 m reaches about 88% of prisons.
OBM_GROUP_QUARTERS_POI_RADIUS_M = 25.0
POI_AMBIGUITY_RATIO = 1.5
# A closed ring needs four nodes (the first repeated last); a line needs two.
MIN_RING_NODES = 4
MIN_LINE_NODES = 2

WHOLE_RULES = [
    "group_quarters_class",
    "group_quarters_site",
    "group_quarters_place",
    "group_quarters_name",
    "group_quarters_place_nearby",
]
EFFECTIVE_SCHEMA = pa.schema(
    [
        ("fid", pa.int64()),
        ("rule", pa.string()),
        ("occupancy", pa.string()),
        ("floors", pa.float32()),
        ("floors_source", pa.string()),
        ("parent_1", pa.string()),
        ("weight_1", pa.float32()),
        ("source_1", pa.string()),
        ("parent_2", pa.string()),
        ("weight_2", pa.float32()),
        ("source_2", pa.string()),
    ]
)
NAME_PATTERN = re.compile(pcc.OBM_GROUP_QUARTERS_NAME_PATTERN, re.IGNORECASE)
NAME_EXCLUDE = re.compile(pcc.OBM_GROUP_QUARTERS_NAME_EXCLUDE, re.IGNORECASE)


def group_quarters_name(name: object) -> bool:
    if not isinstance(name, str):
        return False
    return bool(NAME_PATTERN.search(name)) and not NAME_EXCLUDE.search(name)


#####################
# Overture lookups  #
#####################


def building_attributes_main(
    rcov_data: RawCovariateData, path: Path, local_quadkeys: set[str]
) -> pd.DataFrame:
    """Extract floors, height and group-quarters names of OSM-sourced buildings."""
    frames = []
    columns = ["sources", "bbox", "num_floors", "height", "names"]
    for batch in pq.ParquetFile(path).iter_batches(BATCH_SIZE, columns=columns):
        names = pc.struct_field(batch.column("names"), "primary").to_pandas()
        gq_name = names.where(names.map(group_quarters_name))
        keep = (
            pc.is_valid(batch.column("num_floors")).to_numpy(zero_copy_only=False)
            | pc.is_valid(batch.column("height")).to_numpy(zero_copy_only=False)
            | gq_name.notna().to_numpy()
        )
        if not keep.any():
            continue
        batch = batch.filter(pa.array(keep))  # noqa: PLW2901
        gq_name = gq_name[keep].reset_index(drop=True)
        rows, osm_id, _ = osm_records(batch.column("sources"))
        bbox = batch.column("bbox").take(pa.array(rows))
        pair_rows, quadkeys = bbox_quadkeys(bbox)
        take = rows[pair_rows]
        frames.append(
            pd.DataFrame(
                {
                    "quadkey": quadkeys,
                    "osm_id": osm_id[pair_rows],
                    "num_floors": batch.column("num_floors")
                    .take(pa.array(take))
                    .to_pandas(),
                    "height": batch.column("height").take(pa.array(take)).to_pandas(),
                    "gq_name": gq_name.iloc[take].to_numpy(),
                }
            )
        )
    columns_out = ["quadkey", "osm_id", "num_floors", "height", "gq_name"]
    records = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=columns_out)
    )
    records = records[records["quadkey"].isin(local_quadkeys)]
    table = pa.Table.from_pandas(
        records.drop(columns="quadkey"),
        schema=pa.schema(
            [
                ("osm_id", pa.int64()),
                ("num_floors", pa.float32()),
                ("height", pa.float32()),
                ("gq_name", pa.string()),
            ]
        ),
        preserve_index=False,
    )
    counts = write_partitions(
        rcov_data,
        "building_attributes",
        path.stem,
        table,
        records["quadkey"].to_numpy(),
    )
    return pd.DataFrame({"quadkey": counts.index, "n_rows": counts.to_numpy()})


def write_features(  # noqa: PLR0913
    rcov_data: RawCovariateData,
    lookup: str,
    source_stem: str,
    features: pd.DataFrame,
    bounds: np.ndarray,
    local_quadkeys: set[str],
) -> pd.DataFrame:
    """File point or polygon features under every OBM tile their bounds touch."""
    bbox = pa.StructArray.from_arrays(
        [pa.array(bounds[:, i], pa.float32()) for i in (0, 2, 1, 3)],
        names=["xmin", "xmax", "ymin", "ymax"],
    )
    rows, quadkeys = bbox_quadkeys(bbox)
    records = features.iloc[rows].assign(quadkey=quadkeys)
    records = records[records["quadkey"].isin(local_quadkeys)]
    table = pa.Table.from_pandas(
        records.drop(columns="quadkey"),
        schema=pa.schema(
            [
                ("feature_id", pa.string()),
                ("kind", pa.string()),
                ("name", pa.string()),
                ("geometry", pa.binary()),
            ]
        ),
        preserve_index=False,
    )
    counts = write_partitions(
        rcov_data, lookup, source_stem, table, records["quadkey"].to_numpy()
    )
    return pd.DataFrame({"quadkey": counts.index, "n_rows": counts.to_numpy()})


def group_quarters_places_main(
    rcov_data: RawCovariateData, path: Path, local_quadkeys: set[str]
) -> pd.DataFrame:
    """Extract the group-quarters places in one Overture places file."""
    table = pq.read_table(path, columns=["id", "categories", "names", "geometry"])
    category = pc.struct_field(table.column("categories").combine_chunks(), "primary")
    table = table.filter(pc.is_in(category, pa.array(pcc.OBM_GROUP_QUARTERS_PLACES)))
    geometry = shapely.from_wkb(table.column("geometry").to_numpy(zero_copy_only=False))
    features = pd.DataFrame(
        {
            "feature_id": table.column("id").to_pandas(),
            "kind": pc.struct_field(
                table.column("categories").combine_chunks(), "primary"
            ).to_pandas(),
            "name": pc.struct_field(
                table.column("names").combine_chunks(), "primary"
            ).to_pandas(),
            "geometry": shapely.to_wkb(geometry),
        }
    )
    return write_features(
        rcov_data,
        "group_quarters_places",
        path.stem,
        features,
        shapely.bounds(geometry).reshape(-1, 4),
        local_quadkeys,
    )


def effective_lookup_main(
    lookup: str, overture_file: str, raw_covariate_dir: str, *, overwrite: bool = False
) -> None:
    rcov_data = RawCovariateData(raw_covariate_dir)
    theme, theme_type = LOOKUP_SOURCES[lookup]
    path = rcov_data.overture / f"theme={theme}" / f"type={theme_type}" / overture_file
    marker = rcov_data.obm_overture_lookup_marker_path(lookup, path.stem)
    if marker.exists() and not overwrite:
        click.echo(f"{marker} already exists; skipping.")
        return
    local_quadkeys = set(list_local_quadkeys(rcov_data))
    if lookup == "building_attributes":
        summary = building_attributes_main(rcov_data, path, local_quadkeys)
    else:
        summary = group_quarters_places_main(rcov_data, path, local_quadkeys)
    click.echo(
        f"{path.name}: {int(summary['n_rows'].sum()):,} rows in {len(summary)} tiles."
    )
    write_marker(marker, summary.assign(source=path.name))


def write_marker(marker: Path, summary: pd.DataFrame) -> None:
    # Written last: its presence means every part for this source is complete.
    mkdir(marker.parent, exist_ok=True, parents=True)
    touch(marker, clobber=True)
    summary.to_parquet(marker, index=False)


#################
# OSM site scan #
#################


def site_kind(tags: Any) -> str | None:
    """The group-quarters kind of an OSM object's tags, if it is one."""
    for key, value in pcc.OBM_GROUP_QUARTERS_SITE_TAGS:
        if tags.get(key) != value:
            continue
        if value == "social_facility":
            facility = tags.get("social_facility")
            if facility in pcc.OBM_GROUP_QUARTERS_SOCIAL_FACILITIES:
                return f"social_facility:{facility}"
            continue
        return str(value)
    return None


def osm_group_quarters_sites_main(  # noqa: C901, PLR0912, PLR0915
    raw_covariate_dir: str, *, overwrite: bool = False
) -> None:
    """Extract group-quarters site polygons from the OSM planet file.

    Four filtered passes, each over the whole file but with the filtering done in
    C++: tagged multipolygon relations, tagged closed ways, the relations' member
    ways, and the locations of every node those ways use. Polygons are then built
    with shapely, relations by assembling their member ways.
    """
    import osmium
    from osmium.filter import IdFilter, TagFilter

    rcov_data = RawCovariateData(raw_covariate_dir)
    marker = rcov_data.obm_overture_lookup_marker_path(
        OSM_SITES_LOOKUP, OSM_SITES_SOURCE
    )
    if marker.exists() and not overwrite:
        click.echo(f"{marker} already exists; skipping.")
        return
    planet = str(rcov_data.osm_planet)
    tag_filter = TagFilter(*pcc.OBM_GROUP_QUARTERS_SITE_TAGS)

    relations: dict[int, tuple[str, str | None, list[int]]] = {}
    rel: Any
    for rel in osmium.FileProcessor(planet, osmium.osm.RELATION).with_filter(
        tag_filter
    ):
        kind = site_kind(rel.tags)
        if kind and rel.tags.get("type") == "multipolygon":
            ways = [m.ref for m in rel.members if m.type == "w"]
            relations[rel.id] = (kind, rel.tags.get("name"), ways)
    click.echo(f"{len(relations):,} site relations.")

    closed: dict[int, tuple[str, str | None, list[int]]] = {}
    way: Any
    for way in osmium.FileProcessor(planet, osmium.osm.WAY).with_filter(tag_filter):
        kind = site_kind(way.tags)
        if kind and way.is_closed() and len(way.nodes) >= MIN_RING_NODES:
            closed[way.id] = (kind, way.tags.get("name"), [n.ref for n in way.nodes])
    click.echo(f"{len(closed):,} site ways.")

    member_ids = {w for _, _, ways in relations.values() for w in ways}
    members: dict[int, list[int]] = {}
    if member_ids:
        member_filter = IdFilter(member_ids)
        for way in osmium.FileProcessor(planet, osmium.osm.WAY).with_filter(
            member_filter
        ):
            members[way.id] = [n.ref for n in way.nodes]

    node_ids = {n for _, _, refs in closed.values() for n in refs}
    node_ids.update(n for refs in members.values() for n in refs)
    location: dict[int, tuple[float, float]] = {}
    node: Any
    for node in osmium.FileProcessor(planet, osmium.osm.NODE).with_filter(
        IdFilter(node_ids)
    ):
        if node.location.valid():
            location[node.id] = (node.location.lon, node.location.lat)
    click.echo(f"{len(location):,} of {len(node_ids):,} node locations found.")

    def coords(refs: list[int]) -> list[tuple[float, float]]:
        return [location[r] for r in refs if r in location]

    features, geometries = [], []
    for way_id, (kind, name, refs) in closed.items():
        ring = coords(refs)
        if len(ring) >= MIN_RING_NODES:
            features.append((f"w{way_id}", kind, name))
            geometries.append(shapely.make_valid(shapely.Polygon(ring)))
    for rel_id, (kind, name, ways) in relations.items():
        lines = [
            shapely.LineString(c)
            for w in ways
            if len(c := coords(members.get(w, []))) >= MIN_LINE_NODES
        ]
        if not lines:
            continue
        area = shapely.build_area(shapely.union_all(lines))
        if not area.is_empty:
            features.append((f"r{rel_id}", kind, name))
            geometries.append(area)

    geometry = np.asarray(geometries, dtype=object)
    frame = pd.DataFrame(features, columns=["feature_id", "kind", "name"]).assign(
        geometry=shapely.to_wkb(geometry)
    )
    click.echo(
        f"{len(frame):,} site polygons: {frame['kind'].value_counts().to_dict()}"
    )
    summary = write_features(
        rcov_data,
        OSM_SITES_LOOKUP,
        OSM_SITES_SOURCE,
        frame,
        shapely.bounds(geometry).reshape(-1, 4),
        set(list_local_quadkeys(rcov_data)),
    )
    write_marker(marker, summary.assign(source=rcov_data.osm_planet.name))


#########
# Rules #
#########


def residential_share(floors: np.ndarray) -> np.ndarray:
    """Residential share of a mixed building whose other use takes the ground floor."""
    floors = np.asarray(floors, dtype=np.float64)
    share = np.where(
        np.isnan(floors),
        pcc.OBM_UNKNOWN_FLOORS_RESIDENTIAL_SHARE,
        np.where(
            floors <= 1,
            pcc.OBM_SINGLE_STOREY_RESIDENTIAL_SHARE,
            1 - 1 / np.maximum(floors, 1),
        ),
    )
    return share.astype(np.float32)


def tag_parents(rcov_data: RawCovariateData) -> pd.Series:
    """The parent OBM's own tag table gives each building=* value, where it gives one."""
    tags = pd.read_csv(
        rcov_data.open_building_map_reference_path("B_building_and_POI_tags.csv")
    )
    building = tags[tags["key"] == "building"].drop_duplicates("value")
    first_code = building.set_index("value")["GEM_occupancy"].str.split("|").str[0]
    return first_code.map(pcc.OBM_OCCUPANCY_PARENTS).dropna()


def load_lookup(
    rcov_data: RawCovariateData, lookup: str, quadkey: str, columns: list[str]
) -> pd.DataFrame:
    parts = rcov_data.list_obm_overture_lookup_parts(lookup, quadkey)
    if not parts:
        return pd.DataFrame(columns=columns)
    return pd.concat(
        [pd.read_parquet(p, columns=columns) for p in parts], ignore_index=True
    )


def tile_attributes(rcov_data: RawCovariateData, quadkey: str) -> pd.DataFrame:
    """Floors, their source, and any group-quarters name, per OSM id."""
    lk = load_lookup(
        rcov_data,
        "building_attributes",
        quadkey,
        ["osm_id", "num_floors", "height", "gq_name"],
    )
    lk = lk.drop_duplicates("osm_id").set_index("osm_id")
    from_count = lk["num_floors"].where(lk["num_floors"] >= 1)
    from_height = np.maximum(np.round(lk["height"] / pcc.OBM_STOREY_HEIGHT_M), 1).where(
        lk["height"] > 0
    )
    source = pd.Series(None, index=lk.index, dtype=object)
    source[from_height.notna()] = "height"
    source[from_count.notna()] = "num_floors"
    return pd.DataFrame(
        {
            "floors": from_count.fillna(from_height),
            "floors_source": source,
            "gq_name": lk["gq_name"],
        }
    )


def footprints_near(
    rcov_data: RawCovariateData,
    quadkey: str,
    geometries: np.ndarray,
    pad_m: float = 0.0,
) -> gpd.GeoDataFrame:
    """Read the tile's footprints near each geometry, without reading the whole tile."""
    frames = []
    for geom in geometries:
        x0, y0, x1, y1 = geom.bounds
        lat = (y0 + y1) / 2
        dy = pad_m / 110_540
        dx = pad_m / (111_320 * max(math.cos(math.radians(lat)), 0.05))
        b = pyogrio.read_dataframe(
            rcov_data.open_building_map_path(quadkey),
            layer="building",
            columns=["occupancy"],
            bbox=(x0 - dx, y0 - dy, x1 + dx, y1 + dy),
            fid_as_index=True,
        )
        if len(b):
            frames.append(b)
    if not frames:
        return gpd.GeoDataFrame({"occupancy": []}, geometry=[], crs="EPSG:4326")
    near = gpd.GeoDataFrame(pd.concat(frames), crs="EPSG:4326")
    return near[~near.index.duplicated()]


def site_fids(rcov_data: RawCovariateData, quadkey: str) -> np.ndarray:
    """Footprints whose centroid lies in a group-quarters site polygon."""
    sites = load_lookup(rcov_data, OSM_SITES_LOOKUP, quadkey, ["geometry"])
    if sites.empty:
        return np.array([], dtype=np.int64)
    geoms = shapely.from_wkb(sites["geometry"].to_numpy())
    near = footprints_near(rcov_data, quadkey, geoms)
    if near.empty:
        return np.array([], dtype=np.int64)
    centroids = (
        near.geometry.to_crs(pcc.OBM_EQUAL_AREA_CRS)
        .centroid.to_crs("EPSG:4326")
        .to_numpy()
    )
    inside = np.unique(shapely.STRtree(geoms).query(centroids, predicate="within")[0])
    fids: np.ndarray = near.index.to_numpy(dtype=np.int64)[inside]
    return fids


def place_fids(
    rcov_data: RawCovariateData, quadkey: str, radius_m: float
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """Footprints containing a group-quarters place, and those nearest one within the radius."""
    places = load_lookup(rcov_data, "group_quarters_places", quadkey, ["geometry"])
    stats = {"places": len(places), "contained": 0, "nearby": 0, "unassigned": 0}
    empty = np.array([], dtype=np.int64)
    if places.empty:
        return empty, empty, stats
    points = shapely.from_wkb(places["geometry"].to_numpy())
    near = footprints_near(rcov_data, quadkey, points, pad_m=max(radius_m, 1.0))
    if near.empty:
        stats["unassigned"] = len(points)
        return empty, empty, stats
    geoms = near.geometry.to_numpy()
    fids = near.index.to_numpy(dtype=np.int64)
    contained, nearby = [], []
    for point in points:
        px, py = shapely.get_x(point), shapely.get_y(point)
        k = math.cos(math.radians(py))

        def local(
            xy: np.ndarray, px: float = px, py: float = py, k: float = k
        ) -> np.ndarray:
            return np.column_stack(
                [(xy[:, 0] - px) * 111_320 * k, (xy[:, 1] - py) * 110_540]
            )

        distance = shapely.distance(
            shapely.transform(geoms, local), shapely.Point(0, 0)
        )
        order = np.argsort(distance)
        if distance[order[0]] == 0:
            contained.extend(fids[distance == 0])
            stats["contained"] += 1
        elif distance[order[0]] <= radius_m and (
            len(order) == 1
            or distance[order[1]] >= POI_AMBIGUITY_RATIO * distance[order[0]]
        ):
            nearby.append(fids[order[0]])
            stats["nearby"] += 1
        else:
            stats["unassigned"] += 1
    return (
        np.unique(np.asarray(contained, dtype=np.int64)),
        np.unique(np.asarray(nearby, dtype=np.int64)),
        stats,
    )


def mixed_shares(c: pd.DataFrame, class_parent: pd.Series) -> pd.DataFrame:
    """Two weighted shares and their label sources for each mixed-use footprint."""
    mix = c["rule"] == "mixed_use"
    splits = c["occupancy"].where(mix).map(pcc.OBM_MIXED_USE_SPLITS)

    def by_weight(weights: object, pick: Any) -> str | None:
        if not isinstance(weights, dict):
            return None
        return str(pick(weights.items(), key=lambda kv: kv[1])[0])

    dominant = splits.map(lambda w: by_weight(w, max))
    secondary = splits.map(lambda w: by_weight(w, min))
    obm_weight = splits.map(
        lambda w: max(w.values()) if isinstance(w, dict) else np.nan
    )

    floor_share = residential_share(c["floors"].to_numpy())
    floor_split = mix & c["occupancy"].isin(pcc.OBM_MOSTLY_RESIDENTIAL_MIX)
    weight_1 = np.where(floor_split, floor_share, obm_weight)
    # OBM took a MIX code's dominant use from the zone when the footprint sits in a
    # zone of that use and its own tag does not name it.
    tag_supports = c["class"].map(class_parent) == dominant
    zone_dominant = c["zone_match"].astype(bool) & ~tag_supports
    source_1 = np.where(zone_dominant, "inherited", "tagged")

    override = c["rule"] == "residential_tag_override"
    return pd.DataFrame(
        {
            "parent_1": np.where(override, "residential_mu", dominant),
            "weight_1": np.where(override, floor_share, weight_1),
            "source_1": np.where(override, "tagged", source_1),
            "parent_2": np.where(override, c["parent"], secondary),
            "weight_2": np.where(
                override, 1 - floor_share, 1 - weight_1.astype(np.float64)
            ),
            "source_2": "tagged",
        },
        index=c.index,
    )


def effective_tile(
    rcov_data: RawCovariateData,
    cov_data: CovariateData,
    quadkey: str,
    overriding: set[str],
    class_parent: pd.Series,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply the rules to one tile. Returns the touched footprints and diagnostics."""
    lookup = load_lookup(
        rcov_data, "buildings", quadkey, ["osm_id", "class", "is_part"]
    )
    lookup = lookup[lookup["class"].notna() & ~lookup["is_part"].astype(bool)]
    own_class = lookup.drop_duplicates("osm_id").set_index("osm_id")["class"]
    attributes = tile_attributes(rcov_data, quadkey)
    named = attributes.index[attributes["gq_name"].notna()].to_numpy()

    sites = site_fids(rcov_data, quadkey)
    contained, nearby, place_stats = place_fids(
        rcov_data, quadkey, OBM_GROUP_QUARTERS_POI_RADIUS_M
    )
    spatial = np.unique(np.concatenate([sites, contained, nearby]))

    # Only footprints a rule can touch: those with an own class or a name match, the
    # mixed codes, and the spatial candidates. The full tile is never materialized.
    filters: list[Any] = [
        [("has_own_class", "==", True)],
        [("occupancy", "in", list(pcc.OBM_MIXED_USE_SPLITS))],
    ]
    if len(spatial):
        filters.append([("fid", "in", spatial.tolist())])
    if len(named):
        filters.append([("source_id", "==", 0), ("id", "in", named.tolist())])
    c = pq.read_table(
        cov_data.open_building_map_classified_path(quadkey),
        columns=["fid", "id", "source_id", "occupancy", "parent", "zone_match"],
        filters=filters,
    ).to_pandas()
    is_osm = c["source_id"] == 0
    c["class"] = c["id"].map(own_class).where(is_osm)
    for column in ["floors", "floors_source", "gq_name"]:
        c[column] = c["id"].map(attributes[column]).where(is_osm)
    residential_tag = c["class"].map(class_parent) == "residential_mu"
    gq_compatible = (
        c["class"].isna()
        | residential_tag
        | c["class"].isin(pcc.OBM_GROUP_QUARTERS_COMPATIBLE_CLASSES)
    )

    rule = pd.Series(None, index=c.index, dtype=object)

    def assign(mask: pd.Series, name: str) -> None:
        rule[mask & rule.isna()] = name

    assign(c["class"].isin(pcc.OBM_GROUP_QUARTERS_CLASSES), "group_quarters_class")
    assign(c["fid"].isin(sites) & gq_compatible, "group_quarters_site")
    assign(c["fid"].isin(contained) & gq_compatible, "group_quarters_place")
    assign(c["gq_name"].notna() & gq_compatible, "group_quarters_name")
    assign(c["fid"].isin(nearby) & gq_compatible, "group_quarters_place_nearby")
    overrides = (overriding - set(pcc.OBM_WHOLE_SITE_CODES)) - set(
        pcc.OBM_OVERRIDES_KEPT
    )
    assign(residential_tag & c["occupancy"].isin(overrides), "residential_tag_override")
    assign(c["occupancy"].isin(pcc.OBM_MIXED_USE_SPLITS), "mixed_use")
    c = c[rule.notna()].assign(rule=rule[rule.notna()])

    whole = c["rule"].isin(WHOLE_RULES)
    shares = mixed_shares(c, class_parent)
    out = pd.DataFrame(
        {
            "fid": c["fid"].astype(np.int64),
            "rule": c["rule"],
            "occupancy": c["occupancy"],
            "floors": c["floors"].astype(np.float32),
            "floors_source": c["floors_source"].where(~whole),
            "parent_1": np.where(whole, "residential_mu", shares["parent_1"]),
            "weight_1": np.where(whole, 1.0, shares["weight_1"]).astype(np.float32),
            "source_1": np.where(whole, "tagged", shares["source_1"]),
            "parent_2": shares["parent_2"].where(~whole),
            "weight_2": np.where(whole, 0.0, shares["weight_2"]).astype(np.float32),
            "source_2": shares["source_2"].where(~whole),
        }
    )
    mixed = out[~whole]
    stats: dict[str, Any] = {
        "quadkey": quadkey,
        **{f"n_{k}": int(v) for k, v in out["rule"].value_counts().items()},
        **{f"places_{k}": v for k, v in place_stats.items()},
        "n_site_candidates": len(sites),
        "n_name_matches": len(named),
        "n_mixed_dominant_inherited": int((mixed["source_1"] == "inherited").sum()),
        "n_mixed_with_floors": int(mixed["floors"].notna().sum()),
    }
    return out, stats


def open_building_map_effective_occupancy_main(
    quadkey: str, raw_covariate_dir: str, output_dir: str, *, overwrite: bool = False
) -> None:
    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)
    out_path = cov_data.open_building_map_effective_path(quadkey)
    summary_path = cov_data.open_building_map_effective / "summary" / out_path.name
    if out_path.exists() and summary_path.exists() and not overwrite:
        click.echo(f"{out_path} already exists; skipping.")
        return

    touched, stats = effective_tile(
        rcov_data,
        cov_data,
        quadkey,
        rcov_data.load_obm_overriding_occupancies(),
        tag_parents(rcov_data),
    )
    # Write via temporary files so an interrupted task never leaves a partial table.
    mkdir(out_path.parent, exist_ok=True, parents=True)
    tmp = out_path.with_suffix(".parquet.tmp")
    pq.write_table(
        pa.Table.from_pandas(touched, schema=EFFECTIVE_SCHEMA, preserve_index=False),
        tmp,
    )
    touch(out_path, clobber=True)
    tmp.replace(out_path)
    mkdir(summary_path.parent, exist_ok=True, parents=True)
    tmp = summary_path.with_suffix(".parquet.tmp")
    pd.DataFrame([stats]).to_parquet(tmp, index=False)
    touch(summary_path, clobber=True)
    tmp.replace(summary_path)
    click.echo(f"{quadkey}: {stats}")


#########
# Tasks #
#########


@click.command()
@clio.with_choice(
    "effective_lookup",
    allow_all=False,
    choices=list(LOOKUP_SOURCES),
    help="Overture lookup to extract.",
)
@click.option("--overture-file", type=click.STRING, required=True)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_overwrite()
def open_building_map_effective_lookup_task(
    effective_lookup: str,
    overture_file: str,
    raw_covariate_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    """Extract one Overture file's lookup for the effective occupancy."""
    effective_lookup_main(
        effective_lookup, overture_file, raw_covariate_dir, overwrite=overwrite
    )


@click.command()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_overwrite()
def open_building_map_osm_sites_task(
    raw_covariate_dir: str, *, overwrite: bool = False
) -> None:
    """Extract the group-quarters site polygons from the OSM planet file."""
    osm_group_quarters_sites_main(raw_covariate_dir, overwrite=overwrite)


@click.command()
@clio.with_obm_quadkey()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_overwrite()
def open_building_map_effective_occupancy_task(
    obm_quadkey: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    """Apply the effective-occupancy rules to one Open Building Map tile."""
    open_building_map_effective_occupancy_main(
        obm_quadkey, raw_covariate_dir, output_dir, overwrite=overwrite
    )


@click.command()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_overwrite()
@clio.with_queue()
def open_building_map_effective_occupancy(
    raw_covariate_dir: str,
    output_dir: str,
    queue: str,
    *,
    overwrite: bool = False,
) -> None:
    """Build the effective-occupancy corrections for every OBM tile.

    Needs the label-source extract to have run. Scans the OSM planet file for
    group-quarters sites and builds the Overture lookups, then runs one task per
    tile. Completed work is skipped unless --overwrite.
    """
    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)
    flag: dict[str, Any] = {"overwrite": None} if overwrite else {}
    quadkeys = sorted(list_local_quadkeys(rcov_data))
    missing = [
        q
        for q in quadkeys
        if not cov_data.open_building_map_classified_path(q).exists()
    ]
    if missing:
        msg = (
            f"{len(missing)} tiles have no label-source classification; run that first."
        )
        raise FileNotFoundError(msg)

    # Stage 1: the OSM site scan and the Overture lookups run side by side.
    sites_marker = rcov_data.obm_overture_lookup_marker_path(
        OSM_SITES_LOOKUP, OSM_SITES_SOURCE
    )
    if overwrite or not sites_marker.exists():
        _run(
            "open_building_map_osm_sites",
            ("raw-covariate-dir",),
            [(raw_covariate_dir,)],
            flag,
            {"queue": queue, "memory": "40G", "runtime": "8h", "cores": 8},
            rcov_data.log_dir("extract_open_building_map_osm_sites"),
        )
    lookup_jobs = [
        (lookup, path.name)
        for lookup, (theme, theme_type) in LOOKUP_SOURCES.items()
        for path in sorted(rcov_data.list_overture_paths(theme, theme_type))
        if overwrite
        or not rcov_data.obm_overture_lookup_marker_path(lookup, path.stem).exists()
    ]
    _run(
        "open_building_map_effective_lookup",
        ("effective-lookup", "overture-file"),
        lookup_jobs,
        {"raw-covariate-dir": raw_covariate_dir, **flag},
        {"queue": queue, "memory": "15G", "runtime": "2h"},
        rcov_data.log_dir("extract_open_building_map_effective_lookup"),
    )

    # Stage 2: the rules, one task per tile.
    jobs = [
        (q,)
        for q in quadkeys
        if overwrite or not cov_data.open_building_map_effective_path(q).exists()
    ]
    _run(
        "open_building_map_effective_occupancy",
        ("obm-quadkey",),
        jobs,
        {"raw-covariate-dir": raw_covariate_dir, "output-dir": output_dir, **flag},
        {"queue": queue, "memory": "30G", "runtime": "4h"},
        cov_data.log_dir("extract_open_building_map_effective_occupancy"),
    )

    summary = pd.concat(
        [
            pd.read_parquet(p)
            for p in sorted(
                (cov_data.open_building_map_effective / "summary").glob("*.parquet")
            )
        ],
        ignore_index=True,
    ).fillna(0)
    path = cov_data.open_building_map_effective / "summary.parquet"
    touch(path, clobber=True)
    summary.to_parquet(path, index=False)
    click.echo(summary.drop(columns="quadkey").sum().to_string())
