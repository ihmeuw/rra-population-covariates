import bz2
import re
from pathlib import Path
from typing import Any

import click
import requests
from rra_tools import jobmon

from rra_population_covariates import cli_options as clio
from rra_population_covariates import constants as pcc
from rra_population_covariates.data import RawCovariateData

# Open Building Map publishes a plain Apache directory index of bz2-compressed
# GeoPackages, one per zoom-6 quadkey, e.g. "building.002202.gpkg.bz2".
TILE_PATTERN = re.compile(r'href="building\.(\d+)\.gpkg\.bz2"')
CHUNK_SIZE = 16 * 1024 * 1024
TIMEOUT = 60
# Tiles are downloaded one per task; the archive is a public research server, so
# we keep the number of simultaneous connections to it modest.
MAX_CONCURRENT_DOWNLOADS = 20


def tile_url(quadkey: str) -> str:
    return f"{pcc.OBM_ROOT_URL}building.{quadkey}.gpkg.bz2"


def list_remote_quadkeys(quadkey_prefixes: tuple[str, ...]) -> list[str]:
    """Get the quadkeys of all remote tiles starting with one of the prefixes."""
    response = requests.get(pcc.OBM_ROOT_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return sorted(
        quadkey
        for quadkey in TILE_PATTERN.findall(response.text)
        if quadkey.startswith(quadkey_prefixes)
    )


def download_tile(url: str, path: Path) -> tuple[int, int]:
    """Download a bz2-compressed tile, decompressing it as it streams.

    The archive is never written to disk; only the decompressed GeoPackage is,
    first to a temporary path and then renamed into place so an interrupted
    download can't leave behind a truncated file that looks complete.

    Returns the compressed and decompressed sizes in bytes.
    """
    tmp_path = path.with_suffix(".gpkg.tmp")
    tmp_path.unlink(missing_ok=True)

    decompressor = bz2.BZ2Decompressor()
    stream_complete = False
    compressed_bytes, decompressed_bytes = 0, 0

    try:
        with (
            requests.get(url, stream=True, timeout=TIMEOUT) as response,
            tmp_path.open("wb") as out_file,
        ):
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                compressed_bytes += len(chunk)
                buffer = chunk
                while buffer:
                    decompressed_bytes += out_file.write(
                        decompressor.decompress(buffer)
                    )
                    if decompressor.eof:
                        # A bz2 file may be several concatenated streams; anything
                        # left over belongs to the next one.
                        buffer = decompressor.unused_data
                        decompressor = bz2.BZ2Decompressor()
                        stream_complete = not buffer
                    else:
                        buffer = b""
                        stream_complete = False

        if not stream_complete:
            msg = f"Download of {url} ended mid-stream; the archive is truncated."
            raise RuntimeError(msg)
    finally:
        # Never leave a partial file behind for a later run to trip over.
        if not stream_complete:
            tmp_path.unlink(missing_ok=True)

    tmp_path.replace(path)
    return compressed_bytes, decompressed_bytes


def open_building_map_main(
    quadkey: str,
    output_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    rcov_data = RawCovariateData(output_dir)
    rcov_data.create_open_building_map_root()

    path = rcov_data.open_building_map_path(quadkey)
    if path.exists() and not overwrite:
        click.echo(f"{path} already exists; skipping.")
        return

    compressed_bytes, decompressed_bytes = download_tile(tile_url(quadkey), path)
    click.echo(
        f"{quadkey}: {compressed_bytes / 1024**2:.1f} MiB compressed -> "
        f"{decompressed_bytes / 1024**2:.1f} MiB on disk"
    )


@click.command()
@clio.with_obm_quadkey()
@clio.with_output_directory(pcc.RAW_COVARIATES_ROOT)
@clio.with_overwrite()
def open_building_map_task(
    obm_quadkey: str,
    output_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    """Run the Open Building Map download task."""
    open_building_map_main(obm_quadkey, output_dir, overwrite=overwrite)


@click.command()
@clio.with_obm_quadkey_prefix(
    choices=pcc.OBM_QUADKEY_PREFIXES,
    allow_all=True,
)
@clio.with_output_directory(pcc.RAW_COVARIATES_ROOT)
@clio.with_overwrite()
@clio.with_queue()
def open_building_map(
    obm_quadkey_prefix: list[str],
    output_dir: str,
    queue: str,
    *,
    overwrite: bool = False,
) -> None:
    """Run the Open Building Map download pipeline."""
    rcov_data = RawCovariateData(output_dir)

    # Tile sizes span four orders of magnitude and cluster geographically, so we
    # fan out one task per tile and let the scheduler balance the load rather
    # than grouping tiles by quadkey prefix.
    quadkeys = list_remote_quadkeys(tuple(obm_quadkey_prefix))
    if not quadkeys:
        msg = f"No Open Building Map tiles found for prefixes {obm_quadkey_prefix}."
        raise ValueError(msg)
    click.echo(f"Found {len(quadkeys)} Open Building Map tiles.")

    task_args: dict[str, Any] = {"output-dir": output_dir}
    if overwrite:
        # A value of None renders as a bare command line flag.
        task_args["overwrite"] = None

    jobmon.run_parallel(
        runner="pctask extract",
        task_name="open_building_map",
        flat_node_args=(("obm-quadkey",), [(quadkey,) for quadkey in quadkeys]),
        task_args=task_args,
        task_resources={
            "queue": queue,
            "memory": "5G",
            "runtime": "4h",
            "project": "proj_rapidresponse",
        },
        concurrency_limit=MAX_CONCURRENT_DOWNLOADS,
        max_attempts=3,
        log_root=rcov_data.log_dir("extract_open_building_map"),
    )
