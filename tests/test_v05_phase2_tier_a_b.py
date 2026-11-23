"""Offline unit tests for v0.5 Phase 2 Tier A (gauges/HWM) and Tier B (pluggable benchmarks)."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from rasterio.transform import from_origin
from shapely.geometry import Polygon

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_validate_module():
    script_path = REPO_ROOT / "pipeline" / "03_validate_sf.py"
    spec = importlib.util.spec_from_file_location("v05_phase2_validate", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VAL = _load_validate_module()


def test_convert_elevation_to_navd88_m() -> None:
    assert math.isclose(
        _VAL.convert_elevation_to_navd88_m(2.5, vertical_datum="NAVD88", unit="m"),
        2.5,
        rel_tol=1e-9,
    )
    assert math.isclose(
        _VAL.convert_elevation_to_navd88_m(10.0, vertical_datum="NAVD88", unit="ft"),
        3.048,
        rel_tol=1e-9,
    )
    # NGVD29 feet -> NAVD88 meters with explicit Bay Area offset (+0.823 m)
    conv = _VAL.convert_elevation_to_navd88_m(
        10.0,
        vertical_datum="NGVD29",
        unit="ft",
        offset_to_navd88_m=0.823,
    )
    assert math.isclose(conv, 3.871, rel_tol=1e-9)

    arr = np.array([1.0, 2.0], dtype=np.float64)
    out = _VAL.convert_elevation_to_navd88_m(
        arr, vertical_datum="MLLW", unit="m", offset_to_navd88_m=-0.05
    )
    np.testing.assert_allclose(out, np.array([0.95, 1.95]))

    with pytest.raises(ValueError, match="requires explicit offset_to_navd88_m"):
        _VAL.convert_elevation_to_navd88_m(5.0, vertical_datum="NGVD29", unit="ft")

    with pytest.raises(ValueError, match="Unsupported vertical datum"):
        _VAL.convert_elevation_to_navd88_m(5.0, vertical_datum="WGS84", unit="m")


def test_compute_gauge_timeseries_metrics() -> None:
    t_min = np.array([0.0, 60.0, 120.0, 180.0, 240.0])
    obs_m = np.array([1.0, 1.5, 2.5, 1.8, 1.2])
    # Cold-start t=0, plus constant +0.1 m bias and 60 min peak lag at t=180
    mod_m = np.array([0.0, 1.6, 2.4, 2.6, 1.3])

    metrics = _VAL.compute_gauge_timeseries_metrics(
        time_min=t_min,
        obs_wse_navd88_m=obs_m,
        mod_wse_navd88_m=mod_m,
        skip_initial_steps=1,
    )
    assert metrics["n_samples"] == 4
    assert math.isclose(metrics["obs_peak_m"], 2.5, abs_tol=1e-4)
    assert math.isclose(metrics["mod_peak_m"], 2.6, abs_tol=1e-4)
    assert math.isclose(metrics["peak_error_m"], 0.1, abs_tol=1e-4)
    assert math.isclose(metrics["obs_peak_time_min"], 120.0, abs_tol=1e-1)
    assert math.isclose(metrics["mod_peak_time_min"], 180.0, abs_tol=1e-1)
    assert math.isclose(metrics["peak_timing_error_min"], 60.0, abs_tol=1e-1)
    # Errors on [60..240]: [+0.1, -0.1, +0.8, +0.1] -> bias = +0.225
    assert math.isclose(metrics["bias_m"], 0.225, abs_tol=1e-4)
    assert math.isclose(metrics["mae_m"], 0.275, abs_tol=1e-4)
    assert metrics["rmse_m"] > 0.4

    empty = _VAL.compute_gauge_timeseries_metrics(
        time_min=[0.0], obs_wse_navd88_m=[np.nan], mod_wse_navd88_m=[1.0]
    )
    assert empty["n_samples"] == 0
    assert empty["rmse_m"] is None


def test_parse_usgs_nwis_iv_json_and_stn_hwm_scoring() -> None:
    # Synthetic USGS NWIS IV JSON payload for 11164500 (San Francisquito Creek)
    nwis_payload = {
        "value": {
            "timeSeries": [
                {
                    "sourceInfo": {
                        "siteName": "SAN FRANCISQUITO C A STANFORD UNIVERSITY CA",
                        "siteCode": [{"value": "11164500"}],
                        "geoLocation": {
                            "geogLocation": {
                                "latitude": 37.4233,
                                "longitude": -122.1886,
                            }
                        },
                    },
                    "variable": {"variableCode": [{"value": "00065"}]},
                    "values": [
                        {
                            "value": [
                                {
                                    "dateTime": "2022-12-31T10:00:00.000-08:00",
                                    "value": "10.0",
                                },
                                {
                                    "dateTime": "2022-12-31T11:00:00.000-08:00",
                                    "value": "12.0",
                                },
                                {
                                    "dateTime": "2022-12-31T12:00:00.000-08:00",
                                    "value": "-999999",
                                },
                            ]
                        }
                    ],
                }
            ]
        }
    }
    parsed = _VAL.parse_usgs_nwis_iv_json(
        nwis_payload,
        parameter_cd="00065",
        vertical_datum="NGVD29",
        unit="ft",
        offset_to_navd88_m=0.823,
    )
    assert parsed["station_id"] == "11164500"
    assert parsed["vertical_datum"] == "NAVD88"
    assert len(parsed["wse_navd88_m"]) == 2
    assert math.isclose(parsed["wse_navd88_m"][0], 10.0 * 0.3048 + 0.823, abs_tol=1e-4)
    assert math.isclose(parsed["wse_navd88_m"][1], 12.0 * 0.3048 + 0.823, abs_tol=1e-4)

    # Synthetic USGS STN HWM records (one NAVD88 m, one NGVD29 ft)
    hwm_records = [
        {
            "hwm_id": "HWM-1",
            "latitude": 37.45,
            "longitude": -122.14,
            "wse_m": 4.0,
            "vertical_datum": "NAVD88",
        },
        {
            "hwm_id": "HWM-2",
            "latitude": 37.4501,
            "longitude": -122.1399,
            "elev_ft": 10.0,
            "vertical_datum": "NGVD29",
            "offset_to_navd88_m": 0.823,
        },
    ]
    hwm_gdf = _VAL.parse_usgs_stn_hwm_records(hwm_records, target_crs="EPSG:26910")
    assert str(hwm_gdf.crs) == "EPSG:26910"
    assert len(hwm_gdf) == 2

    # Build a synthetic zsmax raster covering both projected HWM points
    xs = hwm_gdf["x_epsg26910"].to_numpy()
    ys = hwm_gdf["y_epsg26910"].to_numpy()
    tf = from_origin(float(xs.min()) - 10.0, float(ys.max()) + 10.0, 2.0, 2.0)
    zsmax_m = np.full((30, 30), 4.0, dtype=np.float32)

    pts_xy = list(zip(xs, ys, strict=True))
    mod_wse = _VAL.sample_raster_at_points(zsmax_m, pts_xy, tf)
    hwm_metrics = _VAL.compute_hwm_metrics(
        obs_wse_navd88_m=hwm_gdf["wse_navd88_m"].to_numpy(),
        mod_wse_navd88_m=mod_wse,
    )
    assert hwm_metrics["n_hwm"] == 2
    # HWM-1: obs=4.0, mod=4.0 (err=0.0); HWM-2: obs=3.871, mod=4.0 (err=+0.129)
    assert math.isclose(hwm_metrics["bias_m"], 0.0645, abs_tol=1e-4)
    assert math.isclose(hwm_metrics["mae_m"], 0.0645, abs_tol=1e-4)
    assert math.isclose(hwm_metrics["max_abs_error_m"], 0.129, abs_tol=1e-4)


def test_evaluate_event_gauges_synthetic_netcdf_and_csv(tmp_path: Path) -> None:
    # Synthetic SFINCS map.nc with 4x4 grid, 4 hourly steps (t=0 cold-start zs=0)
    x_vals = np.linspace(547000.0, 547060.0, 4)
    y_vals = np.linspace(4184440.0, 4184500.0, 4)
    xx, yy = np.meshgrid(x_vals, y_vals)
    times = pd.date_range("2022-12-31T00:00:00", periods=4, freq="1h")
    zs = np.zeros((4, 4, 4), dtype=np.float32)
    zs[1, :, :] = 1.35
    zs[2, :, :] = 2.05
    zs[3, :, :] = 1.60
    msk = np.ones((4, 4), dtype=np.int8)

    map_nc = tmp_path / "sfincs_map.nc"
    ds = xr.Dataset(
        data_vars={
            "zs": (("time", "n", "m"), zs),
            "msk": (("n", "m"), msk),
        },
        coords={
            "time": times,
            "x": (("n", "m"), xx),
            "y": (("n", "m"), yy),
        },
    )
    ds.to_netcdf(map_nc)
    ds.close()

    tide_csv = tmp_path / "tide_obs.csv"
    pd.DataFrame(
        {
            "time_min": [0.0, 60.0, 120.0, 180.0],
            "wl_9414290_m": [1.20, 1.35, 2.04, 1.61],
            "wl_9999999_m": [1.0, 1.0, 1.0, 1.0],  # Unconfigured gauge ignored
        }
    ).to_csv(tide_csv, index=False)

    tide_gauges_cfg = [
        {
            "id": "9414290",
            "name": "San Francisco (Presidio)",
            "x_epsg26910": 547016.8,
            "y_epsg26910": 4184458.7,
        }
    ]
    res = _VAL.evaluate_event_gauges(
        gauges_file=tide_csv,
        map_nc_path=map_nc,
        tide_gauges_cfg=tide_gauges_cfg,
    )
    assert res is not None
    assert res["vertical_datum"] == "NAVD88"
    assert res["n_stations"] == 1
    st = res["stations"][0]
    assert st["station_id"] == "9414290"
    assert st["n_samples"] == 3  # t=0 cold-start skipped
    assert math.isclose(st["bias_m"], 0.0, abs_tol=1e-4)
    assert math.isclose(st["peak_timing_error_min"], 0.0, abs_tol=1e-1)
    assert st["rmse_m"] < 0.01

    # Also test JSON HWM evaluation path
    hwm_json = tmp_path / "hwms.json"
    hwm_json.write_text(
        json.dumps(
            {
                "hwm_records": [
                    {
                        "hwm_id": "HWM-A",
                        "latitude": 37.806,
                        "longitude": -122.465,
                        "wse_m": 2.05,
                        "vertical_datum": "NAVD88",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # Build matching raster covering (-122.465, 37.806) in EPSG:26910
    hwm_gdf = _VAL.parse_usgs_stn_hwm_records(
        json.loads(hwm_json.read_text(encoding="utf-8"))["hwm_records"]
    )
    hx = float(hwm_gdf["x_epsg26910"].iloc[0])
    hy = float(hwm_gdf["y_epsg26910"].iloc[0])
    tf = from_origin(hx - 10.0, hy + 10.0, 2.0, 2.0)
    zsmax_m = np.full((10, 10), 2.05, dtype=np.float32)
    hwm_res = _VAL.evaluate_event_gauges(
        gauges_file=hwm_json,
        map_nc_path=map_nc,
        zsmax_m=zsmax_m,
        transform=tf,
    )
    assert hwm_res is not None
    assert hwm_res["n_hwm"] == 1
    assert math.isclose(hwm_res["summary"]["rmse_m"], 0.0, abs_tol=1e-4)


def test_pluggable_tier_b_benchmark_interface(tmp_path: Path) -> None:
    tf = from_origin(560000.0, 4150040.0, 10.0, 10.0)
    valid_land = np.ones((4, 4), dtype=bool)
    depth_m = np.zeros((4, 4), dtype=np.float32)
    depth_m[1:3, 1:3] = 0.25  # 4 flooded cells (>= 0.1524 m)

    # Benchmark polygons with attribute column so we can test attribute_filter
    poly_ae = Polygon(
        [
            (560010.0, 4150010.0),
            (560030.0, 4150010.0),
            (560030.0, 4150030.0),
            (560010.0, 4150030.0),
            (560010.0, 4150010.0),
        ]
    )
    poly_x = Polygon(
        [
            (560000.0, 4150030.0),
            (560010.0, 4150030.0),
            (560010.0, 4150040.0),
            (560000.0, 4150040.0),
            (560000.0, 4150030.0),
        ]
    )
    gdf_utm = gpd.GeoDataFrame(
        [
            {"FLD_ZONE": "AE", "geometry": poly_ae},
            {"FLD_ZONE": "X", "geometry": poly_x},
        ],
        crs="EPSG:26910",
    )

    corridors = {"EPA_Core": (560000.0, 4150000.0, 560040.0, 4150040.0)}
    result = _VAL.evaluate_polygon_benchmark(
        depth_m=depth_m,
        benchmark_gdf_utm=gdf_utm,
        valid_land_mask=valid_land,
        transform=tf,
        source="fema_nfhl",
        depth_threshold_m=0.1524,
        corridors_utm=corridors,
        attribute_filter={"FLD_ZONE": ["AE", "A"]},
    )
    assert result["summary"]["source"] == "fema_nfhl"
    assert math.isclose(result["summary"]["csi"], 1.0, abs_tol=1e-6)
    assert math.isclose(result["summary"]["hit_rate"], 1.0, abs_tol=1e-6)
    assert math.isclose(result["summary"]["false_alarm_ratio"], 0.0, abs_tol=1e-6)
    assert len(result["corridors"]) == 1
    assert result["corridors"][0]["corridor"] == "EPA_Core"

    # CRS mismatch must raise ValueError
    with pytest.raises(ValueError, match="CRS mismatch"):
        _VAL.evaluate_polygon_benchmark(
            depth_m=depth_m,
            benchmark_gdf_utm=gdf_utm.to_crs("EPSG:4326"),
            valid_land_mask=valid_land,
            transform=tf,
            source="fema_nfhl",
        )

    # Config loader supports both pluggable validation.benchmarks and legacy domain.validation_reference
    cfg_pluggable = {
        "project": {"region_id": "epa_pilot"},
        "domain": {},
        "validation": {
            "depth_threshold_m": 0.1524,
            "benchmarks": [
                {
                    "source": "valley_water_100yr",
                    "vector_file": str(tmp_path / "vw.gpkg"),
                    "depth_threshold_m": 0.1524,
                    "attribute_filter": {"ZONE": ["100YR"]},
                }
            ],
        },
    }
    specs = _VAL.load_benchmark_specs(cfg_pluggable)
    assert len(specs) == 1
    assert specs[0].source == "valley_water_100yr"
    assert specs[0].corridors_utm is None

    cfg_legacy = {
        "project": {"region_id": "sf_citywide"},
        "domain": {"validation_reference": str(tmp_path / "sfpuc.gpkg")},
        "validation": {"depth_threshold_m": 0.1524},
    }
    legacy_specs = _VAL.load_benchmark_specs(cfg_legacy)
    assert len(legacy_specs) == 1
    assert legacy_specs[0].source == "sfpuc_100yr"
    assert legacy_specs[0].corridors_utm == _VAL.SF_NEIGHBORHOOD_BOXES_UTM
