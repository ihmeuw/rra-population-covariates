import math
from typing import Any, cast

import click
import geopandas as gpd  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import rasterra as rt
import shapely  # type: ignore[import-untyped]
import tqdm  # type: ignore[import-untyped]
from affine import Affine  # type: ignore[import-untyped]
from rasterio.features import rasterize  # type: ignore[import-untyped]
from rra_tools import jobmon
from rra_tools.shell_tools import mkdir, touch

from rra_population_covariates import cli_options as clio
from rra_population_covariates import constants as pcc
from rra_population_covariates.data import CovariateData, RawCovariateData

# rra_population_model pulls in torch and friends, and every task in this package
# imports this module through the CLI. Import it where it is used so that stages
# which don't need the population model still run without it installed.


def quadkey_bounds(quadkey: str) -> tuple[float, float, float, float]:
    """Get the (west, south, east, north) bounds of a quadkey tile in degrees.

    Open Building Map tiles the world with the standard Web Mercator quad tree, so
    a tile's extent follows from its quadkey with no lookup table.
    """
    x = y = 0
    zoom = len(quadkey)
    for i, digit in enumerate(quadkey):
        mask = 1 << (zoom - i - 1)
        value = int(digit)
        if value & 1:
            x |= mask
        if value & 2:
            y |= mask
    n = 2**zoom

    def lon(tile_x: int) -> float:
        return float(tile_x / n * 360.0 - 180.0)

    def lat(tile_y: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * tile_y / n))))

    return lon(x), lat(y + 1), lon(x + 1), lat(y)


def list_local_quadkeys(rcov_data: RawCovariateData) -> dict[str, shapely.Polygon]:
    """Map the quadkey of every downloaded tile to its extent in degrees."""
    paths = rcov_data.list_open_building_map_paths()
    if not paths:
        msg = (
            f"No Open Building Map tiles found in {rcov_data.open_building_map}. "
            "Run 'pcrun extract open_building_map' first."
        )
        raise FileNotFoundError(msg)
    quadkeys = [path.name.split(".")[1] for path in paths]
    return {quadkey: shapely.box(*quadkey_bounds(quadkey)) for quadkey in quadkeys}


def check_occupancy_codes(occupancy: "gpd.pd.Series") -> None:
    """Fail if any occupancy code has no parent building type."""
    unmapped = sorted(set(occupancy.unique()) - set(pcc.OBM_OCCUPANCY_WEIGHTS))
    if unmapped:
        msg = (
            f"Occupancy codes with no parent building type: {unmapped}. Add them to "
            "OBM_PARENT_BUILDING_TYPES or OBM_MIXED_USE_SPLITS in constants.py."
        )
        raise ValueError(msg)


class LabelSourceLookup:
    """The label source of every footprint, by tile and GeoPackage fid.

    A block reads each of its tiles many times, once per modeling-frame tile, so each
    tile's classification is loaded once and held as an array indexed by fid.
    """

    def __init__(self, cov_data: CovariateData) -> None:
        self._cov_data = cov_data
        self._tiles: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def _load(self, quadkey: str) -> tuple[np.ndarray, np.ndarray]:
        path = self._cov_data.open_building_map_classified_path(quadkey)
        if not path.exists():
            msg = (
                f"{path} not found. Run 'pcrun extract open_building_map_label_source' "
                "first."
            )
            raise FileNotFoundError(msg)
        table = pq.read_table(path, columns=["fid", "label_source"])
        fid = table.column("fid").to_numpy()
        label_source = table.column("label_source").combine_chunks()
        codes = np.full(fid.max() + 1, -1, dtype=np.int8)
        codes[fid] = label_source.indices.to_numpy()
        return codes, np.asarray(label_source.dictionary.to_pylist(), dtype=object)

    def get(self, quadkey: str, fid: np.ndarray) -> np.ndarray:
        if quadkey not in self._tiles:
            self._tiles[quadkey] = self._load(quadkey)
        codes, labels = self._tiles[quadkey]
        if fid.max(initial=0) >= len(codes) or (codes[fid] < 0).any():
            msg = (
                f"Tile {quadkey} has footprints missing from its classification; "
                "it is stale. Rerun the label source extract for this tile."
            )
            raise ValueError(msg)
        return cast("np.ndarray[Any, Any]", labels[codes[fid]])


