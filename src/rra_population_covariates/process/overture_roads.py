import click
import geopandas as gpd  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
import tqdm  # type: ignore[import-untyped]
from rra_tools import jobmon

from rra_population_covariates import cli_options as clio
from rra_population_covariates import constants as pcc
from rra_population_covariates.data import CovariateData, RawCovariateData

COVARIATE = "roads"
THEME = "transportation"
THEME_TYPE = "segment"
CLASS_MAP = pcc.OVERTURE_CLASS_MAPS[COVARIATE]


def overture_roads_main(
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

        if not gdf.empty:
            all_gdfs.append(gdf)

    if not all_gdfs:
        return

    combined_gdf = pd.concat(all_gdfs, ignore_index=True)
    cov_data.save_overture_covariate(combined_gdf, COVARIATE, class_key)


@click.command()
@clio.with_overture_class_key(
    choices=CLASS_MAP,
)
@clio.with_input_directory("raw_covariate", pcc.RAW_COVARIATES_ROOT)
@clio.with_output_directory(pcc.COVARIATES_ROOT)
@clio.with_progress_bar()
def overture_roads_task(
    overture_class_key: str,
    raw_covariate_dir: str,
    output_dir: str,
    *,
    progress_bar: bool = True,
) -> None:
    """Run the Overture Roads task."""
    overture_roads_main(
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
def overture_roads(
    overture_class_key: list[str], raw_covariate_dir: str, output_dir: str, queue: str
) -> None:
    """Run the Overture Roads pipeline."""
    cov_data = CovariateData(output_dir)
    jobmon.run_parallel(
        runner="pctask process",
        task_name="overture_roads",
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
        log_root=cov_data.log_dir("process_overture_roads"),
    )
