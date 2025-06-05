import click
import geopandas as gpd  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
import tqdm  # type: ignore[import-untyped]
from rra_tools import jobmon
from shapely.geometry import box  # type: ignore[import-untyped]

from rra_population_covariates import cli_options as clio
from rra_population_covariates import constants as pcc
from rra_population_covariates.data import CovariateData, RawCovariateData

COVARIATE = "water"
THEME = "base"
THEME_TYPE = "water"
CLASS_MAP = pcc.OVERTURE_CLASS_MAPS[COVARIATE]


def remove_overlapping_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Remove points that overlap with non-point geometries in the GeoDataFrame.
    """
    point_gdf = gdf[gdf.geometry.geom_type == "Point"].copy()
    non_point_gdf = gdf[gdf.geometry.geom_type != "Point"].copy()

    if point_gdf.empty or non_point_gdf.empty:
        return gdf

    if point_gdf.crs != non_point_gdf.crs:
        non_point_gdf = non_point_gdf.to_crs(point_gdf.crs)

    overlaps = gpd.sjoin(
        point_gdf, non_point_gdf[["geometry"]], how="left", predicate="intersects"
    )

    non_overlapping_points = overlaps[overlaps.index_right.isna()].drop(
        columns=["index_right"]
    )

    return pd.concat([non_point_gdf, non_overlapping_points], ignore_index=True)


def filter_points_near_geometries(
    gdf: gpd.GeoDataFrame, distance_m: int = 500
) -> gpd.GeoDataFrame:
    """
    Filter out isolated point geometries that are not within a certain distance of any non-point geometry.
    """
    points_gdf = gdf[gdf.geometry.geom_type == "Point"].copy()
    others_gdf = gdf[gdf.geometry.geom_type != "Point"].copy()

    # If no points, return original gdf
    if points_gdf.empty:
        return gdf

    # Project to equal-area CRS
    projected_crs = "EPSG:6933"
    points_gdf = points_gdf.to_crs(projected_crs)
    others_gdf = others_gdf.to_crs(projected_crs)

    # Build spatial index on non-point geometries
    sindex = others_gdf.sindex

    # Collect indices of retained points
    retained_indices = []
    for idx, point in points_gdf.iterrows():
        geom = point.geometry
        buffer_bounds = box(
            geom.x - distance_m,
            geom.y - distance_m,
            geom.x + distance_m,
            geom.y + distance_m,
        )
        candidate_idxs = list(sindex.intersection(buffer_bounds.bounds))
        if candidate_idxs:
            candidates = others_gdf.iloc[candidate_idxs]
            if not candidates[candidates.intersects(buffer_bounds)].empty:
                retained_indices.append(idx)

    # Keep only nearby points
    retained_points_gdf = points_gdf.loc[retained_indices]

    return pd.concat([others_gdf, retained_points_gdf], ignore_index=True).to_crs(
        gdf.crs
    )


def overture_water_main(
    class_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    progress_bar: bool = True,
) -> None:
    rcov_data = RawCovariateData(raw_covariate_dir)
    cov_data = CovariateData(output_dir)

    class_list = CLASS_MAP[class_key].split(",")
    paths = rcov_data.list_overture_paths(theme=THEME, theme_type=THEME_TYPE)

    all_gdfs = []
    for path in tqdm.tqdm(paths, disable=not progress_bar):
        gdf = gpd.read_parquet(
            path,
            columns=["id", "geometry", "class"],
            filters=[("class", "in", class_list)],
        ).drop(columns=["class"])

        # Apply filters based on class_key
        gdf = remove_overlapping_points(gdf)

        if class_key == "river_water":
            gdf = filter_points_near_geometries(gdf)

        if not gdf.empty:
            all_gdfs.append(gdf)

    if not all_gdfs:
        return

    combined_gdf = pd.concat(all_gdfs, ignore_index=True)

    # Only apply final remove_overlapping_points if not river (already applied twice)
    if class_key != "river_water":
        combined_gdf = remove_overlapping_points(combined_gdf)

    cov_data.save_overture_covariate(combined_gdf, COVARIATE, class_key)


@click.command()
@clio.with_overture_class_key(
    choices=CLASS_MAP,
)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_progress_bar()
def overture_water_task(
    overture_class_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    progress_bar: bool = True,
) -> None:
    """Run the Overture Water task."""
    overture_water_main(
        overture_class_key, raw_covariate_dir, output_dir, progress_bar=progress_bar
    )


@click.command()
@clio.with_overture_class_key(
    choices=CLASS_MAP,
    allow_all=True,
)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_queue()
def overture_water(
    overture_class_key: list[str], raw_covariate_dir: str, output_dir: str, queue: str
) -> None:
    """Run the Overture Water pipeline."""
    cov_data = CovariateData(output_dir)
    jobmon.run_parallel(
        runner="pctask process",
        task_name="overture_water",
        node_args={
            "overture-class-key": overture_class_key,
        },
        task_args={
            "raw-covariate-dir": raw_covariate_dir,
            "output-dir": output_dir,
        },
        task_resources={
            "queue": queue,
            "memory": "50G",
            "runtime": "30m",
            "project": "proj_rapidresponse",
        },
        log_root=cov_data.log_dir("process_overture_water"),
    )
