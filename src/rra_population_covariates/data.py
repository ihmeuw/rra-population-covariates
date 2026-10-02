from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd  # type: ignore[import-untyped]
from rra_tools.shell_tools import mkdir, touch

from rra_population_covariates import constants as pcc

if TYPE_CHECKING:
    import rasterra as rt


class RawCovariateData:
    def __init__(self, root: str | Path = pcc.RAW_COVARIATES_ROOT) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def overture(self) -> Path:
        return self._root / "overture" / "2025-04-23.0"

    def list_overture_paths(self, theme: str, theme_type: str) -> list[Path]:
        root = self.overture / f"theme={theme}" / f"type={theme_type}"
        return list(Path(root).glob("*.parquet"))

    @property
    def osm_planet(self) -> Path:
        # Dated 2025-04-18, 14 days after the OBM snapshot.
        return self._root / "osm" / "planet-latest.osm.pbf"

    @property
    def logs(self) -> Path:
        return self._root / "logs"

    def log_dir(self, step_name: str) -> Path:
        mkdir(self.logs, exist_ok=True)
        return self.logs / step_name

    @property
    def open_building_map(self) -> Path:
        return self._root / "open_building_map" / pcc.OBM_VERSION

    def create_open_building_map_root(self) -> None:
        mkdir(self.open_building_map, exist_ok=True, parents=True)
        mkdir(self.open_building_map_reference, exist_ok=True)

    def open_building_map_path(self, quadkey: str) -> Path:
        return self.open_building_map / f"building.{quadkey}.gpkg"

    @property
    def open_building_map_reference(self) -> Path:
        return self.open_building_map / "reference"

    def open_building_map_reference_path(self, filename: str) -> Path:
        return self.open_building_map_reference / filename

    def load_obm_overriding_occupancies(self) -> set[str]:
        """Get the occupancy codes assigned from an explicit source tag.

        These are the higher-confidence labels; the rest are inferred.
        """
        path = self.open_building_map_reference_path(
            pcc.OBM_OVERRIDING_OCCUPANCIES_FILE
        )
        if not path.exists():
            msg = (
                f"{path} not found. Run 'pcrun extract open_building_map' to "
                "download the Open Building Map reference files."
            )
            raise FileNotFoundError(msg)
        lines = path.read_text().splitlines()
        return {line.split(",")[0].strip() for line in lines if line.strip()}

    def list_open_building_map_paths(self) -> list[Path]:
        return sorted(self.open_building_map.glob("building.*.gpkg"))

    # Overture lookups for the label-source classification. Each is partitioned by
    # OBM quadkey (quadkey=<qk>/part-<source>.parquet) with one part per Overture
    # source file, plus a marker per source file written once all its parts are.
    def obm_overture_lookup(self, lookup: str) -> Path:
        return self.open_building_map / f"overture_{lookup}"

    def obm_overture_lookup_part_path(
        self, lookup: str, quadkey: str, source_stem: str
    ) -> Path:
        return (
            self.obm_overture_lookup(lookup)
            / f"quadkey={quadkey}"
            / f"part-{source_stem}.parquet"
        )

    def list_obm_overture_lookup_parts(self, lookup: str, quadkey: str) -> list[Path]:
        root = self.obm_overture_lookup(lookup) / f"quadkey={quadkey}"
        return sorted(root.glob("part-*.parquet"))

    def obm_overture_lookup_marker_path(self, lookup: str, source_stem: str) -> Path:
        return self.obm_overture_lookup(lookup) / "_sources" / f"{source_stem}.parquet"

    def list_obm_overture_lookup_markers(self, lookup: str) -> list[Path]:
        return sorted((self.obm_overture_lookup(lookup) / "_sources").glob("*.parquet"))

    @property
    def obm_land_use_classes_path(self) -> Path:
        return self.open_building_map / "overture_land_use_classes.csv"


