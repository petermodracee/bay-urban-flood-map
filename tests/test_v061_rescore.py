"""Fast offline unit tests for v0.6.1 Phase 2 rescoring helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon


def _load_val_module():
    path = Path(__file__).resolve().parent.parent / "pipeline" / "03_validate_sf.py"
    spec = importlib.util.spec_from_file_location("val03_v061", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_deep_and_contiguous_filter():
    mod = _load_val_module()
    depth = np.zeros((10, 10), dtype=np.float32)
    # Small isolated 2-cell puddle (2 * 4 m2 = 8 m2 < 20 m2)
    depth[1, 1:3] = 0.30
    # Large contiguous puddle (5 cells = 20 m2 >= 20 m2)
    depth[5:7, 5:8] = 0.25
    valid = np.ones((10, 10), dtype=bool)

    filtered = mod.filter_deep_and_contiguous_mask(
        depth,
        valid,
        cell_area_m2=4.0,
        depth_threshold_m=0.1524,
        min_area_m2=20.0,
    )
    assert not filtered[1, 1]
    assert not filtered[1, 2]
    assert filtered[5, 5]
    assert np.sum(filtered) == 6

    obs = np.zeros((10, 10), dtype=bool)
    obs[5:7, 5:8] = True
    raw_m = mod.compute_flood_metrics(depth, obs, valid, depth_threshold_m=0.1524)
    filt_m = mod.compute_flood_metrics_from_mask(filtered, obs, valid)
    assert raw_m["false_alarm_ratio"] > 0.0
    assert filt_m["false_alarm_ratio"] == 0.0
    assert filt_m["csi"] == 1.0


def test_benchmark_depth_diagnostics():
    mod = _load_val_module()
    depth = np.array([[0.0, 0.2], [0.4, 0.1]], dtype=np.float32)
    obs = np.array([[False, True], [True, False]])
    valid = np.ones((2, 2), dtype=bool)
    diag = mod.compute_benchmark_depth_diagnostics(depth, obs, valid)
    assert diag["inside_benchmark"]["n_cells"] == 2
    assert np.isclose(diag["inside_benchmark"]["mean_m"], 0.3)
    assert np.isclose(diag["outside_benchmark"]["mean_m"], 0.05)


def test_filter_right_of_way_lines():
    mod = _load_val_module()
    gdf = gpd.GeoDataFrame(
        {
            "highway": ["residential", None, "footway", ""],
            "railway": [None, "rail", None, "light_rail"],
        },
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 5), (10, 5)]),
            LineString([(0, 10), (10, 10)]),
            LineString([(0, 15), (10, 15)]),
        ],
        crs="EPSG:26910",
    )
    row = mod.filter_right_of_way_lines(gdf)
    assert len(row) == 2
    assert set(row["highway"]) == {"residential", "footway"}


def test_groundsource_specificity_and_confusion_matrix():
    mod = _load_val_module()
    transform = from_origin(0.0, 100.0, 10.0, 10.0)
    valid = np.ones((10, 10), dtype=bool)
    # 2x2 subbasin grid over [0, 100] x [0, 100], cell_size=50
    depth = np.zeros((10, 10), dtype=np.float32)
    # Flood top-left (TP) and top-right (FP)
    depth[0:5, 0:10] = 0.20
    gs_gdf = gpd.GeoDataFrame(
        {"area_km2": [0.001]},
        geometry=[Polygon([(5, 60), (20, 60), (20, 80), (5, 80)])],
        crs="EPSG:26910",
    )
    res = mod.evaluate_groundsource_subbasin_match(
        groundsource_gdf_utm=gs_gdf,
        modeled_depth_m=depth,
        valid_land_mask=valid,
        transform=transform,
        domain_bbox_utm=(0.0, 0.0, 100.0, 100.0),
        depth_threshold_m=0.05,
    )
    # build_subbasin_grid defaults to 2000m -> 1 subbasin; pass explicit 4 subbasins
    sb = mod.build_subbasin_grid((0.0, 0.0, 100.0, 100.0), cell_size_m=50.0)
    res4 = mod.evaluate_groundsource_subbasin_match(
        groundsource_gdf_utm=gs_gdf,
        modeled_depth_m=depth,
        valid_land_mask=valid,
        transform=transform,
        subbasins_gdf_utm=sb,
        depth_threshold_m=0.05,
    )
    assert res["n_subbasins"] == 1
    assert res4["confusion_matrix"] == {"tp": 1, "fp": 1, "fn": 0, "tn": 2}
    assert np.isclose(res4["specificity"], 2.0 / 3.0, atol=1e-4)


def test_pooled_ensemble_and_income_stratification():
    mod = _load_val_module()
    ev1 = {
        "valid_land_points": 100,
        "by_buffer": [
            {
                "radius_m": 25.0,
                "ar_roc_auc": 0.60,
                "thresholds": {
                    "0.05m": {
                        "hit_count": 60,
                        "capture_rate": 0.60,
                        "control_rate": 0.30,
                        "lift": 2.0,
                    }
                },
            }
        ],
    }
    ev2 = {
        "valid_land_points": 300,
        "by_buffer": [
            {
                "radius_m": 25.0,
                "ar_roc_auc": 0.80,
                "thresholds": {
                    "0.05m": {
                        "hit_count": 120,
                        "capture_rate": 0.40,
                        "control_rate": 0.20,
                        "lift": 2.0,
                    }
                },
            }
        ],
    }
    pooled = mod.compute_pooled_ensemble_point_metrics(
        [ev1, ev2], radii_m=(25.0,), thresholds_m=(0.05,)
    )
    assert pooled["total_points"] == 400
    buf = pooled["by_buffer"][0]
    assert np.isclose(buf["ar_roc_auc_weighted"], 0.75)
    t = buf["thresholds"]["0.05m"]
    assert t["hit_count"] == 180
    assert np.isclose(t["capture_rate"], 0.45)
    assert np.isclose(t["control_rate"], 0.225)
    assert np.isclose(t["lift"], 2.0)

    tracts = gpd.GeoDataFrame(
        {"median_income_usd": [40000.0, 90000.0, 150000.0]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
            Polygon([(20, 0), (30, 0), (30, 10), (20, 10)]),
        ],
        crs="EPSG:26910",
    )
    samples = [
        {
            "xs_pts": np.array([5.0, 15.0, 25.0]),
            "ys_pts": np.array([5.0, 5.0, 5.0]),
            "xs_ctrl": np.array([2.0, 12.0, 22.0]),
            "ys_ctrl": np.array([2.0, 2.0, 2.0]),
            "ar_pts": {25.0: np.array([0.2, 0.0, 0.3], dtype=np.float32)},
            "ar_ctrl": {25.0: np.array([0.0, 0.0, 0.1], dtype=np.float32)},
        }
    ]
    strat = mod.compute_income_stratified_point_metrics(
        samples, tracts, radii_m=(25.0,)
    )
    assert strat["n_income_tracts"] == 3
    assert len(strat["strata"]) == 3
    assert strat["strata"][0]["stratum"] == "Low Income"
    assert strat["strata"][0]["by_buffer"][0]["capture_rate"] == 1.0


def test_nfip_zero_share_and_kendall():
    mod = _load_val_module()
    df = pd.DataFrame(
        {
            "claim_count": [0, 0, 0, 1, 2, 0, 0, 3],
            "total_paid_usd": [0.0, 0.0, 0.0, 1000.0, 2000.0, 0.0, 0.0, 5000.0],
            "exposed_buildings": [0, 1, 0, 2, 3, 0, 1, 4],
            "flooded_land_area_m2": [10.0, 20.0, 5.0, 100.0, 150.0, 0.0, 30.0, 200.0],
        }
    )
    res = mod.compute_nfip_spearman_correlation(df)
    assert res["n_zero_claim_tracts"] == 5
    assert np.isclose(res["zero_claim_tract_fraction"], 5 / 8)
    assert res["kendall_tau_b"] is not None
    assert res["kendall_p_value"] is not None
    assert "auc" in res["binary_presence_mwu"]


def test_sfpuc_elevation_metrics():
    mod = _load_val_module()
    dem = np.array([[10.0, 12.0], [15.0, 20.0]], dtype=np.float32)
    valid = np.ones((2, 2), dtype=bool)
    sfpuc_wse = np.array([[10.5, 12.4], [15.0, np.nan]], dtype=np.float32)

    # 1. Perfect agreement
    perfect_depth = np.array([[0.5, 0.4], [0.0, 0.0]], dtype=np.float32)
    m_perf = mod.compute_sfpuc_elevation_metrics(perfect_depth, dem, sfpuc_wse, valid)
    assert m_perf["all_zone_cells"]["n_cells"] == 3
    assert m_perf["all_zone_cells"]["depth_rmse_m"] == 0.0
    assert m_perf["all_zone_cells"]["depth_bias_m"] == 0.0
    assert m_perf["mutually_wet_cells"]["n_cells"] == 2
    assert m_perf["mutually_wet_cells"]["wse_rmse_m"] == 0.0

    # 2. Constant +0.10 m bias on wet cells, dry on cell (1, 0)
    biased_depth = np.array([[0.6, 0.5], [0.0, 0.0]], dtype=np.float32)
    m_bias = mod.compute_sfpuc_elevation_metrics(biased_depth, dem, sfpuc_wse, valid)
    assert m_bias["mutually_wet_cells"]["n_cells"] == 2
    assert np.isclose(m_bias["mutually_wet_cells"]["wse_bias_m"], 0.10, atol=1e-4)
    assert np.isclose(m_bias["mutually_wet_cells"]["depth_mae_m"], 0.10, atol=1e-4)
    assert np.isclose(m_bias["mutually_wet_cells"]["depth_rmse_m"], 0.10, atol=1e-4)

    # 3. Empty overlap edge case
    empty_wse = np.full((2, 2), np.nan, dtype=np.float32)
    m_empty = mod.compute_sfpuc_elevation_metrics(perfect_depth, dem, empty_wse, valid)
    assert m_empty["all_zone_cells"]["n_cells"] == 0
    assert m_empty["mutually_wet_cells"]["n_cells"] == 0
    assert m_empty["mutually_wet_cells"]["wse_rmse_m"] is None
