# RRA Population Covariates
---

**Documentation**: [https://ihmeuw.github.io/rra-population-covariates](https://ihmeuw.github.io/rra-population-covariates)

**Source Code**: [https://github.com/ihmeuw/rra-population-covariates](https://github.com/ihmeuw/rra-population-covariates)

---

Proccessing pipeline for population model covariates

## Pipeline stages

The pipeline has two stages, each a subpackage with its own CLI group:

- **`extract`** — [src/rra_population_covariates/extract/](src/rra_population_covariates/extract/) downloads raw source data into `…/01-raw-data/covariates/`. Run as `pcrun extract <source>`.
- **`process`** — [src/rra_population_covariates/process/](src/rra_population_covariates/process/) turns raw data into model-ready covariates under `…/02-processed-data/covariates/`. Run as `pcrun process <covariate>`.

Sources that are already staged on disk (Overture) have no extract step; sources we download ourselves (Open Building Map) do.

Covariates come in two output shapes:

- **Class-filtered GeoParquet** (Overture roads, water) — one vector file per class, consumed by whatever downstream step needs that layer.
- **Block rasters** (Open Building Map) — GeoTIFFs on the population model's modeling frame, directly stackable with the model's other features.

The Overture scripts share the same shape:

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

### Open Building Map — download — [extract/open_building_map.py](src/rra_population_covariates/extract/open_building_map.py)

**Why.** [Open Building Map](https://www.openbuildingmap.org/) (Oostwegel et al. 2025, [doi:10.5880/GFZ.LKUT.2025.002](https://doi.org/10.5880/GFZ.LKUT.2025.002)) is a global building inventory of ~2.7 billion footprints that combines OpenStreetMap with the Google and Microsoft ML footprint datasets, and — unlike those sources on their own — carries an **occupancy type** per building following the [GEM Building Taxonomy v2.0](https://github.com/gem/gem_taxonomy). That attribute is what lets us split building footprints into residential and non-residential covariates. It is published as 1,271 bz2-compressed GeoPackages, one per zoom-6 quadkey, totalling 284 GB compressed.

**How.** One jobmon task per tile, discovered by scraping the publisher's directory index. Each task streams the archive through `bz2.BZ2Decompressor` straight to a `.gpkg.tmp` and renames on success, so the compressed file is never written to disk (the largest single tile is 11 GB compressed) and an interrupted download cannot leave behind a truncated file that looks complete. Tiles are skipped if already present unless `--overwrite`. The runner also downloads the five reference tables published alongside the data, including the authoritative occupancy code list, into `…/open_building_map/<version>/reference/`.

Tile sizes span four orders of magnitude and cluster geographically, so the fan-out is one task per tile rather than per quadkey prefix, letting the scheduler balance the load. Concurrency is capped so the archive host sees a modest number of simultaneous connections.

**Output:** `…/01-raw-data/covariates/open_building_map/<version>/building.<quadkey>.gpkg`

**Run the full download:**

```sh
pcrun extract open_building_map --queue all.q
```

**Run a single tile (debugging / re-runs):**

```sh
pctask extract open_building_map --obm-quadkey 021230
```

### Open Building Map — rasterization — [process/open_building_map.py](src/rra_population_covariates/process/open_building_map.py)

**Why.** The population model consumes block rasters on its own modeling frame, so building polygons split by occupancy have to be rasterized onto that grid. The output is one raster per **parent building type** per block, valued as the **fraction of each pixel covered** by footprints of that type.

**How — grid and mask.** Each block is written onto the grid of a building-density tile loaded through `BuildingDensityData`, which supplies both the affine transform and the land mask. This guarantees the rasters are co-registered pixel-for-pixel with the model's existing features rather than merely close to them. `nan` means outside the modeled land area; `0` means land with no footprint of that type.

**How — harmonizing the two tilings.** Open Building Map tiles the world by Web Mercator quadkey; the modeling frame uses `ESRI:54034` blocks. Because 54034 is cylindrical (x depends only on longitude, y only on latitude), a block rectangle maps to an exact lon/lat rectangle, so the crosswalk is analytic and needs no lookup table. Each block resolves to the 1–4 quadkey GeoPackages it overlaps, which are then read with a bbox filter pushed into the GeoPackage's R-tree index. Blocks are processed one 512-pixel tile at a time: a block spans hundreds of kilometres and could otherwise hold tens of millions of footprints at once.

**How — fractional coverage.** A median footprint is 127 m², against 10,000 m² for a 100 m pixel and 1,600 m² for a 40 m pixel, so a binary rasterization is wrong either way. `all_touched=True` inflates every sub-pixel building to a whole pixel — roughly 79× too much area at 100 m — while pixel-centre rasterization silently discards buildings that contain no pixel centre, measured at **97.8% of buildings dropped at 100 m**, and discards them *size-selectively* so only large structures survive.

Instead each geometry group is rasterized onto a grid `OBM_SUPERSAMPLE_FACTOR` (10) times finer than the target and averaged back down, which yields the covered fraction directly. The factor is relative to the target resolution — 10 m subpixels at 100 m, 4 m at 40 m — so both products get 100 subcells per pixel and 0.01 precision. This is mass-preserving: total footprint area is identical at 4 m, 40 m and 100 m, and a full-tile check recovers 99.0% of the vector area, the shortfall being edge slivers lost to quantization.

**How — parent building types.** Occupancy codes are grouped into the parent types in [`OBM_PARENT_BUILDING_TYPES`](src/rra_population_covariates/constants.py#L140): `residential_mu`, `commercial`, `industrial`, `agriculture`, `government`, `education`, `assembly`, `unknown`. Codes follow their taxonomy prefix apart from `RES3`, which is GEM "Temporary lodging" (hotels, motels, guest lodges) and is grouped with commercial because it houses transient occupants rather than residents.

Mixed-use codes are not assigned wholly to one parent. A mixed-use building is part one use and part another, so its footprint is **split 75/25** between the two uses the taxonomy names, per [`OBM_MIXED_USE_SPLITS`](src/rra_population_covariates/constants.py#L187):

| code | GEM label | 75% | 25% |
|---|---|---|---|
| MIX1 | Mostly residential and commercial | `residential_mu` | `commercial` |
| MIX2 | Mostly commercial and residential | `commercial` | `residential_mu` |
| MIX3 | Mostly commercial and industrial | `commercial` | `industrial` |
| MIX4 | Mostly residential and industrial | `residential_mu` | `industrial` |
| MIX5 | Mostly industrial and commercial | `industrial` | `commercial` |
| MIX6 | Mostly industrial and residential | `industrial` | `residential_mu` |

Because the weights sum to 1, area is still conserved. Codes belonging wholly to one parent are rasterized together in a single pass so overlapping footprints of the same type are unioned rather than summed; only the mixed-use codes are rasterized individually and scaled.

**A note on values above 1.** Pixel values are fractions and normally fall in `[0, 1]`. A value above 1 means the same ground was counted twice, which happens when an OpenStreetMap building and an ML-derived footprint describe the same structure. It is *not* a signal of building density — several buildings in one pixel is the ordinary case and stays within `[0, 1]`. Measured on the densest test block (29.1M land pixels): zero pixels above 1 in any single layer, and 22 of 1,554,305 covered pixels (0.0014%, max 1.610) when summing all layers. These are left unclipped so the artifact stays visible in QA, which means **consumers must not assume a hard upper bound of 1**.

**Output:** `…/02-processed-data/covariates/open_building_map/<version>/<resolution>m/<block_key>/<parent_building_type>.tif` — float32, `nodata=nan`, `ESRI:54034`, ZSTD-compressed, tiled 512×512, matching the population model's own raster parameters.

**Run both resolutions:**

```sh
# Fan out one job per (resolution, block) via jobmon
pcrun process open_building_map --queue all.q
```

**Run a single resolution or block (debugging / re-runs):**

```sh
pcrun process open_building_map --obm-resolution 100 --queue all.q
pctask process open_building_map --obm-resolution 100 --obm-block-key B-0007X-0002Y
```

Validation for the rasterized output lives in [notebooks/2026_08_03_validate_obm_rasters.ipynb](notebooks/2026_08_03_validate_obm_rasters.ipynb), which checks structural completeness, grid alignment against the model template, value domain, area conservation recomputed independently from the vectors, the direction of the mixed-use weights, tile-seam integrity, and agreement between the 100 m and 40 m products.

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
