from collections.abc import Callable, Collection

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