EFFECTIVE_COLUMNS = [
    "parent_1",
    "weight_1",
    "source_1",
    "parent_2",
    "weight_2",
    "source_2",
]


class EffectiveOccupancyLookup:
    """The effective-occupancy corrections of every tile, by GeoPackage fid.

    The tables are sparse, holding only the footprints a rule touched, so each is
    small enough to keep for the whole block.
    """

    def __init__(self, cov_data: CovariateData) -> None:
        self._cov_data = cov_data
        self._tiles: dict[str, pd.DataFrame] = {}

    def get(self, quadkey: str, fid: np.ndarray) -> pd.DataFrame:
        if quadkey not in self._tiles:
            path = self._cov_data.open_building_map_effective_path(quadkey)
            if not path.exists():
                msg = (
                    f"{path} not found. Run 'pcrun extract "
                    "open_building_map_effective_occupancy' first."
                )
                raise FileNotFoundError(msg)
            self._tiles[quadkey] = pd.read_parquet(
                path, columns=["fid", *EFFECTIVE_COLUMNS]
            ).set_index("fid")
        return self._tiles[quadkey].reindex(fid)


def read_tile_buildings(  # noqa: PLR0913
    rcov_data: RawCovariateData,
    quadkeys: list[str],
    bounds: tuple[float, float, float, float],
    target_crs: str,
    label_sources: LabelSourceLookup | None = None,
    effective: EffectiveOccupancyLookup | None = None,
) -> gpd.GeoDataFrame | None:
    """Read the buildings intersecting a bounding box, tagged by parent type.

    The bounding box is pushed into each GeoPackage's spatial index, so only the
    relevant footprints are materialized. Footprints straddling the edge are
    returned whole and clipped later by the rasterization. Given a lookup, each
    footprint also gets its label source, joined on the GeoPackage fid.
    """
    frames = []
    for quadkey in quadkeys:
        gdf = gpd.read_file(
            rcov_data.open_building_map_path(quadkey),
            layer="building",
            columns=["occupancy"],
            bbox=bounds,
            fid_as_index=True,
        )
        if not gdf.empty:
            fid = gdf.index.to_numpy(dtype=np.int64)
            if label_sources is not None:
                gdf["label_source"] = label_sources.get(quadkey, fid)
            if effective is not None:
                gdf[EFFECTIVE_COLUMNS] = effective.get(quadkey, fid).to_numpy()
            frames.append(gdf)

    if not frames:
        return None

    buildings = gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True), crs=frames[0].crs
    )
    check_occupancy_codes(buildings["occupancy"])
    buildings["parent_building_type"] = buildings["occupancy"].map(
        pcc.OBM_OCCUPANCY_PARENTS
    )
    return buildings.to_crs(target_crs)


