"""One-time fix for the tagged/inherited overlap in the split OBM rasters.

The first Step B run rasterized each parent's tagged and inherited footprints in
separate passes, so ground under both was counted in both halves. Tagged wins on
overlap, which makes the correct inherited layer

    inherited = clip(original - tagged, 0, inherited)

where `original` is the unsplit raster (run 1). This rewrites only the
block-parent pairs whose Step B check found any overlap (`n_differ > 0`); every other
pair already equals `original - tagged`. Step B itself now masks inherited by tagged
on the fine grid (`process/open_building_map.py`), so a rerun needs none of this.

See HANDOFF_OBM_STEP_B_FIX.md (rra_pop_cov handoffs), section 3.

Applied once, on 2026-10-01, to run 2 of the covariate. Run 2 has since moved to
`_open_building_map_SPLIT/` and run 1 to `_open_building_map_UNSPLIT/`, and the path
the model reads, `open_building_map/`, now holds run 3 (the effective-occupancy
rules), which never had the overlap. The paths below follow run 2, so a rerun finds
every block already fixed and does nothing; it must never point at `open_building_map/`.

Usage:
    python obm_overlap_migration.py list                      # pairs and blocks in scope
    python obm_overlap_migration.py run --block B-0059X-0006Y # one block
    python obm_overlap_migration.py run --task-index 0        # one array task
    python obm_overlap_migration.py submit                    # the Slurm array
    python obm_overlap_migration.py check                     # the 3.4 checks
"""

# A command line script: print is its interface.
# ruff: noqa: T201

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import rasterra as rt

from rra_population_covariates import constants as pcc
from rra_population_covariates.data import CovariateData, save_raster

ROOT = Path(pcc.COVARIATES_ROOT)
RESOLUTION = "40"
LIVE = ROOT / pcc.OBM_SPLIT_REFERENCE_DIRNAME / pcc.OBM_VERSION
CHECKS = LIVE / "label_source_checks" / f"{RESOLUTION}m"
FIXED = LIVE / "label_source_checks" / f"{RESOLUTION}m_overlap_fixed"
BACKUP = LIVE / "inherited_prefix_backup" / f"{RESOLUTION}m"
BLOCKS_PER_TASK = 10
WORST_BLOCKS = [
    "B-0061X-0007Y",
    "B-0055X-0010Y",
    "B-0059X-0007Y",
    "B-0059X-0006Y",
    "B-0071X-0002Y",
    "B-0076X-0003Y",
]


def pairs_in_scope() -> pd.DataFrame:
    """Block-parent pairs with any overlap, sorted so array tasks are reproducible."""
    checks = pd.concat(
        [pd.read_parquet(p) for p in sorted(CHECKS.glob("*.parquet"))],
        ignore_index=True,
    )
    scope = checks[(checks["parent"] != "unknown") & (checks["n_differ"] > 0)]
    return scope[["block_key", "parent", "n_differ"]].sort_values(
        ["block_key", "parent"], ignore_index=True
    )


def read(path: Path) -> np.ndarray:
    return rt.load_raster(path).to_numpy()