class CovariateData:
    def __init__(self, root: str | Path = pcc.COVARIATES_ROOT) -> None:
        self._root = Path(root)
        self._create_model_root()

    def _create_model_root(self) -> None:
        mkdir(self.root, exist_ok=True)
        mkdir(self.logs, exist_ok=True)
        mkdir(self.overture, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def logs(self) -> Path:
        return self._root / "logs"

    def log_dir(self, step_name: str) -> Path:
        return self.logs / step_name

    @property
    def open_building_map(self) -> Path:
        return self.open_building_map_version(pcc.OBM_VERSION)

    def open_building_map_version(self, version: str) -> Path:
        # Covariate versions sit side by side, e.g. 2025-04-04 and
        # 2025-04-04_effective_occupancy.
        return self._root / "open_building_map" / version

    def open_building_map_raster_path(
        self,
        resolution: str,
        block_key: str,
        parent_building_type: str,
        measure: str,
        version: str = pcc.OBM_VERSION,
    ) -> Path:
        if measure not in pcc.OBM_MEASURES:
            msg = f"Unknown measure {measure!r}; expected one of {pcc.OBM_MEASURES}."
            raise ValueError(msg)
        return (
            self.open_building_map_version(version)
            / f"{resolution}m"
            / block_key
            / f"{parent_building_type}_{measure}.tif"
        )

    def save_open_building_map_raster(  # noqa: PLR0913
        self,
        raster: "rt.RasterArray",
        resolution: str,
        block_key: str,
        parent_building_type: str,
        measure: str,
        version: str = pcc.OBM_VERSION,
    ) -> None:
        path = self.open_building_map_raster_path(
            resolution, block_key, parent_building_type, measure, version
        )
        mkdir(path.parent, exist_ok=True, parents=True)
        save_raster(raster, path)

    @property
    def open_building_map_reference(self) -> Path:
        return self._root / pcc.OBM_REFERENCE_DIRNAME / pcc.OBM_VERSION

    def open_building_map_reference_raster_path(
        self,
        resolution: str,
        block_key: str,
        parent_building_type: str,
        measure: str,
    ) -> Path:
        return (
            self.open_building_map_reference
            / f"{resolution}m"
            / block_key
            / f"{parent_building_type}_{measure}.tif"
        )

    def open_building_map_split_reference_raster_path(
        self,
        resolution: str,
        block_key: str,
        layer: str,
        measure: str,
    ) -> Path:
        return (
            self._root
            / pcc.OBM_SPLIT_REFERENCE_DIRNAME
            / pcc.OBM_VERSION
            / f"{resolution}m"
            / block_key
            / f"{layer}_{measure}.tif"
        )

    def open_building_map_check_path(
        self, resolution: str, block_key: str, version: str = pcc.OBM_VERSION
    ) -> Path:
        # Kept out of the resolution directory, which holds only block directories.
        return (
            self.open_building_map_version(version)
            / "label_source_checks"
            / f"{resolution}m"
            / f"{block_key}.parquet"
        )

    @property
    def open_building_map_classified(self) -> Path:
        return self._root / "open_building_map_classified"

    def open_building_map_classified_path(self, quadkey: str) -> Path:
        # Named after the raw tile it classifies, building.<quadkey>.gpkg.
        return self.open_building_map_classified / f"building.{quadkey}.parquet"

    def open_building_map_classified_summary_path(self, quadkey: str) -> Path:
        return (
            self.open_building_map_classified
            / "summary"
            / f"building.{quadkey}.parquet"
        )

    @property
    def open_building_map_effective(self) -> Path:
        return self._root / "open_building_map_effective_occupancy"

    def open_building_map_effective_path(self, quadkey: str) -> Path:
        # Sparse: only the footprints whose occupancy a rule changed.
        return self.open_building_map_effective / f"building.{quadkey}.parquet"

    @property
    def overture(self) -> Path:
        return self._root / "overture"

    def overture_path(self, covariate: str, class_key: str) -> Path:
        return self.overture / covariate / f"{class_key}.parquet"

    def save_overture_covariate(
        self, gdf: gpd.GeoDataFrame, covariate: str, class_key: str
    ) -> None:
        path = self.overture_path(covariate, class_key)
        mkdir(path.parent, exist_ok=True)
        save_geo_parquet(gdf, path)


def save_raster(
    raster: "rt.RasterArray",
    output_path: str | Path,
    num_cores: int = 1,
    **kwargs: Any,
) -> None:
    """Save a raster with the same parameters the population model features use."""
    save_params = {
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "ZSTD",
        "predictor": 2,  # horizontal differencing
        "num_threads": num_cores,
        "bigtiff": "yes",
        **kwargs,
    }
    touch(output_path, clobber=True)
    raster.to_file(output_path, **save_params)


def save_geo_parquet(
    gdf: gpd.GeoDataFrame,
    path: str | Path,
    *,
    write_covering_bbox: bool = True,
    **kwargs: Any,
) -> None:
    """Save a GeoDataFrame to a Parquet file."""
    path = Path(path)
    touch(path, clobber=True)
    gdf.to_parquet(
        path,
        write_covering_bbox=write_covering_bbox,
        **kwargs,
    )