def fine_mask(
    geometries: gpd.GeoSeries,
    out_shape: tuple[int, int],
    transform: Affine,
    factor: int = pcc.OBM_SUPERSAMPLE_FACTOR,
) -> np.ndarray:
    """Rasterize the geometries onto a grid `factor` times finer, as a 0/1 mask.

    Overlapping geometries are unioned: a subpixel is 1 however many cover it.
    """
    fine_shape = (out_shape[0] * factor, out_shape[1] * factor)
    if geometries.empty:
        # rasterize rejects an empty shape list; one half of a split can be empty.
        return np.zeros(fine_shape, dtype=np.uint8)
    fine_transform = Affine(
        transform.a / factor,
        transform.b,
        transform.c,
        transform.d,
        transform.e / factor,
        transform.f,
    )
    fine = rasterize(
        [(geom, 1) for geom in geometries],
        out_shape=fine_shape,
        transform=fine_transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    return cast("np.ndarray[Any, Any]", fine)


def average_down(
    fine: np.ndarray,
    out_shape: tuple[int, int],
    factor: int = pcc.OBM_SUPERSAMPLE_FACTOR,
) -> np.ndarray:
    """Average each `factor` x `factor` block of a fine mask down to one pixel."""
    averaged = (
        fine.reshape(out_shape[0], factor, out_shape[1], factor)
        .mean(axis=(1, 3))
        .astype(np.float32)
    )
    return cast("np.ndarray[Any, Any]", averaged)


def coverage_fraction(
    geometries: gpd.GeoSeries,
    out_shape: tuple[int, int],
    transform: Affine,
    factor: int = pcc.OBM_SUPERSAMPLE_FACTOR,
) -> np.ndarray:
    """Get the fraction of each pixel covered by the geometries.

    Footprints are much smaller than a pixel, so we rasterize onto a grid `factor`
    times finer and average each block of subpixels back down. This preserves
    footprint area, which a binary rasterization at the target resolution cannot.
    """
    return average_down(
        fine_mask(geometries, out_shape, transform, factor), out_shape, factor
    )


def weighted_coverage_fraction(
    geometries: gpd.GeoSeries,
    weights: np.ndarray,
    out_shape: tuple[int, int],
    transform: Affine,
    factor: int = pcc.OBM_SUPERSAMPLE_FACTOR,
) -> np.ndarray:
    """Get each pixel's coverage by geometries that each count for a fraction.

    Each footprint is burnt into the fine grid at its weight in percent, so where
    weighted footprints overlap the last one wins, as a union would for whole ones.
    """
    fine_transform = Affine(
        transform.a / factor,
        transform.b,
        transform.c,
        transform.d,
        transform.e / factor,
        transform.f,
    )
    percent = np.rint(np.asarray(weights, dtype=np.float64) * 100).astype(np.uint8)
    shapes = [(g, int(w)) for g, w in zip(geometries, percent, strict=True) if w > 0]
    if not shapes:
        return np.zeros(out_shape, dtype=np.float32)
    fine = rasterize(
        shapes,
        out_shape=(out_shape[0] * factor, out_shape[1] * factor),
        transform=fine_transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    return cast("np.ndarray[Any, Any]", average_down(fine, out_shape, factor) / 100)


def split_coverage_fraction(
    tagged: gpd.GeoSeries,
    inherited: gpd.GeoSeries,
    out_shape: tuple[int, int],
    transform: Affine,
) -> tuple[np.ndarray, np.ndarray]:
    """Get the tagged and inherited coverage of one parent, with tagged winning.

    Both halves are rasterized on the fine grid before either is averaged, and
    inherited is masked by tagged there. Ground under both is therefore counted once,
    as tagged: a footprint's own label is stronger evidence than its zone's, and it
    is the building:part case, where an untagged part sits inside a tagged outline.
    The two halves then sum exactly to the unsplit coverage.
    """
    fine_tagged = fine_mask(tagged, out_shape, transform)
    fine_inherited = fine_mask(inherited, out_shape, transform)
    fine_inherited[fine_tagged.astype(bool)] = 0
    return average_down(fine_tagged, out_shape), average_down(fine_inherited, out_shape)


def building_height(
    bd_data: Any,
    resolution: str,
    block_key: str,
    covered: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Get GHSL building height for a block, with one-storey imputation.

    `covered` marks the pixels that hold OBM footprints. A single storey is substituted
    only there, since that is the only place the height is ever used - everywhere else
    it multiplies a zero coverage. Restricting the substitution keeps the returned array
    honest: 2.5 m appears only where it actually contributes to a written value, rather
    than across the ~78% of land that GHSL considers unbuilt.

    Returns the height in meters and the mask of substituted pixels, which the caller
    reports. See OBM_ONE_STOREY_HEIGHT_M in constants.py for why GHSL height is used at
    all and why 2.5 m is the fallback.
    """
    height = bd_data.load_tile(
        resolution=resolution,
        provider=pcc.OBM_HEIGHT_PROVIDER,
        measure=pcc.OBM_HEIGHT_MEASURE,
        time_point=pcc.OBM_HEIGHT_TIME_POINT,
        block_key=block_key,
    ).to_numpy()
    # GHSL reports 0, not nan, on unbuilt land, and its nodata mask does not always match
    # the template's land mask. Fill first so that no nan can survive into the volume
    # rasters, whose mask must match the density rasters exactly.
    height = np.nan_to_num(height)
    imputed = covered & (height == 0)
    height = np.where(imputed, pcc.OBM_ONE_STOREY_HEIGHT_M, height).astype(np.float32)
    return cast("np.ndarray[Any, Any]", height), cast("np.ndarray[Any, Any]", imputed)


def parent_layers(parent: str, *, by_label_source: bool) -> list[str]:
    """Get the layers a parent building type is written as."""
    if not by_label_source or parent == "unknown":
        return [parent]
    return [f"{parent}_{source}" for source in pcc.OBM_LABEL_SOURCES]


def compare_with_reference(  # noqa: PLR0913
    cov_data: CovariateData,
    resolution: str,
    block_key: str,
    parent: str,
    layers: dict[str, np.ndarray],
    land: np.ndarray,
) -> dict[str, Any]:
    """Compare a parent's split layers, summed, with the original unsplit raster.

    Tagged masks inherited on the fine grid, so the two halves sum to the unsplit
    coverage and should match to float32 rounding.
    """
    density = sum(layers.values())
    row: dict[str, Any] = {
        "block_key": block_key,
        "parent": parent,
        **{
            f"density_{name.removeprefix(parent).strip('_') or 'all'}": float(
                np.nansum(np.where(land, array, 0.0))
            )
            for name, array in layers.items()
        },
    }
    path = cov_data.open_building_map_reference_raster_path(
        resolution, block_key, parent, "density"
    )
    row["reference_found"] = path.exists()
    if not path.exists():
        return row

    reference = np.nan_to_num(rt.load_raster(path).to_numpy())
    new = np.where(land, density, 0.0)
    reference = np.where(land, reference, 0.0)
    diff = new - reference
    built = (new > 0) | (reference > 0)
    differs = built & (np.abs(diff) > pcc.OBM_CHECK_TOLERANCE)
    reference_total = float(reference.sum())
    row.update(
        {
            "max_abs_diff": float(np.abs(diff).max(initial=0.0)),
            "n_built": int(built.sum()),
            "n_differ": int(differs.sum()),
            "reference_density": reference_total,
            "density_diff": float(diff.sum()),
            "density_diff_share": float(diff.sum() / reference_total)
            if reference_total
            else 0.0,
        }
    )
    return row


def compare_with_split(
    cov_data: CovariateData,
    resolution: str,
    block_key: str,
    layers: dict[str, np.ndarray],
    land: np.ndarray,
) -> list[dict[str, Any]]:
    """Compare each effective-occupancy layer with the same layer of the split run.

    The corrections move density between parents and label sources, so per-layer
    totals change by design; summed over all layers they should barely move.
    """
    rows = []
    for layer, array in layers.items():
        path = cov_data.open_building_map_split_reference_raster_path(
            resolution, block_key, layer, "density"
        )
        current = (
            float(np.nansum(np.where(land, rt.load_raster(path).to_numpy(), 0.0)))
            if path.exists()
            else np.nan
        )
        rows.append(
            {
                "block_key": block_key,
                "layer": layer,
                "density_effective": float(np.where(land, array, 0.0).sum()),
                "density_split": current,
            }
        )
    return rows


def open_building_map_main(  # noqa: C901, PLR0912, PLR0913, PLR0915
    resolution: str,
    block_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    by_label_source: bool = False,
    effective_occupancy: bool = False,
    progress_bar: bool = False,
) -> None:
    from rra_population_model.data import (
        BuildingDensityData,
        PopulationModelData,
    )

    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)
    pm_data = PopulationModelData()
    bd_data = BuildingDensityData()

    # The building density tile gives us the exact block grid and the land mask
    # every other model feature is built on.
    block_template = bd_data.load_tile(
        resolution=resolution,
        provider=pcc.OBM_TEMPLATE_PROVIDER,
        measure=pcc.OBM_TEMPLATE_MEASURE,
        time_point=pcc.OBM_TEMPLATE_TIME_POINT,
        block_key=block_key,
    )
    block_shape = block_template.shape
    block_transform = block_template.transform
    pixel_size = abs(block_transform.a)

    model_frame = pm_data.load_modeling_frame(resolution)
    block_frame = model_frame[model_frame["block_key"] == block_key]
    tile_frame = block_frame.to_crs("EPSG:4326")

    local_quadkeys = list_local_quadkeys(rcov_data)

    # One accumulator per layer, covering the whole block: a layer per parent type,
    # or split by label source a tagged and an inherited layer per labelled parent.
    # The effective occupancy is applied on top of the label-source split.
    by_label_source = by_label_source or effective_occupancy
    label_sources = LabelSourceLookup(cov_data) if by_label_source else None
    effective = EffectiveOccupancyLookup(cov_data) if effective_occupancy else None
    coverage = {
        layer: np.zeros(block_shape, dtype=np.float32)
        for parent in pcc.OBM_PARENT_BUILDING_TYPES
        for layer in parent_layers(parent, by_label_source=by_label_source)
    }
    # Mixed-use codes never come from a single zone, so they are always tagged.
    mixed_suffix = "_tagged" if by_label_source else ""

    # Work tile by tile. A block spans hundreds of kilometers and could hold tens of
    # millions of footprints, which is more geometry than we want in memory at once.
    tiles = list(zip(block_frame.geometry, tile_frame.geometry, strict=True))
    for tile_geom, tile_geom_degrees in tqdm.tqdm(tiles, disable=not progress_bar):
        bounds = tile_geom_degrees.bounds
        quadkeys = [
            quadkey
            for quadkey, extent in local_quadkeys.items()
            if extent.intersects(tile_geom_degrees)
        ]
        if not quadkeys:
            continue

        buildings = read_tile_buildings(
            rcov_data,
            quadkeys,
            bounds,
            model_frame.crs.to_string(),
            label_sources,
            effective,
        )
        if buildings is None:
            continue

        # Locate this tile's window within the block array.
        tile_minx, _, _, tile_maxy = tile_geom.bounds
        col_off = round((tile_minx - block_transform.c) / pixel_size)
        row_off = round((block_transform.f - tile_maxy) / pixel_size)
        tile_transform = Affine(pixel_size, 0, tile_minx, 0, -pixel_size, tile_maxy)
        n_rows = round(tile_geom.bounds[3] - tile_geom.bounds[1]) // int(pixel_size)
        n_cols = round(tile_geom.bounds[2] - tile_minx) // int(pixel_size)
        tile_shape = (n_rows, n_cols)

        rows = slice(row_off, row_off + n_rows)
        cols = slice(col_off, col_off + n_cols)

        # Codes that belong wholly to one parent are rasterized together per parent,
        # so overlapping footprints of the same parent are unioned rather than summed.
        # Split by label source, tagged and inherited are rasterized together too,
        # with tagged winning where they overlap.
        # The mixed-use codes contribute a fraction to each of two parents, so each is
        # rasterized on its own and scaled by its weight.
        weighted = None
        if effective is not None:
            # A whole correction rests on the footprint's own class, a group-quarters
            # site, place or name, never on OBM's zone, so it is tagged and replaces
            # the parent. Fractional ones carry a label source per share and are
            # rasterized by weight below.
            corrected = buildings["parent_1"].notna()
            fractional = corrected & buildings["parent_2"].notna()
            to_whole = corrected & ~fractional
            buildings.loc[to_whole, "parent_building_type"] = buildings.loc[
                to_whole, "parent_1"
            ]
            buildings.loc[to_whole, "label_source"] = "tagged"
            buildings.loc[to_whole, "occupancy"] = pcc.OBM_GROUP_QUARTERS_CODE
            weighted, buildings = buildings[fractional], buildings[~fractional]

        is_mixed = buildings["occupancy"].isin(pcc.OBM_MIXED_USE_SPLITS)
        whole, mixed = buildings[~is_mixed], buildings[is_mixed]

        for parent, group in whole.groupby("parent_building_type"):
            # unknown is a parent and a label source at once, and is not split.
            if not by_label_source or parent == "unknown":
                coverage[parent][rows, cols] += coverage_fraction(
                    group.geometry, tile_shape, tile_transform
                )
                continue
            is_tagged = group["label_source"] == "tagged"
            tagged, inherited = split_coverage_fraction(
                group.geometry[is_tagged],
                group.geometry[~is_tagged],
                tile_shape,
                tile_transform,
            )
            coverage[f"{parent}_tagged"][rows, cols] += tagged
            coverage[f"{parent}_inherited"][rows, cols] += inherited

        for code, group in mixed.groupby("occupancy"):
            fraction = coverage_fraction(group.geometry, tile_shape, tile_transform)
            for parent, weight in pcc.OBM_MIXED_USE_SPLITS[code].items():
                coverage[parent + mixed_suffix][rows, cols] += weight * fraction

        if weighted is not None and not weighted.empty:
            share_layers = {
                f"{p}_{s}"
                for k in ("1", "2")
                for p, s in zip(
                    weighted[f"parent_{k}"], weighted[f"source_{k}"], strict=True
                )
            }
            for layer in share_layers:
                weights = np.zeros(len(weighted))
                for k in ("1", "2"):
                    weights += np.where(
                        (weighted[f"parent_{k}"] + "_" + weighted[f"source_{k}"])
                        == layer,
                        weighted[f"weight_{k}"].astype(np.float64),
                        0.0,
                    )
                coverage[layer][rows, cols] += weighted_coverage_fraction(
                    weighted.geometry, weights, tile_shape, tile_transform
                )

    # Land mask: nan outside the modeled area, matching every other feature.
    land = ~np.isnan(block_template.to_numpy())

    # Volume is height * density with height constant per pixel, so it factors out of
    # the accumulation entirely and is a multiply over the finished density arrays. No
    # second pass over the footprints is needed.
    covered = land & (sum(coverage.values()) > 0)
    height, imputed = building_height(bd_data, resolution, block_key, covered)
    n_covered = int(covered.sum())
    n_imputed = int(imputed.sum())
    share = 100 * n_imputed / n_covered if n_covered else 0.0
    click.echo(
        f"{block_key}: {n_covered:,} pixels hold OBM footprints; GHSL had no height for "
        f"{n_imputed:,} of them ({share:.2f}%), imputed at "
        f"{pcc.OBM_ONE_STOREY_HEIGHT_M} m."
    )

    def save(array: np.ndarray, parent: str, measure: str) -> None:
        raster = rt.RasterArray(
            np.where(land, array, np.nan).astype(np.float32),
            transform=block_transform,
            crs=block_template.crs,
            no_data_value=np.nan,
        )
        cov_data.save_open_building_map_raster(
            raster, resolution, block_key, parent, measure
        )

    # Write and release one parent at a time; holding both measures for every layer
    # at once would double peak memory for no benefit. Split by label source, each
    # parent is checked against the original raster before it is released.
    checks = []
    for parent in pcc.OBM_PARENT_BUILDING_TYPES:
        layers = {
            layer: coverage.pop(layer)
            for layer in parent_layers(parent, by_label_source=by_label_source)
        }
        for layer, density in layers.items():
            save(density, layer, "density")
            save(height * density, layer, "volume")
        if effective_occupancy:
            checks.extend(
                compare_with_split(cov_data, resolution, block_key, layers, land)
            )
        elif by_label_source:
            checks.append(
                compare_with_reference(
                    cov_data, resolution, block_key, parent, layers, land
                )
            )
        del layers

    if by_label_source:
        report = pd.DataFrame(checks)
        path = cov_data.open_building_map_check_path(resolution, block_key)
        mkdir(path.parent, exist_ok=True, parents=True)
        touch(path, clobber=True)
        report.to_parquet(path, index=False)
        click.echo(report.to_string(index=False))


@click.command()
@clio.with_obm_resolution()
@clio.with_obm_block_key()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@click.option(
    "--by-label-source",
    is_flag=True,
    help=(
        "Split each labelled parent into tagged and inherited layers, using the "
        "label source extract, and check the split against the original rasters."
    ),
)
@click.option(
    "--effective-occupancy",
    is_flag=True,
    help=(
        "Apply the effective-occupancy corrections on top of the label-source split, "
        f"and check each block against {pcc.OBM_SPLIT_REFERENCE_DIRNAME}."
    ),
)
@clio.with_progress_bar()
def open_building_map_task(  # noqa: PLR0913
    obm_resolution: str,
    obm_block_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    by_label_source: bool = False,
    effective_occupancy: bool = False,
    progress_bar: bool = False,
) -> None:
    """Rasterize Open Building Map footprints for one block."""
    open_building_map_main(
        obm_resolution,
        obm_block_key,
        raw_covariate_dir,
        output_dir,
        by_label_source=by_label_source,
        effective_occupancy=effective_occupancy,
        progress_bar=progress_bar,
    )


@click.command()
@clio.with_obm_resolution(allow_all=True)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@click.option(
    "--by-label-source",
    is_flag=True,
    help=(
        "Split each labelled parent into tagged and inherited layers, using the "
        "label source extract, and check the split against the original rasters."
    ),
)
@click.option(
    "--effective-occupancy",
    is_flag=True,
    help=(
        "Apply the effective-occupancy corrections on top of the label-source split, "
        f"and check each block against {pcc.OBM_SPLIT_REFERENCE_DIRNAME}."
    ),
)
@clio.with_queue()
def open_building_map(  # noqa: PLR0913
    obm_resolution: list[str],
    raw_covariate_dir: str,
    output_dir: str,
    queue: str,
    *,
    by_label_source: bool = False,
    effective_occupancy: bool = False,
) -> None:
    """Rasterize Open Building Map footprints by block and parent building type."""
    from rra_population_model.data import PopulationModelData

    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)
    pm_data = PopulationModelData()

    local_quadkeys = list_local_quadkeys(rcov_data)
    by_label_source = by_label_source or effective_occupancy
    if by_label_source:
        # A block reads every tile it overlaps, so a partial extract would leave
        # some blocks failing and others written. Require all of it up front.
        unclassified = [
            quadkey
            for quadkey in local_quadkeys
            if not cov_data.open_building_map_classified_path(quadkey).exists()
        ]
        if unclassified:
            msg = (
                f"{len(unclassified)} of {len(local_quadkeys)} tiles have no label "
                f"source classification (e.g. {unclassified[:5]}). Run 'pcrun "
                "extract open_building_map_label_source' to completion first."
            )
            raise FileNotFoundError(msg)
    if effective_occupancy:
        uncorrected = [
            quadkey
            for quadkey in local_quadkeys
            if not cov_data.open_building_map_effective_path(quadkey).exists()
        ]
        if uncorrected:
            msg = (
                f"{len(uncorrected)} of {len(local_quadkeys)} tiles have no effective "
                "occupancy table. Run 'pcrun extract "
                "open_building_map_effective_occupancy' to completion first."
            )
            raise FileNotFoundError(msg)
    obm_extent = gpd.GeoDataFrame(
        {"quadkey": list(local_quadkeys)},
        geometry=list(local_quadkeys.values()),
        crs="EPSG:4326",
    )

    # Only blocks that overlap a downloaded tile have anything to rasterize.
    jobs: list[tuple[str, str]] = []
    for resolution in obm_resolution:
        model_frame = pm_data.load_modeling_frame(resolution)
        overlapping = model_frame.sjoin(
            obm_extent.to_crs(model_frame.crs), how="inner", predicate="intersects"
        )
        block_keys = sorted(overlapping["block_key"].unique())
        click.echo(f"{resolution}m: {len(block_keys)} blocks overlap downloaded tiles.")
        jobs.extend((resolution, block_key) for block_key in block_keys)

    jobmon.run_parallel(
        runner="pctask process",
        task_name="open_building_map",
        flat_node_args=(("obm-resolution", "obm-block-key"), jobs),
        task_args={
            "raw-covariate-dir": raw_covariate_dir,
            "output-dir": output_dir,
            # A value of None renders as a bare command line flag.
            **({"by-label-source": None} if by_label_source else {}),
            **({"effective-occupancy": None} if effective_occupancy else {}),
        },
        task_resources={
            "queue": queue,
            # 15 accumulators instead of 8, plus the per-tile label source arrays.
            "memory": "50G" if by_label_source else "30G",
            # The slowest unsplit 40m block took 2h47m; splitting adds up to ~1.8x.
            "runtime": "8h" if by_label_source else "4h",
            "project": "proj_rapidresponse",
        },
        max_attempts=3,
        log_root=cov_data.log_dir("process_open_building_map"),
    )