def fix_pair(
    cov_data: CovariateData, block_key: str, parent: str, measure: str
) -> dict[str, Any]:
    block_dir = LIVE / f"{RESOLUTION}m" / block_key
    name = f"{parent}_inherited_{measure}.tif"
    live_path = block_dir / name
    backup_path = BACKUP / block_key / name

    # Back up the pre-fix layer once, by copying, and always compute from the backup so
    # rerunning an interrupted task gives the same result rather than compounding.
    if not backup_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_backup = backup_path.with_name(f".{name}.copytmp")
        shutil.copy2(live_path, tmp_backup)
        tmp_backup.replace(backup_path)

    inherited_raster = rt.load_raster(backup_path)
    inherited = inherited_raster.to_numpy()
    tagged = read(block_dir / f"{parent}_tagged_{measure}.tif")
    original = read(
        cov_data.open_building_map_reference_raster_path(
            RESOLUTION, block_key, parent, measure
        )
    )

    land = np.isfinite(inherited)
    i0, t0, o0 = (np.nan_to_num(a) for a in (inherited, tagged, original))
    new = np.where(land, np.clip(o0 - t0, 0, i0), np.nan).astype(np.float32)

    # Write via a temporary file, verify it, then swap it in.
    tmp_path = block_dir / f".{name}.fixtmp.tif"
    save_raster(
        rt.RasterArray(
            new,
            transform=inherited_raster.transform,
            crs=inherited_raster.crs,
            no_data_value=np.nan,
        ),
        tmp_path,
    )
    if not np.array_equal(read(tmp_path), new, equal_nan=True):
        tmp_path.unlink(missing_ok=True)
        msg = f"{tmp_path} did not read back as written."
        raise RuntimeError(msg)
    tmp_path.replace(live_path)

    def n_differ(layer: np.ndarray) -> int:
        return int((land & (np.abs(t0 + layer - o0) > pcc.OBM_CHECK_TOLERANCE)).sum())

    new0 = np.nan_to_num(new)
    return {
        "block_key": block_key,
        "parent": parent,
        "measure": measure,
        "n_differ_before": n_differ(i0),
        "n_differ_after": n_differ(new0),
        "max_abs_diff_after": float(np.abs(np.where(land, t0 + new0 - o0, 0)).max()),
        "inherited_before": float(i0[land].sum()),
        "inherited_after": float(new0[land].sum()),
        "tagged": float(t0[land].sum()),
    }


def fix_block(cov_data: CovariateData, block_key: str, parents: list[str]) -> None:
    done = FIXED / f"{block_key}.parquet"
    if done.exists():
        print(f"{block_key}: already fixed; skipping.", flush=True)
        return
    block_dir = LIVE / f"{RESOLUTION}m" / block_key
    for leftover in block_dir.glob(".*.fixtmp.tif"):
        leftover.unlink()

    rows = [
        fix_pair(cov_data, block_key, parent, measure)
        for parent in parents
        for measure in pcc.OBM_MEASURES
    ]
    FIXED.mkdir(parents=True, exist_ok=True)
    tmp = done.with_name(f".{done.name}.tmp")
    pd.DataFrame(rows).to_parquet(tmp, index=False)
    tmp.replace(done)
    worst = max(r["n_differ_after"] for r in rows)
    print(
        f"{block_key}: fixed {len(parents)} parents; max n_differ_after {worst}",
        flush=True,
    )


def run(args: argparse.Namespace) -> None:
    scope = pairs_in_scope()
    blocks = sorted(scope["block_key"].unique())
    if args.block:
        chosen = [args.block]
    else:
        start = args.task_index * BLOCKS_PER_TASK
        chosen = blocks[start : start + BLOCKS_PER_TASK]
    cov_data = CovariateData(ROOT)
    for block_key in chosen:
        parents = scope.loc[scope["block_key"] == block_key, "parent"].tolist()
        if not parents:
            print(f"{block_key}: no overlap; nothing to fix.", flush=True)
            continue
        fix_block(cov_data, block_key, parents)


def submit(args: argparse.Namespace) -> None:
    n_blocks = pairs_in_scope()["block_key"].nunique()
    n_tasks = -(-n_blocks // BLOCKS_PER_TASK)
    log_dir = ROOT / "logs" / "obm_overlap_migration"
    log_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--array=0-{n_tasks - 1}%{args.concurrency}",
        "--job-name=obm_overlap_fix",
        "--partition=all.q",
        "--account=proj_rapidresponse",
        "--mem=6G",
        "--cpus-per-task=1",
        "--time=01:00:00",
        f"--output={log_dir}/%A_%a.out",
        f"--error={log_dir}/%A_%a.err",
        f"--wrap={sys.executable} {Path(__file__).resolve()} run --task-index $SLURM_ARRAY_TASK_ID",
    ]
    print(f"{n_blocks} blocks in {n_tasks} tasks of {BLOCKS_PER_TASK}.")
    subprocess.run(command, check=True)  # noqa: S603


