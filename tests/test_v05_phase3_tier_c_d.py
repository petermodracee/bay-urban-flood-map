"""Offline unit tests for v0.5 Phase 3 Tier C (road closures) and Tier D (Groundsource)."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr
import yaml
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon, box

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(script_name: str, mod_name: str):
    script_path = REPO_ROOT / "pipeline" / script_name
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_FETCH = _load_module("00_fetch_data.py", "v05_phase3_fetch")
_VAL = _load_module("03_validate_sf.py", "v05_phase3_validate")


def test_parse_caltrans_lcs_and_511_flood_closures() -> None:
    sf_bbox = (-122.52, 37.70, -122.35, 37.82)
    caltrans_payload = {
        "data": [
            {
                "lcs": {
                    "index": "101-1",
                    "location": {
                        "begin": {
                            "beginLatitude": 37.735,
                            "beginLongitude": -122.405,
                            "beginRoute": "US-101",
                            "beginLocationName": "Alemany Blvd",
                        }
                    },
                    "closure": {
                        "closureID": "LCS-101",
                        "typeOfWork": "Emergency Roadway Flooding",
                        "typeOfClosure": "Full",
                        "closureComments": "Standing water at underpass",
                        "closureTimestamp": {"closureStartDate": "2022-12-31"},
                    },
                }
            },
            {
                "lcs": {
                    "index": "280-2",
                    "location": {
                        "begin": {
                            "beginLatitude": 37.740,
                            "beginLongitude": -122.410,
                            "beginRoute": "I-280",
                            "beginLocationName": "Cesar Chavez",
                        }
                    },
                    "closure": {
                        "closureID": "LCS-280",
                        "typeOfWork": "Paving Maintenance",
                        "typeOfClosure": "Lane",
                    },
                }
            },
            {
                "lcs": {
                    "index": "out-of-bbox",
                    "location": {
                        "begin": {
                            "beginLatitude": 34.05,
                            "beginLongitude": -118.25,
                            "beginRoute": "I-10",
                        }
                    },
                    "closure": {"typeOfWork": "Flooding"},
                }
            },
        ]
    }
    cal_gdf = _FETCH.parse_caltrans_lcs_json(
        caltrans_payload, bbox_wgs84=sf_bbox, flood_only=True
    )
    assert len(cal_gdf) == 1
    assert cal_gdf.iloc[0]["event_id"] == "LCS-101"
    assert cal_gdf.iloc[0]["corridor"] == "US-101"
    assert cal_gdf.iloc[0]["source"] == "caltrans_lcs"

    payload_511 = {
        "events": [
            {
                "id": "511-1",
                "headline": "Flooding on US-101 NB at Cesar Chavez",
                "description": "High water blocking right lanes",
                "event_type": "INCIDENT",
                "updated": "2022-12-31T18:00:00Z",
                "roads": [{"name": "US-101"}],
                "geography": {"type": "Point", "coordinates": [-122.402, 37.748]},
            },
            {
                "id": "511-2",
                "headline": "Planned utility work",
                "description": "No water",
                "event_type": "CONSTRUCTION",
                "roads": [{"name": "Van Ness Ave"}],
                "geography": {"type": "Point", "coordinates": [-122.420, 37.780]},
            },
        ]
    }
    gdf_511 = _FETCH.parse_511_events_json(
        payload_511, bbox_wgs84=sf_bbox, flood_only=True
    )
    assert len(gdf_511) == 1
    assert gdf_511.iloc[0]["event_id"] == "511-1"
    assert gdf_511.iloc[0]["source"] == "511_sf_bay"


def test_snap_and_score_tier_c_road_closures() -> None:
    xmin, ymax = 550000.0, 4177000.0
    transform = from_origin(xmin, ymax, 2.0, 2.0)
    height, width = 100, 100
    valid_land_mask = np.ones((height, width), dtype=bool)
    ar_depth_m = np.zeros((height, width), dtype=np.float32)

    # Road network: horizontal Alemany corridor (y=4176900 -> row 50) & vertical US-101 (x=550100 -> col 50)
    roads_gdf = gpd.GeoDataFrame(
        [
            {
                "highway": "trunk",
                "name": "US-101",
                "geometry": LineString([(550100.0, 4176805.0), (550100.0, 4176995.0)]),
            },
            {
                "highway": "primary",
                "name": "Alemany",
                "geometry": LineString([(550005.0, 4176900.0), (550195.0, 4176900.0)]),
            },
        ],
        crs="EPSG:26910",
    )

    # Small flooded patches on US-101 (y=4176950 -> row 25, col 50) and Alemany (x=550130, y=4176900 -> row 50, col 65)
    ar_depth_m[49:51, 64:66] = 0.35
    ar_depth_m[24:26, 49:51] = 0.35

    # Two closure reports slightly offset (~10 m) from roads near flooded areas, plus one dry point
    closures_gdf = gpd.GeoDataFrame(
        [
            {"corridor": "US-101", "geometry": Point(550110.0, 4176950.0)},
            {"corridor": "Alemany", "geometry": Point(550130.0, 4176910.0)},
            {"corridor": "I-280", "geometry": Point(550020.0, 4176908.0)},
        ],
        crs="EPSG:26910",
    )

    snapped_gdf, n_snapped = _VAL.snap_points_to_roads(
        closures_gdf, roads_gdf, max_snap_distance_m=50.0
    )
    assert n_snapped == 3
    assert math.isclose(snapped_gdf.geometry.iloc[0].x, 550100.0, abs_tol=1e-6)
    assert math.isclose(snapped_gdf.geometry.iloc[1].y, 4176900.0, abs_tol=1e-6)

    metrics = _VAL.compute_road_closure_metrics(
        closures_gdf_utm=closures_gdf,
        ar_depth_m=ar_depth_m,
        valid_land_mask=valid_land_mask,
        transform=transform,
        roads_gdf_utm=roads_gdf,
        n_controls=200,
        seed=42,
    )
    assert metrics["valid_land_points"] == 3
    assert metrics["snapped_points"] == 3
    assert metrics["control_type"] == "road_network"
    assert metrics["n_controls"] == 200
    assert [b["radius_m"] for b in metrics["by_buffer"]] == [25.0, 50.0]

    b25 = metrics["by_buffer"][0]
    assert math.isclose(
        b25["thresholds"]["0.05m"]["capture_rate"], 2.0 / 3.0, abs_tol=1e-4
    )
    assert b25["thresholds"]["0.05m"]["lift"] > 1.0
    assert b25["ar_roc_auc"] > 0.65
    assert metrics["by_corridor"]["US-101"]["capture_rate"] == 1.0
    assert metrics["by_corridor"]["I-280"]["capture_rate"] == 0.0


def test_groundsource_filter_subbasin_match_and_holdout_discovery() -> None:
    xmin, ymin, xmax, ymax = 550000.0, 4176000.0, 554000.0, 4180000.0
    transform = from_origin(xmin, ymax, 20.0, 20.0)
    height, width = 200, 200
    valid_land_mask = np.ones((height, width), dtype=bool)
    modeled_depth_m = np.zeros((height, width), dtype=np.float32)
    # Flood SW quadrant (rows 100..200, cols 0..100 -> x in [550000, 552000], y in [4176000, 4178000])
    modeled_depth_m[120:160, 20:60] = 0.25

    gs_gdf = gpd.GeoDataFrame(
        [
            {
                "area_km2": 0.5,
                "start_date": "2022-12-31",
                "end_date": "2023-01-01",
                "geometry": box(550200.0, 4176200.0, 550900.0, 4176900.0),
            },
            {
                "area_km2": 0.4,
                "start_date": "2023-03-14",
                "end_date": "2023-03-15",
                "geometry": box(550300.0, 4176300.0, 550800.0, 4176800.0),
            },
            {
                "area_km2": 0.3,
                "start_date": "2023-03-14",
                "end_date": "2023-03-15",
                "geometry": box(550400.0, 4176400.0, 550700.0, 4176700.0),
            },
            {
                # Should be filtered out (> 25 km2)
                "area_km2": 40.0,
                "start_date": "2023-03-14",
                "end_date": "2023-03-15",
                "geometry": box(552100.0, 4178100.0, 553900.0, 4179900.0),
            },
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326")

    filtered = _VAL.filter_groundsource_records(
        gs_gdf,
        max_area_km2=25.0,
        domain_bbox_utm=(xmin, ymin, xmax, ymax),
        target_crs="EPSG:26910",
    )
    assert len(filtered) == 3
    assert all(filtered["area_km2"] <= 25.0)

    ensemble_events = [
        {
            "id": "ar_20221231",
            "validation_311_start_utc": "2022-12-31T00:00:00",
            "validation_311_end_utc": "2023-01-02T00:00:00",
        }
    ]
    res = _VAL.evaluate_groundsource_subbasin_match(
        groundsource_gdf_utm=filtered,
        modeled_depth_m=modeled_depth_m,
        valid_land_mask=valid_land_mask,
        transform=transform,
        domain_bbox_utm=(xmin, ymin, xmax, ymax),
        depth_threshold_m=0.05,
        event_windows=ensemble_events,
    )
    assert res["n_groundsource_records"] == 3
    assert res["n_subbasins"] == 4
    assert res["n_both_present"] == 1
    assert res["n_neither"] == 3
    assert res["presence_hit_rate"] == 1.0
    assert res["presence_accuracy"] == 1.0
    assert res["matched_ensemble_events"] == ["ar_20221231"]
    assert len(res["holdout_storm_candidates"]) == 1
    assert res["holdout_storm_candidates"][0]["start_date"] == "2023-03-14"
    assert res["holdout_storm_candidates"][0]["n_records"] == 2


def test_validate_region_wires_tier_c_and_groundsource_end_to_end(
    tmp_path: Path,
) -> None:
    xmin, ymin, xmax, ymax = 550000.0, 4176000.0, 550200.0, 4176200.0
    dem_tf = from_origin(xmin, ymax, 2.0, 2.0)
    dem = np.full((100, 100), 10.0, dtype=np.float32)
    dem_path = tmp_path / "hydro_dem.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=dem_tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(dem, 1)

    nlcd_path = tmp_path / "nlcd.tif"
    with rasterio.open(
        nlcd_path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="uint8",
        crs="EPSG:26910",
        transform=dem_tf,
        nodata=0,
    ) as dst:
        dst.write(np.full((100, 100), 23, dtype=np.uint8), 1)

    ref_path = tmp_path / "sfpuc.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "geometry": Polygon(
                    [
                        (550040, 4176040),
                        (550160, 4176040),
                        (550160, 4176160),
                        (550040, 4176160),
                    ]
                ),
            }
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326").to_file(ref_path, driver="GPKG")

    roads_path = tmp_path / "roads.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "highway": "trunk",
                "name": "US-101",
                "geometry": LineString([(550100.0, 4176010.0), (550100.0, 4176190.0)]),
            }
        ],
        crs="EPSG:26910",
    ).to_file(roads_path, driver="GPKG")

    closures_path = tmp_path / "closures.gpkg"
    gpd.GeoDataFrame(
        [{"corridor": "US-101", "geometry": Point(550104.0, 4176100.0)}],
        crs="EPSG:26910",
    ).to_file(closures_path, driver="GPKG")

    pts_311_path = tmp_path / "pts311.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "service_subtype": "Flooding",
                "neighborhood": "Mission",
                "address": "100 Main",
                "geometry": Point(550100.0, 4176100.0),
            },
            {
                "service_subtype": "Flooding",
                "neighborhood": "Mission",
                "address": "200 Main",
                "geometry": Point(550020.0, 4176020.0),
            },
        ],
        crs="EPSG:26910",
    ).to_file(pts_311_path, driver="GPKG")

    gs_path = tmp_path / "groundsource_sf.parquet"
    gpd.GeoDataFrame(
        [
            {
                "area_km2": 0.2,
                "start_date": "2022-12-31",
                "end_date": "2023-01-01",
                "geometry": box(550050.0, 4176050.0, 550150.0, 4176150.0),
            },
            {
                "area_km2": 0.3,
                "start_date": "2024-03-01",
                "end_date": "2024-03-02",
                "geometry": box(550060.0, 4176060.0, 550140.0, 4176140.0),
            },
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326").to_parquet(gs_path)

    runs_dir = tmp_path / "runs"
    for scen in ("synth_100yr_slr0ft", "synth_ar_20221231"):
        run_dir = runs_dir / scen
        run_dir.mkdir(parents=True, exist_ok=True)
        xs = np.arange(550010.0, 550200.0, 20.0)
        ys = np.arange(4176190.0, 4176000.0, -20.0)
        zsmax = np.full((1, len(ys), len(xs)), 10.0, dtype=np.float32)
        zsmax[0, 2:8, 2:8] = 10.30
        ds = xr.Dataset(
            {"zsmax": (("timemax", "y", "x"), zsmax)},
            coords={"timemax": [0.0], "y": ys, "x": xs},
        )
        ds.to_netcdf(run_dir / "sfincs_map.nc")

    ensemble_json = tmp_path / "validation_ensemble_metrics.json"
    cfg = {
        "project": {
            "name": "synth",
            "region_id": "synth",
            "baseline_scenario": "synth_100yr_slr0ft",
            "crs": "EPSG:26910",
        },
        "domain": {
            "bbox_epsg26910": [xmin, ymin, xmax, ymax],
            "grid_resolution_m": 20.0,
            "nlcd_landcover_source": str(nlcd_path),
            "roads_rail_source": str(roads_path),
            "validation_reference": str(ref_path),
        },
        "terrain_prep": {
            "hydro_dem_file": str(dem_path),
            "nodata_value": -9999.0,
            "manning_n_paved": 0.030,
        },
        "sfincs_model": {
            "drainage_tier": "tier0",
            "drainage_rate_mm_hr": 5.0,
            "rain_source": "mrms",
        },
        "paths": {"runs_dir": str(runs_dir)},
        "validation": {
            "depth_threshold_m": 0.1524,
            "min_land_elevation_m": 0.5,
            "downscaled_depth_file": str(tmp_path / "depth_100yr.tif"),
            "summary_file": str(tmp_path / "validation_summary.json"),
            "ensemble_metrics_file": str(ensemble_json),
            "hindcast_ensemble": [
                {
                    "id": "ar_20221231",
                    "label": "Dec 31, 2022 AR",
                    "scenario": "synth_ar_20221231",
                    "depth_file": str(tmp_path / "depth_ar.tif"),
                    "validation_311_reference": str(pts_311_path),
                    "validation_311_start_utc": "2022-12-31T00:00:00",
                    "validation_311_end_utc": "2023-01-02T00:00:00",
                    "road_closures_file": str(closures_path),
                }
            ],
            "sources": {
                "groundsource_file": str(gs_path),
            },
        },
    }
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    _VAL.validate_region(cfg_path)
    payload = json.loads(ensemble_json.read_text(encoding="utf-8"))

    ev = payload["events"][0]
    assert ev["tiers_evaluated"] == ["311", "road_closures"]
    assert ev["road_closures_validation"] is not None
    assert ev["road_closures_validation"]["snapped_points"] == 1
    assert ev["road_closures_validation"]["control_type"] == "road_network"

    gs_res = payload["regional_crosschecks"]["groundsource_subbasin_match"]
    assert gs_res is not None
    assert gs_res["n_groundsource_records"] == 2
    assert gs_res["matched_ensemble_events"] == ["ar_20221231"]
    assert gs_res["holdout_storm_candidates"][0]["start_date"] == "2024-03-01"
