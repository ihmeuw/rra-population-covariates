# RRA Population Covariates
---

**Documentation**: [https://ihmeuw.github.io/rra-population-covariates](https://ihmeuw.github.io/rra-population-covariates)

**Source Code**: [https://github.com/ihmeuw/rra-population-covariates](https://github.com/ihmeuw/rra-population-covariates)

---

Proccessing pipeline for population model covariates

## Processing pipelines

Each covariate has a processing script in [src/rra_population_covariates/process/](src/rra_population_covariates/process/) that turns raw [Overture Maps](https://overturemaps.org/) parquet partitions into a single class-filtered GeoParquet per covariate class.

All scripts share the same shape:

1. Read the relevant Overture partitions (`theme=<theme>` / `type=<theme_type>`).
2. Apply a predicate-pushdown filter on the `class` column using a class map defined in [constants.py](src/rra_population_covariates/constants.py).
3. Concatenate the filtered partitions.
4. Save a single GeoParquet to `…/covariates/overture/<covariate>/<class_key>.parquet` via `CovariateData.save_overture_covariate`.

Each script exposes two Click commands:

- `*_task` — the per-class worker (one class key in, one parquet out). Invoked as `pctask process <covariate> …`.
- `*` — the orchestrator that fans the task out across all class keys in parallel via `rra_tools.jobmon.run_parallel`. Invoked as `pcrun process <covariate> …`.

Both are registered through the entry points in [pyproject.toml](pyproject.toml) and wired up in [process/__init__.py](src/rra_population_covariates/process/__init__.py).

### Overture roads — [overture_roads.py](src/rra_population_covariates/process/overture_roads.py)

**Why.** Population-model covariates that depend on road presence, density, or accessibility need road segments split by functional class (motorway vs. residential vs. track behave very differently for accessibility modeling). Overture ships road segments tagged with a `class` attribute; this script materializes one GeoParquet per class so downstream covariate steps can pull the layer they need without re-filtering the full transportation dataset every time.

**How.** Reads Overture's `theme=transportation` / `type=segment` partitions, applies a predicate-pushdown filter on the `class` column using `DRIVABLE_CLASS_MAP` in [constants.py:10-21](src/rra_population_covariates/constants.py#L10-L21), drops the `class` column, concatenates partitions, and writes the result. Pure ETL — no geometry cleaning. Most classes map one-to-one to the Overture class; `residential` bundles `residential` + `living_street`.

**Run all road classes:**

```sh
# Fan out one job per road class via jobmon
pcrun process overture_roads --queue all.q
```

**Run a single class (debugging / re-runs):**

```sh
pctask process overture_roads --overture-class-key motorway
```

### Overture water — [overture_water.py](src/rra_population_covariates/process/overture_water.py)

**Why.** Different water-body types affect population differently (a coastal ocean shoreline, an inland lake, and a sewage ditch all matter for separate covariates). Overture's water layer also mixes points, lines, and polygons for the same real-world feature — e.g. a lake may appear as both a polygon and a centroid point — which inflates counts and breaks distance calculations downstream. This script produces one cleaned GeoParquet per water-body class so downstream covariates get a de-duplicated layer per water type.

**How.** Same ETL skeleton as roads — filter the `theme=base` / `type=water` partitions on `class` using `WATER_CLASS_MAP` in [constants.py:23-31](src/rra_population_covariates/constants.py#L23-L31), then concat and write. Each class bundles several Overture subclasses (e.g. `inland_water` = `lake,pond,oxbow,spring`; `manmade_freshwater` = `canal,basin,fishpond,reservoir`). On top of the ETL, two geometry-cleaning passes run:

- `remove_overlapping_points` — drops point features that fall inside any non-point geometry in the same class. De-duplicates places where a lake is represented as both a polygon and a centroid point.
- `filter_points_near_geometries` — for `river_water` only, drops isolated point features that are not within ~500m of any non-point geometry. Removes orphan river points with no associated river line/polygon.

**Run all water classes:**

```sh
# Fan out one job per water class via jobmon
pcrun process overture_water --queue all.q
```

**Run a single class (debugging / re-runs):**

```sh
pctask process overture_water --overture-class-key river_water
```

## Development

* Clone this repository
* Requirements:
  * [Poetry](https://python-poetry.org/)
  * Python 3.12+
* Create a virtual environment and install the dependencies

```sh
poetry install
```

* Activate the virtual environment

```sh
poetry shell
```

### Testing

```sh
pytest
```

### Documentation

The documentation is automatically generated from the content of the `docs` directory and from the docstrings
 of the public signatures of the source code. The documentation is updated and published as a [Github project page
 ](https://pages.github.com/) automatically as part each release.

### Pre-commit

Pre-commit hooks run all the auto-formatting (`ruff format`), linters (e.g. `ruff` and `mypy`), and other quality
 checks to make sure the changeset is in good shape before a commit/push happens.

You can install the hooks with (runs for each commit):

```sh
pre-commit install
```

Or if you want them to run only for each push:

```sh
pre-commit install -t pre-push
```

Or if you want e.g. want to run all checks manually for all files:

```sh
pre-commit run --all-files
```

---
