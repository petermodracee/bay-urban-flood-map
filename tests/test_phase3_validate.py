"""Offline unit tests for Phase 3 validation, downscaling, and parameter sweep helpers."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import xarray as xr
import yaml
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "configs" / "sf_benchmark.yml"
VALIDATE_SCRIPT_PATH = REPO_ROOT / "pipeline" / "03_validate_sf.py"


def _load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "phase3_validate_sf", VALIDATE_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


val_mod = _load_validate_module()


def test_downscale_zsmax_to_depth_m_orientation_and_clamping() -> None:
    # 2x2 coarse 10m grid over a 20x20m 1m DEM.
    # SFINCS south-up row 0 (south half) has water at 11.0 m; row 1 (north half) has 9.0 m.
    xmin, ymax = 550000.0, 4175820.0
    src_tf = from_origin(xmin, ymax, 10.0, 10.0)
    dst_tf = from_origin(xmin, ymax, 1.0, 1.0)

    zsmax_south_up = np.array([[11.0, 11.0], [9.0, 9.0]], dtype=np.float32)
    dem = np.full((20, 20), 10.0, dtype=np.float32)
    dem[0, 0] = -9999.0  # Preserve DEM NoData

    depth = val_mod.downscale_zsmax_to_depth_m(
        zsmax_south_up=zsmax_south_up,
        src_transform=src_tf,
        src_crs="EPSG:26910",
        dem_m=dem,
        dst_transform=dst_tf,
        dst_crs="EPSG:26910",
        dem_nodata=-9999.0,
    )
    assert depth.shape == (20, 20)
    assert float(depth[0, 0]) == -9999.0
    # North half (row 2) has zsmax=9.0 < dem=10.0 -> clamped to 0.0 m
    assert math.isclose(float(depth[2, 10]), 0.0, abs_tol=1e-5)
    # South half (row 16) has zsmax=11.0 > dem=10.0 -> 1.0 m depth
    assert math.isclose(float(depth[16, 10]), 1.0, abs_tol=1e-5)

    with pytest.raises(ValueError):
        val_mod.downscale_zsmax_to_depth_m(
            zsmax_south_up, src_tf, "EPSG:3857", dem, dst_tf, "EPSG:26910"
        )


def test_compute_flood_metrics_and_clusters() -> None:
    depth = np.zeros((20, 20), dtype=np.float32)
    obs = np.zeros((20, 20), dtype=bool)
    valid = np.ones((20, 20), dtype=bool)

    # TP=2x2 (4 px), FN=2x2 (4 px), FP=3x3 (9 px)
    obs[0:2, 0:2] = True
    obs[0:2, 2:4] = True
    depth[0:2, 0:2] = 0.25
    depth[10:13, 10:13] = 0.50

    m = val_mod.compute_flood_metrics(depth, obs, valid, depth_threshold_m=0.1524)
    assert m["tp"] == 4
    assert m["fp"] == 9
    assert m["fn"] == 4
    assert math.isclose(float(m["hit_rate"]), 4 / 8, rel_tol=1e-5)
    assert math.isclose(float(m["false_alarm_ratio"]), 9 / 13, rel_tol=1e-5)
    assert math.isclose(float(m["csi"]), 4 / 17, rel_tol=1e-5)

    # All-zero mask edge case should not divide by zero
    zero_m = val_mod.compute_flood_metrics(depth, obs, np.zeros((20, 20), dtype=bool))
    assert zero_m["hit_rate"] == 0.0
    assert zero_m["false_alarm_ratio"] == 0.0
    assert zero_m["csi"] == 0.0

    tf = from_origin(550000.0, 4175820.0, 1.0, 1.0)
    clusters = val_mod.find_top_false_positive_clusters(
        depth_m=depth,
        obs_mask=obs,
        valid_mask=valid,
        dst_transform=tf,
        depth_threshold_m=0.1524,
        downsample_factor=1,
        top_n=5,
    )
    assert len(clusters) == 1
    assert clusters[0]["area_m2"] == 9.0
    assert math.isclose(clusters[0]["centroid_x_m"], 550011.5, abs_tol=1e-4)
    assert math.isclose(clusters[0]["centroid_y_m"], 4175808.5, abs_tol=1e-4)


def test_build_validation_summary_contract_and_config() -> None:
    summary = val_mod.build_validation_summary(
        region="sf_mission_alemany",
        drainage_rate_mm_hr=15.0,
        manning_n_paved=0.020,
        depth_threshold_m=0.1524,
        metrics={"hit_rate": 0.25, "false_alarm_ratio": 0.5, "csi": 0.2},
    )
    assert set(summary.keys()) == {
        "region",
        "drainage_rate_mm_hr",
        "manning_n_paved",
        "depth_threshold_m",
        "metrics",
    }
    assert set(summary["metrics"].keys()) == {"hit_rate", "false_alarm_ratio", "csi"}

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    val_cfg = cfg["validation"]
    assert val_cfg["depth_threshold_m"] == 0.1524
    assert val_cfg["summary_file"] == "data/processed/validation_summary.json"
    assert len(val_cfg["sweep_drainage_rates_mm_hr"]) >= 2
    assert len(val_cfg["sweep_manning_n_paved"]) >= 2


def test_validate_sf_end_to_end_synthetic(tmp_path: Path) -> None:
    xmin, ymin, xmax, ymax = 550000.0, 4175800.0, 550020.0, 4175820.0
    tf_1m = from_origin(xmin, ymax, 1.0, 1.0)

    dem_path = tmp_path / "hydro_dem.tif"
    dem = np.full((20, 20), 10.0, dtype=np.float32)
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=20,
        width=20,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=tf_1m,
        nodata=-9999.0,
    ) as dst:
        dst.write(dem, 1)

    sfpuc_path = tmp_path / "sfpuc.gpkg"
    # South half [ymin, ymin+10] is observed flood zone (in EPSG:4326 like raw DataSF)
    obs_poly = Polygon(
        [
            (xmin, ymin),
            (xmax, ymin),
            (xmax, ymin + 10.0),
            (xmin, ymin + 10.0),
            (xmin, ymin),
        ]
    )
    gpd.GeoDataFrame([{"id": 1, "geometry": obs_poly}], crs="EPSG:26910").to_crs(
        "EPSG:4326"
    ).to_file(sfpuc_path, driver="GPKG")

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "sf_100yr_slr0ft"
    run_dir.mkdir(parents=True)
    # South row flooded to 10.5 m (depth 0.5 m), north row dry at 9.8 m
    zsmax = np.array([[[10.5, 10.5], [9.8, 9.8]]], dtype=np.float32)
    ds = xr.Dataset(
        {"zsmax": (("timemax", "n", "m"), zsmax)},
        coords={"timemax": [0], "n": [0, 1], "m": [0, 1]},
    )
    ds.to_netcdf(run_dir / "sfincs_map.nc")

    out_depth = tmp_path / "processed" / "depth_1m.tif"
    out_summary = tmp_path / "processed" / "validation_summary.json"
    cfg = {
        "project": {
            "name": "synth",
            "region_id": "sf_mission_alemany",
            "baseline_scenario": "sf_100yr_slr0ft",
            "crs": "EPSG:26910",
        },
        "domain": {
            "bbox_epsg26910": [xmin, ymin, xmax, ymax],
            "grid_resolution_m": 10.0,
            "validation_reference": str(sfpuc_path),
        },
        "terrain_prep": {
            "hydro_dem_file": str(dem_path),
            "manning_n_paved": 0.020,
        },
        "sfincs_model": {"drainage_rate_mm_hr": 15.0},
        "paths": {"runs_dir": str(runs_dir)},
        "validation": {
            "depth_threshold_m": 0.1524,
            "min_land_elevation_m": 0.5,
            "downscaled_depth_file": str(out_depth),
            "summary_file": str(out_summary),
            "sweep_drainage_rates_mm_hr": [15.0],
            "sweep_manning_n_paved": [0.020],
        },
    }
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    summary = val_mod.validate_sf(cfg_path, sweep=False)
    assert out_depth.exists()
    assert out_summary.exists()
    written = json.loads(out_summary.read_text(encoding="utf-8"))
    assert written == summary
    assert summary["metrics"]["hit_rate"] > 0.7
    assert summary["metrics"]["csi"] > 0.7

    with rasterio.open(out_depth) as ds_out:
        assert ds_out.crs == CRS.from_epsg(26910)
        assert ds_out.shape == (20, 20)
