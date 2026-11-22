"""Offline unit tests for v0.2 Phase 3 dual citywide validation and 311 comparison."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.transform import from_origin
from shapely.geometry import Point

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATE_SCRIPT_PATH = REPO_ROOT / "pipeline" / "03_validate_sf.py"


def _load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "phase3_dual_validate", VALIDATE_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


val_mod = _load_validate_module()


def test_sample_buffers_and_roc_auc() -> None:
    transform = from_origin(1000.0, 2040.0, 2.0, 2.0)  # 20x20 grid, 2 m resolution
    depth = np.zeros((20, 20), dtype=np.float32)
    # Row 10, col 10 -> x = 1021, y = 2019; offset pixel (10, 18) is 16 m east -> outside 0m, inside 25m
    depth[10, 18] = 0.25
    # Offset pixel (0, 10) is 20 m north -> inside 25m; (10, 2) at x=1005 -> wait: dist from col 10 = 16m; put deep pond 35m away? Grid is 40x40m, col 0 is x=1001 -> dist from x=1039 (col 19)
    mask = np.zeros((20, 20), dtype=bool)
    mask[10, 18] = True

    xs = np.array([1021.0])
    ys = np.array([2019.0])
    sampled = val_mod.sample_max_depth_within_buffers(
        depth_m=depth,
        transform=transform,
        nodata_value=-9999.0,
        xs_m=xs,
        ys_m=ys,
        radii_m=(0.0, 10.0, 25.0),
    )
    assert math.isclose(float(sampled[0.0][0]), 0.0, abs_tol=1e-6)
    assert math.isclose(float(sampled[10.0][0]), 0.0, abs_tol=1e-6)
    assert math.isclose(float(sampled[25.0][0]), 0.25, abs_tol=1e-5)

    mask_sampled = val_mod.sample_mask_within_buffers(
        mask_bool=mask,
        transform=transform,
        xs_m=xs,
        ys_m=ys,
        radii_m=(0.0, 25.0),
    )
    assert bool(mask_sampled[0.0][0]) is False
    assert bool(mask_sampled[25.0][0]) is True

    assert val_mod.compute_roc_auc([0.5, 0.8], [0.0, 0.1]) == 1.0
    assert val_mod.compute_roc_auc([0.0, 0.0], [0.0, 0.0]) == 0.5
    assert val_mod.compute_roc_auc([0.0, 0.1], [0.5, 0.8]) == 0.0


def test_compute_311_validation_neighborhood_and_markdown() -> None:
    transform = from_origin(0.0, 200.0, 2.0, 2.0)  # 100x100 grid = 200m x 200m
    valid_land = np.ones((100, 100), dtype=bool)
    ar_depth = np.zeros((100, 100), dtype=np.float32)
    sfpuc_mask = np.zeros((100, 100), dtype=bool)

    # Plant two distinct flood zones
    ar_depth[10:20, 10:20] = 0.20
    sfpuc_mask[10:20, 10:20] = True
    ar_depth[50:60, 50:60] = 0.08  # AR >= 0.05m only, below 0.1524m and outside SFPUC

    # 3 synthetic 311 points: 2 captured by AR, 1 missed in Mission
    pts_gdf = gpd.GeoDataFrame(
        {
            "service_subtype": ["Flooding", "Sewage_back_up", "Flooding"],
            "neighborhood": ["SOMA", "SOMA", "Mission"],
            "address": ["100 Main St", "102 Main St", "500 Valencia St"],
        },
        geometry=[Point(30.0, 170.0), Point(110.0, 90.0), Point(180.0, 20.0)],
        crs="EPSG:26910",
    )

    metrics = val_mod.compute_311_validation_metrics(
        points_gdf_utm=pts_gdf,
        ar_depth_m=ar_depth,
        sfpuc_mask=sfpuc_mask,
        valid_land_mask=valid_land,
        transform=transform,
        nodata_value=-9999.0,
        radii_m=(0.0, 25.0, 50.0),
        n_controls=200,
        seed=7,
    )
    assert metrics["valid_land_points"] == 3
    b0 = next(b for b in metrics["by_buffer"] if b["radius_m"] == 0.0)
    assert b0["thresholds"]["0.05m"]["hit_count"] == 2
    assert b0["thresholds"]["0.1524m"]["hit_count"] == 1
    assert b0["sfpuc"]["hit_count"] == 1
    assert b0["ar_roc_auc"] > 0.7
    assert metrics["top_missed_clusters"][0]["neighborhood"] == "Mission"
    assert metrics["top_missed_clusters"][0]["missed_count"] == 1

    nb_rows = val_mod.compute_neighborhood_sfpuc_metrics(
        depth_m=ar_depth,
        obs_mask=sfpuc_mask,
        valid_land_mask=valid_land,
        transform=transform,
        depth_threshold_m=0.05,
        neighborhoods_utm={"ZoneA": (0.0, 140.0, 60.0, 200.0)},
    )
    assert len(nb_rows) == 1
    assert math.isclose(nb_rows[0]["hit_rate"], 1.0, abs_tol=1e-6)

    md = val_mod.render_v02_validation_markdown(
        sfpuc_summary={
            "region": "sf_test",
            "drainage_rate_mm_hr": 5.0,
            "manning_n_paved": 0.03,
            "depth_threshold_m": 0.1524,
            "metrics": {"hit_rate": 0.75, "false_alarm_ratio": 0.25, "csi": 0.6},
        },
        neighborhood_rows=nb_rows,
        fp_clusters=[{"area_m2": 400.0, "centroid_x_m": 50.0, "centroid_y_m": 50.0}],
        val_311_metrics=metrics,
    )
    assert "# San Francisco Citywide v0.2 Dual Validation Report" in md
    assert "311 Subtype Breakdown" in md
    assert "ZoneA" in md
