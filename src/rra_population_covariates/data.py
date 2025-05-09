from pathlib import Path
from typing import Any

import geopandas as gpd  # type: ignore[import-untyped]
from rra_tools.shell_tools import mkdir, touch

from rra_population_covariates import constants as pcc


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
