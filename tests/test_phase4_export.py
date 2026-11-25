"""Offline unit tests for Phase 4 COG, GeoJSON benchmark, and manifest export."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import yaml
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "configs" / "sf_benchmark.yml"
EXPORT_SCRIPT_PATH = REPO_ROOT / "pipeline" / "04_export_cog.py"


def _load_export_module():
    spec = importlib.util.spec_from_file_location(
        "phase4_export_cog", EXPORT_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exp_mod = _load_export_module()


def test_mask_dry_cells() -> None:
    depth = np.array(
        [[0.0, 0.049, 0.05], [0.25, float("nan"), -9999.0]],
        dtype=np.float32,
    )
    land_mask = np.array([[True, True, True], [True, True, True]], dtype=bool)
    masked = exp_mod.mask_dry_cells(
        depth, min_depth_m=0.05, nodata_value=-9999.0, land_mask=land_mask
    )
    assert masked.dtype == np.float32
    assert float(masked[0, 0]) == -9999.0
    assert float(masked[0, 1]) == -9999.0
    assert math.isclose(float(masked[0, 2]), 0.05, abs_tol=1e-6)
    assert math.isclose(float(masked[1, 0]), 0.25, abs_tol=1e-6)
    assert float(masked[1, 1]) == -9999.0
    assert float(masked[1, 2]) == -9999.0

    # Open water cell masked even when depth >= 0.05 m
    water_mask = np.array([[True, True, False], [True, True, True]], dtype=bool)
    masked_water = exp_mod.mask_dry_cells(
        depth, min_depth_m=0.05, nodata_value=-9999.0, land_mask=water_mask
    )
    assert float(masked_water[0, 2]) == -9999.0


def test_build_manifest_schema_and_config() -> None:
    entry0 = exp_mod.build_manifest_entry(
        scenario_cfg={"id": "sf_100yr_slr0ft"},
        rain_cfg={"return_period_yr": 100},
        slr_cfg={"slr_ft": 0.0},
        cog_filename="sf_100yr_slr0ft_depth_3857.tif",
    )
    assert entry0 == {
        "id": "sf_100yr_slr0ft_depth",
        "label": "100-Yr Storm — Today (0 ft SLR)",
        "storm": "100yr",
        "slr_ft": 0,
        "metric": "depth",
        "url": "/data/sf_100yr_slr0ft_depth_3857.tif",
    }

    entry3 = exp_mod.build_manifest_entry(
        scenario_cfg={"id": "sf_100yr_slr3ft"},
        rain_cfg={"return_period_yr": 100},
        slr_cfg={"slr_ft": 3.0},
        cog_filename="sf_100yr_slr3ft_depth_3857.tif",
    )
    assert entry3["label"] == "100-Yr Storm — +3 ft SLR"
    assert entry3["slr_ft"] == 3

    manifest = exp_mod.build_manifest([entry0], version="0.1")
    assert set(manifest.keys()) == {"version", "scenarios"}
    assert manifest["version"] == "0.1"
    assert len(manifest["scenarios"]) == 1

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    exp_cfg = cfg["export"]
    assert exp_cfg["min_depth_m"] == 0.05
    assert exp_cfg["nodata_value"] == -9999.0
    assert exp_cfg["cog_blocksize"] == 256
    assert exp_cfg["manifest_file"] == "data/web_outputs/manifest.json"


def test_resolve_asset_url_prefix() -> None:
    assert exp_mod.resolve_asset_url_prefix("/data", "0.1") == "/data"
    assert exp_mod.resolve_asset_url_prefix("/data", "0.6") == "/data"
    assert exp_mod.resolve_asset_url_prefix("/data", "v0.6.1") == "/data/v0.6.1"
    assert exp_mod.resolve_asset_url_prefix("/data/v0.6.1", "v0.6.1") == "/data/v0.6.1"
    assert exp_mod.resolve_asset_url_prefix("", "v0.6.1") == "v0.6.1"
    assert exp_mod.resolve_asset_url_prefix("v0.6.1", "v0.6.1") == "v0.6.1"


def test_export_benchmark_geojson(tmp_path: Path) -> None:
    xmin, ymin, xmax, ymax = 550000.0, 4175800.0, 550100.0, 4175900.0
    poly = Polygon(
        [
            (xmin - 50.0, ymin + 10.0),
            (xmax + 50.0, ymin + 10.0),
            (xmax + 50.0, ymax - 10.0),
            (xmin - 50.0, ymax - 10.0),
            (xmin - 50.0, ymin + 10.0),
        ]
    )
    ref_path = tmp_path / "sfpuc.gpkg"
    gpd.GeoDataFrame([{"id": 1, "geometry": poly}], crs="EPSG:26910").to_crs(
        "EPSG:4326"
    ).to_file(ref_path, driver="GPKG")

    out_geojson = tmp_path / "web" / "sf_sfpuc_100yr_benchmark.geojson"
    exp_mod.export_benchmark_geojson(
        validation_ref_path=ref_path,
        bbox_utm=[xmin, ymin, xmax, ymax],
        project_crs="EPSG:26910",
        output_path=out_geojson,
        simplify_tolerance_m=1.0,
    )
    assert out_geojson.exists()
    assert out_geojson.stat().st_size < 2 * 1024 * 1024
    loaded = gpd.read_file(out_geojson)
    assert loaded.crs == CRS.from_epsg(4326)
    assert len(loaded) == 1


def test_export_cog_end_to_end_synthetic_and_crs_check(tmp_path: Path) -> None:
    # Use 320x320 grid (> 256 blocksize) so gdal_translate -of COG generates internal overviews.
    xmin, ymin, xmax, ymax = 550000.0, 4175800.0, 550320.0, 4176120.0
    tf = from_origin(xmin, ymax, 1.0, 1.0)

    dem_path = tmp_path / "hydro_dem.tif"
    dem = np.full((320, 320), 10.0, dtype=np.float32)
    dem[0:20, 0:20] = 0.0  # Open water (< 0.5 m) to test masking
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=320,
        width=320,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(dem, 1)

    depth_path = tmp_path / "depth_1m.tif"
    depth = np.full((320, 320), 0.01, dtype=np.float32)  # Trace < 0.05 m
    depth[100:220, 100:220] = 0.45  # Flooded zone
    depth[0:20, 0:20] = 1.50  # Open water depth should be masked out
    with rasterio.open(
        depth_path,
        "w",
        driver="GTiff",
        height=320,
        width=320,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(depth, 1)

    ref_path = tmp_path / "sfpuc.gpkg"
    poly = Polygon(
        [
            (xmin + 100.0, ymin + 100.0),
            (xmin + 220.0, ymin + 100.0),
            (xmin + 220.0, ymin + 220.0),
            (xmin + 100.0, ymin + 220.0),
            (xmin + 100.0, ymin + 100.0),
        ]
    )
    gpd.GeoDataFrame([{"id": 1, "geometry": poly}], crs="EPSG:26910").to_crs(
        "EPSG:4326"
    ).to_file(ref_path, driver="GPKG")

    web_dir = tmp_path / "web_outputs"
    cfg = {
        "project": {
            "name": "synth",
            "baseline_scenario": "sf_100yr_slr0ft",
            "crs": "EPSG:26910",
            "output_crs": "EPSG:3857",
        },
        "domain": {
            "bbox_epsg26910": [xmin, ymin, xmax, ymax],
            "grid_resolution_m": 10.0,
            "validation_reference": str(ref_path),
        },
        "terrain_prep": {"hydro_dem_file": str(dem_path)},
        "validation": {
            "min_land_elevation_m": 0.5,
            "downscaled_depth_file": str(depth_path),
        },
        "rain_events": {"rain_100yr": {"return_period_yr": 100}},
        "slr_increments": {"slr_0ft": {"slr_ft": 0.0}},
        "scenarios": [
            {"id": "sf_100yr_slr0ft", "rain": "rain_100yr", "slr": "slr_0ft"}
        ],
        "paths": {"runs_dir": str(tmp_path / "runs"), "web_output_dir": str(web_dir)},
        "export": {
            "min_depth_m": 0.05,
            "nodata_value": -9999.0,
            "mask_open_water": True,
            "cog_blocksize": 256,
            "benchmark_geojson_file": str(web_dir / "sf_sfpuc_100yr_benchmark.geojson"),
            "benchmark_simplify_tolerance_m": 1.0,
            "manifest_file": str(web_dir / "manifest.json"),
            "manifest_version": "0.1",
            "web_data_url_prefix": "/data",
        },
    }
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    out_cog = exp_mod.export_cog(cfg_path, scenario_id="sf_100yr_slr0ft")
    assert out_cog == web_dir / "sf_100yr_slr0ft_depth_3857.tif"
    assert out_cog.exists()

    with rasterio.open(out_cog) as ds:
        assert ds.crs == CRS.from_epsg(3857)
        assert ds.dtypes[0] == "float32"
        assert ds.nodata == -9999.0
        assert len(ds.overviews(1)) > 0

    info_text = subprocess.check_output(["gdalinfo", str(out_cog)], text=True)
    assert "LAYOUT=COG" in info_text

    manifest = json.loads((web_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.1"
    assert len(manifest["scenarios"]) == 1
    assert manifest["scenarios"][0]["url"] == "/data/sf_100yr_slr0ft_depth_3857.tif"

    # Reject non-EPSG:26910 input array
    with pytest.raises(ValueError):
        exp_mod.write_epsg3857_cog(
            masked_depth_m=np.zeros((10, 10), dtype=np.float32),
            src_transform=tf,
            src_crs="EPSG:4326",
            output_cog_path=tmp_path / "bad.tif",
        )
