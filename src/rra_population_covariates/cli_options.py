from collections.abc import Callable, Collection

import click
from rra_tools.cli_tools import (
    RUN_ALL,
    convert_choice,
    process_choices,
    with_choice,
    with_debugger,
    with_dry_run,
    with_input_directory,
    with_num_cores,
    with_output_directory,
    with_overwrite,
    with_progress_bar,
    with_queue,
    with_verbose,
)

from rra_population_covariates import constants as pcc


def with_overture_class_key[**P, T](
    choices: Collection[str] | None = None,
    *,
    allow_all: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    return with_choice(
        "overture_class_key",
        allow_all=allow_all,
        choices=choices,
        help="Name of the Overture class key to process.",
    )


def with_obm_resolution[**P, T](
    *,
    allow_all: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    return with_choice(
        "obm_resolution",
        allow_all=allow_all,
        choices=pcc.OBM_RESOLUTIONS,
        help="Modeling frame resolution in meters.",
    )


def with_obm_block_key[**P, T]() -> Callable[[Callable[P, T]], Callable[P, T]]:
    return click.option(
        "--obm-block-key",
        type=click.STRING,
        required=True,
        help="Modeling frame block key to rasterize.",
    )


def with_obm_quadkey[**P, T]() -> Callable[[Callable[P, T]], Callable[P, T]]:
    return click.option(
        "--obm-quadkey",
        type=click.STRING,
        required=True,
        help="Zoom-6 quadkey of the Open Building Map tile to download.",
    )


def with_obm_quadkey_prefix[**P, T](
    choices: Collection[str] | None = None,
    *,
    allow_all: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    return with_choice(
        "obm_quadkey_prefix",
        allow_all=allow_all,
        choices=choices,
        help="Zoom-2 quadkey prefix of the Open Building Map tiles to process.",
    )


__all__ = [
    "RUN_ALL",
    "convert_choice",
    "process_choices",
    "with_choice",
    "with_debugger",
    "with_dry_run",
    "with_input_directory",
    "with_num_cores",
    "with_output_directory",
    "with_overwrite",
    "with_progress_bar",
    "with_queue",
    "with_verbose",
]
