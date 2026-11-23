"""Unit tests for v0.4 Phase 4 SFINCS quadtree refinement and UGRID downscaling."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from affine import Affine

_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BUILD = _load_module("build_sfincs", "pipeline/02_build_sfincs.py")
_VAL = _load_module("validate_sf", "pipeline/03_validate_sf.py")


def test_build_quadtree_refinement_polygons() -> None:
    dem = np.full((20, 20), 40.0, dtype=np.float32)
    dem[2:12, 2:12] = 5.0  # 100 cells * (20m * 20m) = 40,000 m^2 low-lying zone
    dem[18, 18] = 2.0  # 1 isolated cell = 400 m^2 (< min_area_m2)
    transform = Affine.translation(550000.0, 4180000.0) * Affine.scale(20.0, -20.0)

    gdf = _BUILD.build_quadtree_refinement_polygons(
        dem_m=dem,
        transform=transform,
        project_crs="EPSG:26910",
        zmax_m=15.0,
        nodata_value=-9999.0,
        min_area_m2=10000.0,
        refinement_level=2,
    )
    assert len(gdf) == 1
    assert gdf.crs.to_string() == "EPSG:26910"
    assert int(gdf["refinement_level"].iloc[0]) == 2
    assert gdf.geometry.area.iloc[0] == pytest.approx(40000.0)

    with pytest.raises(ValueError, match="EPSG:26910"):
        _BUILD.build_quadtree_refinement_polygons(
            dem_m=dem,
            transform=transform,
            project_crs="EPSG:4326",
        )


def test_downscale_quadtree_zsmax_to_grid() -> None:
    dem = np.array([[1.0, 2.0], [3.0, -9999.0]], dtype=np.float32)
    transform = Affine.translation(550000.0, 4180004.0) * Affine.scale(2.0, -2.0)
    face_x = np.array([550001.0, 550003.0], dtype=np.float64)
    face_y = np.array([4180003.0, 4180001.0], dtype=np.float64)
    face_zsmax = np.array([1.5, 2.5], dtype=np.float32)

    depth = _VAL.downscale_quadtree_zsmax_to_grid(
        face_x=face_x,
        face_y=face_y,
        face_zsmax=face_zsmax,
        dem_m=dem,
        dem_transform=transform,
        dem_nodata=-9999.0,
        max_dist_m=10.0,
    )
    assert depth[0, 0] == pytest.approx(0.5)
    assert depth[0, 1] == pytest.approx(0.0)  # max(0, 1.5 - 2.0)
    assert depth[1, 0] == pytest.approx(0.0)  # max(0, 2.5 - 3.0)
    assert depth[1, 1] == pytest.approx(-9999.0)  # nodata preserved