def check(_: argparse.Namespace) -> None:
    scope = pairs_in_scope()
    blocks = sorted(scope["block_key"].unique())
    tables = sorted(FIXED.glob("*.parquet"))
    print(
        f"Fixed blocks: {len(tables)} of {len(blocks)}; "
        f"missing: {sorted(set(blocks) - {p.stem for p in tables})[:10]}"
    )
    r = pd.concat([pd.read_parquet(p) for p in tables], ignore_index=True)
    print(f"Pairs fixed: {r[r.measure == 'density'].shape[0]} of {len(scope)}")

    print(
        "\n1. No overlap left:",
        f"n_differ_after total {int(r.n_differ_after.sum())},",
        f"max_abs_diff_after {r.max_abs_diff_after.max():.2e}",
        f"(density {r[r.measure == 'density'].max_abs_diff_after.max():.2e})",
    )
    gained = r[r.inherited_after > r.inherited_before + 1e-6]
    print(
        f"2. Nothing gained: pairs with inherited_after > inherited_before: {len(gained)}"
    )

    # 3. Inherited share of labelled density, from the Step B check tables with the
    # fixed pairs swapped in.
    checks = pd.concat(
        [pd.read_parquet(p) for p in sorted(CHECKS.glob("*.parquet"))],
        ignore_index=True,
    )
    lab = checks[checks.parent != "unknown"].set_index(["block_key", "parent"])
    fixed = r[r.measure == "density"].set_index(["block_key", "parent"])
    after = lab["density_inherited"].copy()
    after.loc[fixed.index] = fixed["inherited_after"]
    by_parent = pd.DataFrame(
        {
            "before": lab["density_inherited"].groupby("parent").sum(),
            "after": after.groupby("parent").sum(),
            "tagged": lab["density_tagged"].groupby("parent").sum(),
        }
    )
    by_parent["share_before"] = (
        100 * by_parent.before / (by_parent.before + by_parent.tagged)
    )
    by_parent["share_after"] = (
        100 * by_parent.after / (by_parent.after + by_parent.tagged)
    )
    total_before = by_parent.before.sum() / (
        by_parent.before.sum() + by_parent.tagged.sum()
    )
    total_after = by_parent.after.sum() / (
        by_parent.after.sum() + by_parent.tagged.sum()
    )
    print(
        f"3. Inherited share of labelled density: {100 * total_before:.2f}% -> {100 * total_after:.2f}%"
    )
    print(by_parent[["share_before", "share_after"]].round(2).to_string())

    print("\n4. Worst blocks (density):")
    worst = r[(r.measure == "density") & r.block_key.isin(WORST_BLOCKS)]
    print(
        worst[
            [
                "block_key",
                "parent",
                "n_differ_before",
                "n_differ_after",
                "inherited_before",
                "inherited_after",
            ]
        ].to_string(index=False)
    )

    expected = {
        f"{layer}_{m}.tif"
        for layer in pcc.OBM_LABEL_SOURCE_LAYERS
        for m in pcc.OBM_MEASURES
    }
    block_dirs = [p for p in (LIVE / f"{RESOLUTION}m").iterdir() if p.is_dir()]
    bad = [p.name for p in block_dirs if {f.name for f in p.iterdir()} != expected]
    tmps = list((LIVE / f"{RESOLUTION}m").glob("*/.*fixtmp*"))
    print(
        f"\n5. Totals: {len(block_dirs)} blocks, {len(bad)} without exactly the 30 rasters "
        f"{bad[:5]}, {len(tmps)} leftover temporary files"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p_run = sub.add_parser("run")
    group = p_run.add_mutually_exclusive_group(required=True)
    group.add_argument("--block")
    group.add_argument("--task-index", type=int)
    p_submit = sub.add_parser("submit")
    p_submit.add_argument("--concurrency", type=int, default=120)
    sub.add_parser("check")
    args = parser.parse_args()

    if args.command == "list":
        scope = pairs_in_scope()
        print(
            f"{len(scope)} pairs, {scope.block_key.nunique()} blocks, {2 * len(scope)} files"
        )
        print(scope.parent.value_counts().to_string())
    else:
        {"run": run, "submit": submit, "check": check}[args.command](args)


if __name__ == "__main__":
    main()
