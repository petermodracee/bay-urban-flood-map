"""Unit tests for v0.6 Phase 1 validation overlay exports and manifest schema."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import geopandas as gpd
import yaml
from shapely.geometry import Point, Polygon

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_export_module():
    script_path = REPO_ROOT / "pipeline" / "04_export_cog.py"
    spec = importlib.util.spec_from_file_location("phase4_export_cog", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_manifest_v06_fields() -> None:
    mod = _load_export_module()
    overlays = {
        "tier_a_gauges": {"url": "/data/sf_tier_a_gauges.geojson", "count": 3},
        "tier_c_by_storm": {
            "ar_20221231": {
                "url": "/data/sf_tier_c_20221231_closures.geojson",
                "count": 66,
            }
        },
        "tier_d_by_storm": {
            "ar_20221231": {"url": "/data/sf_311_20221231_floods.geojson", "count": 424}
        },
    }
    manifest = mod.build_manifest(
        [
            {
                "id": "sf_100yr_slr0ft_depth",
                "storm": "100yr",
                "slr_ft": 0,
                "metric": "depth",
                "url": "/data/a.tif",
            }
        ],
        version="0.6",
        drainage_tier="tier0",
        rain_source="mrms",
        overlays=overlays,
    )
    assert manifest["version"] == "0.6"
    assert manifest["drainage_tier"] == "tier0"
    assert manifest["rain_source"] == "mrms"
    assert manifest["overlays"]["tier_a_gauges"]["count"] == 3
    assert manifest["overlays"]["tier_c_by_storm"]["ar_20221231"]["count"] == 66
    assert manifest["overlays"]["tier_d_by_storm"]["ar_20221231"]["count"] == 424


def test_export_tier_c_and_tier_a_gauges_geojson(tmp_path: Path) -> None:
    mod = _load_export_module()
    bbox = [542600.0, 4173000.0, 555600.0, 4185400.0]

    tier_c_gpkg = tmp_path / "tier_c.gpkg"
    gdf_c = gpd.GeoDataFrame(
        [
            {
                "record_id": "101",
                "timestamp": "2022-12-31T12:00:00",
                "category": "TRAFFIC HAZARD",
                "source": "datasf_dem_2zdj",
                "geometry": Point(548000.0, 4178000.0),
            },
            {
                "record_id": "102",
                "timestamp": "2022-12-31T13:00:00",
                "category": "Water Rescue",
                "source": "sffd",
                "geometry": Point(500000.0, 4100000.0),  # outside bbox
            },
        ],
        crs="EPSG:26910",
    )
    gdf_c.to_file(tier_c_gpkg, driver="GPKG")

    out_c = tmp_path / "out_tier_c.geojson"
    _, count_c = mod.export_tier_c_geojson(
        source_path=tier_c_gpkg,
        bbox_utm=bbox,
        project_crs="EPSG:26910",
        output_path=out_c,
        storm_id="ar_20221231",
    )
    assert count_c == 1
    loaded_c = gpd.read_file(out_c)
    assert loaded_c.crs.to_string() == "EPSG:4326"
    assert loaded_c.iloc[0]["storm_id"] == "ar_20221231"
    assert loaded_c.iloc[0]["category"] == "TRAFFIC HAZARD"

    ens_path = tmp_path / "ensemble.json"
    ens_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "ar_20221231",
                        "gauge_hwm_validation": {
                            "vertical_datum": "NAVD88",
                            "stations": [
                                {
                                    "station_id": "9414290",
                                    "name": "San Francisco (Presidio)",
                                    "obs_peak_m": 2.066,
                                    "mod_peak_m": 2.0661,
                                    "bias_m": 0.0007,
                                    "rmse_m": 0.0014,
                                    "peak_timing_error_min": 0.0,
                                }
                            ],
                        },
                    },
                    {
                        "id": "ar_20240204",
                        "gauge_hwm_validation": {
                            "vertical_datum": "NAVD88",
                            "stations": [
                                {
                                    "station_id": "9414290",
                                    "name": "San Francisco (Presidio)",
                                    "obs_peak_m": 2.24,
                                    "mod_peak_m": 2.2411,
                                    "bias_m": 0.0009,
                                    "rmse_m": 0.0016,
                                    "peak_timing_error_min": 0.0,
                                }
                            ],
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    out_a = tmp_path / "tier_a.geojson"
    _, count_a = mod.export_tier_a_gauges_geojson(
        ensemble_metrics_path=ens_path,
        tide_gauges_cfg=[
            {
                "id": "9414290",
                "name": "San Francisco (Presidio)",
                "x_epsg26910": 547016.8,
                "y_epsg26910": 4184458.7,
            }
        ],
        project_crs="EPSG:26910",
        output_path=out_a,
        exclude_storms=["ar_20240204"],
    )
    assert count_a == 1
    loaded_a = gpd.read_file(out_a)
    assert loaded_a.crs.to_string() == "EPSG:4326"
    assert loaded_a.iloc[0]["storm_id"] == "ar_20221231"
    assert float(loaded_a.iloc[0]["observed_peak_navd88_m"]) == 2.066


def test_export_overlays_only_excludes_holdout_storms(tmp_path: Path) -> None:
    mod = _load_export_module()
    web_out = tmp_path / "web_outputs"
    web_out.mkdir()
    (web_out / "sf_citywide_ar_20221231_depth_3857.tif").write_bytes(b"stub")
    (web_out / "sf_citywide_ar_20240204_depth_3857.tif").write_bytes(b"stub")

    bench_gpkg = tmp_path / "bench.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "geometry": Polygon(
                    [
                        (545000, 4175000),
                        (546000, 4175000),
                        (546000, 4176000),
                        (545000, 4176000),
                    ]
                ),
            }
        ],
        crs="EPSG:26910",
    ).to_file(bench_gpkg, driver="GPKG")

    cfg = {
        "project": {
            "region_id": "sf_citywide",
            "crs": "EPSG:26910",
            "tide_gauges": [],
        },
        "domain": {
            "bbox_epsg26910": [542600.0, 4173000.0, 555600.0, 4185400.0],
            "validation_reference": str(bench_gpkg),
        },
        "paths": {"web_output_dir": str(web_out)},
        "sfincs_model": {"drainage_tier": "tier0", "rain_source": "mrms"},
        "rain_events": {
            "rain_ar_20221231": {
                "label": "Dec 31, 2022 Atmospheric River",
                "return_period_yr": None,
            },
            "rain_ar_20240204": {
                "label": "Feb 4, 2024 Atmospheric River",
                "return_period_yr": None,
            },
        },
        "slr_increments": {
            "tide_ar_20221231": {"slr_ft": 0},
            "tide_ar_20240204": {"slr_ft": 0},
        },
        "scenarios": [
            {
                "id": "sf_citywide_ar_20221231",
                "rain": "rain_ar_20221231",
                "slr": "tide_ar_20221231",
            },
            {
                "id": "sf_citywide_ar_20240204",
                "rain": "rain_ar_20240204",
                "slr": "tide_ar_20240204",
            },
        ],
        "validation": {"hindcast_ensemble": []},
        "export": {
            "benchmark_geojson_file": str(web_out / "sf_sfpuc_100yr_benchmark.geojson"),
            "manifest_file": str(web_out / "manifest.json"),
            "manifest_version": "0.6",
            "holdout_storms": ["ar_20240204"],
            "web_data_url_prefix": "/data",
        },
    }
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    manifest_path = mod.export_overlays_only(cfg_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6"
    assert manifest["rain_source"] == "mrms"
    assert [s["storm"] for s in manifest["scenarios"]] == ["ar_20221231"]

    # Version-prefixed manifest_version ("v0.6.1") prefixes scenario & overlay URLs
    cfg["export"]["manifest_version"] = "v0.6.1"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    manifest_v_path = mod.export_overlays_only(cfg_path)
    manifest_v = json.loads(manifest_v_path.read_text(encoding="utf-8"))
    assert manifest_v["version"] == "v0.6.1"
    assert manifest_v["scenarios"][0]["url"] == (
        "/data/v0.6.1/sf_citywide_ar_20221231_depth_3857.tif"
    )
    assert manifest_v["overlays"]["tier_b_benchmark"]["url"] == (
        "/data/v0.6.1/sf_sfpuc_100yr_benchmark.geojson"
    )
