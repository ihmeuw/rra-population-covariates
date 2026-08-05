import math
from typing import Any, cast

import click
import geopandas as gpd  # type: ignore[import-untyped]
import numpy as np
import rasterra as rt
import shapely  # type: ignore[import-untyped]
import tqdm  # type: ignore[import-untyped]
from affine import Affine  # type: ignore[import-untyped]
from rasterio.features import rasterize  # type: ignore[import-untyped]
from rra_tools import jobmon

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


def read_tile_buildings(
    rcov_data: RawCovariateData,
    quadkeys: list[str],
    bounds: tuple[float, float, float, float],
    target_crs: str,
) -> gpd.GeoDataFrame | None:
    """Read the buildings intersecting a bounding box, tagged by parent type.

    The bounding box is pushed into each GeoPackage's spatial index, so only the
    relevant footprints are materialized. Footprints straddling the edge are
    returned whole and clipped later by the rasterization.
    """
    frames = []
    for quadkey in quadkeys:
        gdf = gpd.read_file(
            rcov_data.open_building_map_path(quadkey),
            layer="building",
            columns=["occupancy"],
            bbox=bounds,
        )
        if not gdf.empty:
            frames.append(gdf)

    if not frames:
        return None

    buildings = gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True), crs=frames[0].crs
    )
    unmapped = sorted(
        set(buildings["occupancy"].unique()) - set(pcc.OBM_OCCUPANCY_WEIGHTS)
    )
    if unmapped:
        msg = (
            f"Occupancy codes with no parent building type: {unmapped}. Add them to "
            "OBM_PARENT_BUILDING_TYPES or OBM_MIXED_USE_SPLITS in constants.py."
        )
        raise ValueError(msg)
    buildings["parent_building_type"] = buildings["occupancy"].map(
        pcc.OBM_OCCUPANCY_PARENTS
    )
    return buildings.to_crs(target_crs)


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
        out_shape=(out_shape[0] * factor, out_shape[1] * factor),
        transform=fine_transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    averaged = (
        fine.reshape(out_shape[0], factor, out_shape[1], factor)
        .mean(axis=(1, 3))
        .astype(np.float32)
    )
    return cast("np.ndarray[Any, Any]", averaged)


def open_building_map_main(
    resolution: str,
    block_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
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

    # One accumulator per parent type, covering the whole block.
    coverage = {
        parent: np.zeros(block_shape, dtype=np.float32)
        for parent in pcc.OBM_PARENT_BUILDING_TYPES
    }

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
            rcov_data, quadkeys, bounds, model_frame.crs.to_string()
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
        # so overlapping footprints of the same type are unioned rather than summed.
        # The mixed-use codes contribute a fraction to each of two parents, so each is
        # rasterized on its own and scaled by its weight.
        is_mixed = buildings["occupancy"].isin(pcc.OBM_MIXED_USE_SPLITS)
        whole, mixed = buildings[~is_mixed], buildings[is_mixed]

        for parent, group in whole.groupby("parent_building_type"):
            coverage[parent][rows, cols] += coverage_fraction(
                group.geometry, tile_shape, tile_transform
            )

        for code, group in mixed.groupby("occupancy"):
            fraction = coverage_fraction(group.geometry, tile_shape, tile_transform)
            for parent, weight in pcc.OBM_MIXED_USE_SPLITS[code].items():
                coverage[parent][rows, cols] += weight * fraction

    # Land mask: nan outside the modeled area, matching every other feature.
    land = ~np.isnan(block_template.to_numpy())
    for parent, array in coverage.items():
        raster = rt.RasterArray(
            np.where(land, array, np.nan).astype(np.float32),
            transform=block_transform,
            crs=block_template.crs,
            no_data_value=np.nan,
        )
        cov_data.save_open_building_map_raster(raster, resolution, block_key, parent)


@click.command()
@clio.with_obm_resolution()
@clio.with_obm_block_key()
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_progress_bar()
def open_building_map_task(
    obm_resolution: str,
    obm_block_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    progress_bar: bool = False,
) -> None:
    """Rasterize Open Building Map footprints for one block."""
    open_building_map_main(
        obm_resolution,
        obm_block_key,
        raw_covariate_dir,
        output_dir,
        progress_bar=progress_bar,
    )


@click.command()
@clio.with_obm_resolution(allow_all=True)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_queue()
def open_building_map(
    obm_resolution: list[str],
    raw_covariate_dir: str,
    output_dir: str,
    queue: str,
) -> None:
    """Rasterize Open Building Map footprints by block and parent building type."""
    from rra_population_model.data import PopulationModelData

    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)
    pm_data = PopulationModelData()

    local_quadkeys = list_local_quadkeys(rcov_data)
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
        },
        task_resources={
            "queue": queue,
            "memory": "30G",
            "runtime": "4h",
            "project": "proj_rapidresponse",
        },
        max_attempts=3,
        log_root=cov_data.log_dir("process_open_building_map"),
    )
