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
        return self._root / "open_building_map" / pcc.OBM_VERSION

    def open_building_map_raster_path(
        self,
        resolution: str,
        block_key: str,
        parent_building_type: str,
    ) -> Path:
        return (
            self.open_building_map
            / f"{resolution}m"
            / block_key
            / f"{parent_building_type}.tif"
        )

    def save_open_building_map_raster(
        self,
        raster: "rt.RasterArray",
        resolution: str,
        block_key: str,
        parent_building_type: str,
    ) -> None:
        path = self.open_building_map_raster_path(
            resolution, block_key, parent_building_type
        )
        mkdir(path.parent, exist_ok=True, parents=True)
        save_raster(raster, path)

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
