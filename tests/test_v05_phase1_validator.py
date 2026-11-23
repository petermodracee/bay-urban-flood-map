"""Offline unit tests for v0.5 Phase 1 region-agnostic validator refactor & JSON contract."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_validate_module():
    script_path = REPO_ROOT / "pipeline" / "03_validate_sf.py"
    spec = importlib.util.spec_from_file_location("v05_phase1_validate", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VAL = _load_validate_module()


def test_compute_land_valid_mask_and_rasterize_benchmark() -> None:
    dem_m = np.array(
        [
            [-9999.0, 0.2, 2.0],
            [1.0, 3.0, 5.0],
            [0.5, 4.0, np.nan],
        ],
        dtype=np.float32,
    )
    nlcd = np.array(
        [
            [21, 21, 11],
            [24, 24, 24],
            [24, 11, 24],
        ],
        dtype=np.uint8,
    )
    mask = _VAL.compute_land_valid_mask(
        dem_m=dem_m,
        dem_nodata=-9999.0,
        min_land_elevation_m=0.5,
        nlcd_landcover=nlcd,
        exclude_open_water=True,
    )
    expected = np.array(
        [
            [False, False, False],
            [True, True, True],
            [True, False, False],
        ],
        dtype=bool,
    )
    np.testing.assert_array_equal(mask, expected)

    # Without open water exclusion, NLCD==11 cells >= 0.5m remain valid
    mask_with_water = _VAL.compute_land_valid_mask(
        dem_m=dem_m,
        dem_nodata=-9999.0,
        min_land_elevation_m=0.5,
        nlcd_landcover=nlcd,
        exclude_open_water=False,
    )
    assert bool(mask_with_water[0, 2]) is True
    assert bool(mask_with_water[2, 1]) is True

    tf = from_origin(560000.0, 4150030.0, 10.0, 10.0)
    poly = Polygon(
        [
            (560010.0, 4150010.0),
            (560020.0, 4150010.0),
            (560020.0, 4150020.0),
            (560010.0, 4150020.0),
            (560010.0, 4150010.0),
        ]
    )
    gdf = gpd.GeoDataFrame([{"id": 1, "geometry": poly}], crs="EPSG:26910")
    bench = _VAL.rasterize_benchmark_polygons(gdf, out_shape=(3, 3), transform=tf)
    assert bench.dtype == bool
    assert bool(bench[1, 1]) is True
    assert int(np.sum(bench)) == 1

    empty_gdf = gpd.GeoDataFrame({"id": []}, geometry=[], crs="EPSG:26910")
    empty_bench = _VAL.rasterize_benchmark_polygons(
        empty_gdf, out_shape=(3, 3), transform=tf
    )
    assert not np.any(empty_bench)


def test_corridor_benchmark_metrics_region_agnostic() -> None:
    tf = from_origin(570000.0, 4145100.0, 2.0, 2.0)  # 50x50 grid -> 100m x 100m
    valid = np.ones((50, 50), dtype=bool)
    obs = np.zeros((50, 50), dtype=bool)
    depth = np.zeros((50, 50), dtype=np.float32)

    obs[5:15, 5:15] = True
    depth[5:15, 5:15] = 0.25  # Perfect match in East Palo Alto corridor

    corridors = {"EPA_Creek": (570000.0, 4145050.0, 570050.0, 4145100.0)}
    rows = _VAL.compute_corridor_benchmark_metrics(
        depth_m=depth,
        obs_mask=obs,
        valid_land_mask=valid,
        transform=tf,
        depth_threshold_m=0.1524,
        corridors_utm=corridors,
    )
    assert len(rows) == 1
    assert rows[0]["corridor"] == "EPA_Creek"
    assert math.isclose(rows[0]["csi"], 1.0, abs_tol=1e-6)


def test_point_capture_metrics_without_benchmark_mask() -> None:
    tf = from_origin(570000.0, 4145200.0, 2.0, 2.0)  # 100x100 grid = 200m x 200m
    valid_land = np.ones((100, 100), dtype=bool)
    valid_land[:, :10] = False  # Bay water strip on west edge

    depth_m = np.zeros((100, 100), dtype=np.float32)
    depth_m[20:30, 30:40] = 0.30  # Flooded pocket

    pts_gdf = gpd.GeoDataFrame(
        {
            "category": ["Roadway", "Parcel", "Parcel"],
            "zone": ["Creek_North", "Creek_South", "Bay_Water"],
            "location": ["10 Bay Rd", "20 Clarke Ave", "Offshore"],
        },
        geometry=[
            Point(570070.0, 4145150.0),  # Inside flooded pocket
            Point(570150.0, 4145050.0),  # Dry land
            Point(570005.0, 4145150.0),  # Outside valid land -> dropped
        ],
        crs="EPSG:26910",
    )

    filtered, xs, ys = _VAL.extract_valid_land_points(pts_gdf, valid_land, tf)
    assert len(filtered) == 2
    assert xs.size == 2 and ys.size == 2

    xs_c, ys_c = _VAL.sample_random_land_controls(
        valid_land, tf, n_controls=100, seed=9
    )
    assert xs_c.size == 100 and ys_c.size == 100

    res = _VAL.compute_point_capture_metrics(
        points_gdf_utm=pts_gdf,
        ar_depth_m=depth_m,
        valid_land_mask=valid_land,
        transform=tf,
        benchmark_mask=None,
        radii_m=(0.0, 25.0),
        n_controls=100,
        seed=9,
        subtype_col="category",
        group_col="zone",
        address_col="location",
    )
    assert res["total_raw_points"] == 3
    assert res["valid_land_points"] == 2
    assert res["dropped_outside_land"] == 1
    b0 = next(b for b in res["by_buffer"] if b["radius_m"] == 0.0)
    assert "sfpuc" not in b0
    assert b0["thresholds"]["0.05m"]["hit_count"] == 1
    assert b0["thresholds"]["0.05m"]["capture_rate"] == pytest.approx(0.5)


def test_additive_ensemble_metrics_json_contract() -> None:
    events = [
        {
            "id": "ar_20221231",
            "label": "Dec 31, 2022 Atmospheric River",
            "scenario": "sf_citywide_ar_20221231",
            "valid_land_points": 424,
            "by_buffer": [
                {
                    "radius_m": 25.0,
                    "ar_roc_auc": 0.5888,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.3695,
                            "control_rate": 0.2954,
                            "lift": 1.251,
                        }
                    },
                }
            ],
        }
    ]
    bench = _VAL.build_design_benchmark_summary(
        source="sfpuc_100yr",
        depth_threshold_m=0.1524,
        metrics={"hit_rate": 0.3887, "false_alarm_ratio": 0.7086, "csi": 0.1998},
    )
    agg = _VAL.aggregate_ensemble_311_metrics(
        events,
        region="sf_citywide",
        drainage_tier="tier0",
        rain_source="mrms",
        design_benchmark=bench,
    )

    # Preserved v0.4 keys
    assert agg["n_events"] == 1
    assert agg["total_311_points"] == 424
    assert agg["ensemble_mean"]["by_buffer"][0]["ar_roc_auc"] == pytest.approx(0.5888)
    assert agg["ensemble_mean"]["by_buffer"][0]["thresholds"]["0.05m"][
        "capture_rate"
    ] == pytest.approx(0.3695)
    assert agg["ensemble_mean"]["by_buffer"][0]["thresholds"]["0.05m"][
        "lift"
    ] == pytest.approx(1.251)

    # Additive v0.5 keys
    assert agg["region"] == "sf_citywide"
    assert agg["drainage_tier"] == "tier0"
    assert agg["rain_source"] == "mrms"
    assert agg["design_benchmark"]["source"] == "sfpuc_100yr"
    assert agg["design_benchmark"]["csi"] == pytest.approx(0.1998)
    assert agg["events"][0]["tiers_evaluated"] == ["311"]
    assert agg["events"][0]["road_closures_validation"] is None
    assert agg["events"][0]["gauge_hwm_validation"] is None
    assert agg["regional_crosschecks"] == {
        "groundsource_subbasin_match": None,
        "nfip_tract_spearman_rho": None,
    }
