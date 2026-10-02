# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased
### Added
- Open Building Map download (`pcrun extract open_building_map`) and rasterization of parent building types into
  density and volume block rasters (`pcrun process open_building_map`).
- Label-source extract (`pcrun extract open_building_map_label_source`). It classifies every OBM footprint as
  `tagged`, `inherited` or `unknown`, using an OSM ID join to Overture building classes and Overture land-use zones.
- `--by-label-source` for `pcrun process open_building_map`. It splits each labelled parent into `_tagged` and
  `_inherited` layers, 30 rasters per block, checked against the unsplit run.
- Effective-occupancy extract (`pcrun extract open_building_map_effective_occupancy`) and `--effective-occupancy` for
  Step B. Group quarters become `RES4`, identified from OSM site polygons, Overture places, OSM building classes and
  building names. Residential tags overridden by OBM's overriding occupancies become mixed use. Mostly-residential
  mixed use is split by floors, and each mixed share carries its own label source.
- `osmium` (pyosmium) dependency for scanning the OSM planet file.
- `scripts/obm_overlap_migration.py`: the one-time fix, applied on 2026-10-01, for tagged/inherited overlap in the
  first split run.

### Changed
- Earlier OBM covariate runs are kept as `_open_building_map_UNSPLIT/` and `_open_building_map_SPLIT/`. Each new run
  writes to `open_building_map/`, the path the population model reads.

### Fixed
- Step B no longer double-counts ground under both a tagged and an inherited footprint of the same parent. Inherited
  coverage is masked by tagged on the supersampled grid.
