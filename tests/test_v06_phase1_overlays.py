"""Unit tests for v0.6 Phase 1 validation overlay exports and manifest schema."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin
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


def test_point_overlays_strip_free_text_and_apply_valid_land_filter(
    tmp_path: Path,
) -> None:
    mod = _load_export_module()
    val_mod = mod._load_validate_module()

    xmin, ymax = 548000.0, 4179000.0
    tf = from_origin(xmin, ymax, 10.0, 10.0)
    bbox = [xmin, ymax - 100.0, xmin + 100.0, ymax]

    # 10x10 DEM: row 0..3 low (< 0.5 m), row 4..9 high (10.0 m)
    dem = np.full((10, 10), 10.0, dtype=np.float32)
    dem[0:4, :] = 0.2
    dem_path = tmp_path / "hydro_dem.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(dem, 1)

    # 10x10 NLCD: col 0..3 open water (11), col 4..9 developed (24)
    nlcd = np.full((10, 10), 24, dtype=np.uint8)
    nlcd[:, 0:4] = 11
    nlcd_path = tmp_path / "nlcd.tif"
    with rasterio.open(
        nlcd_path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="uint8",
        crs="EPSG:26910",
        transform=tf,
    ) as dst:
        dst.write(nlcd, 1)

    valid_land_mask, land_transform = val_mod.load_land_valid_mask(
        hydro_dem_path=dem_path,
        project_crs="EPSG:26910",
        min_land_elevation_m=0.5,
        nlcd_landcover_path=nlcd_path,
        exclude_open_water=True,
    )

    # Points: (1) valid land, (2) below min_land_elevation_m, (3) NLCD open water
    pt_valid = Point(xmin + 65.0, ymax - 65.0)  # row 6, col 6 -> valid
    pt_low = Point(xmin + 65.0, ymax - 15.0)  # row 1, col 6 -> dem=0.2 (< 0.5)
    pt_water = Point(xmin + 15.0, ymax - 65.0)  # row 6, col 1 -> nlcd=11

    gpkg_311 = tmp_path / "311.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "service_request_id": "1",
                "requested_datetime": "2022-12-31T12:00:00",
                "service_subtype": "Flooding",
                "service_details": "Private caller note",
                "address": "123 Main St",
                "neighborhood": "Mission",
                "geometry": pt_valid,
            },
            {
                "service_request_id": "2",
                "requested_datetime": "2022-12-31T12:05:00",
                "service_subtype": "Flooding",
                "service_details": "Low pier",
                "address": "Pier 1",
                "neighborhood": "Waterfront",
                "geometry": pt_low,
            },
            {
                "service_request_id": "3",
                "requested_datetime": "2022-12-31T12:10:00",
                "service_subtype": "Flooding",
                "service_details": "In bay",
                "address": "Bay",
                "neighborhood": "Bay",
                "geometry": pt_water,
            },
        ],
        crs="EPSG:26910",
    ).to_file(gpkg_311, driver="GPKG")

    out_311 = tmp_path / "out_311.geojson"
    mod.export_311_geojson(
        source_path=gpkg_311,
        bbox_utm=bbox,
        project_crs="EPSG:26910",
        output_path=out_311,
        storm_id="ar_20221231",
        valid_land_mask=valid_land_mask,
        land_transform=land_transform,
    )
    loaded_311 = json.loads(out_311.read_text(encoding="utf-8"))
    assert len(loaded_311["features"]) == 1
    props_311 = loaded_311["features"][0]["properties"]
    assert "service_details" not in props_311
    assert "address" not in props_311
    assert set(props_311.keys()) == {
        "service_request_id",
        "requested_datetime",
        "service_subtype",
        "neighborhood",
        "storm_id",
    }

    gpkg_c = tmp_path / "tier_c.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "record_id": "c1",
                "timestamp": "2022-12-31T12:00:00",
                "category": "TRAFFIC HAZARD",
                "source": "datasf_dem_2zdj",
                "address": "456 Market St",
                "service_details": "Caller text",
                "geometry": pt_valid,
            },
            {
                "record_id": "c2",
                "timestamp": "2022-12-31T12:05:00",
                "category": "Water Rescue",
                "source": "sffd",
                "address": "Pier 2",
                "geometry": pt_low,
            },
            {
                "record_id": "c3",
                "timestamp": "2022-12-31T12:10:00",
                "category": "Water Rescue",
                "source": "sffd",
                "address": "Bay",
                "geometry": pt_water,
            },
        ],
        crs="EPSG:26910",
    ).to_file(gpkg_c, driver="GPKG")

    out_c = tmp_path / "out_c.geojson"
    _, count_c = mod.export_tier_c_geojson(
        source_path=gpkg_c,
        bbox_utm=bbox,
        project_crs="EPSG:26910",
        output_path=out_c,
        storm_id="ar_20221231",
        valid_land_mask=valid_land_mask,
        land_transform=land_transform,
    )
    assert count_c == 1
    loaded_c = json.loads(out_c.read_text(encoding="utf-8"))
    props_c = loaded_c["features"][0]["properties"]
    assert "address" not in props_c
    assert "service_details" not in props_c
    assert set(props_c.keys()) == {
        "record_id",
        "timestamp",
        "category",
        "source",
        "storm_id",
    }


def test_sf_citywide_dec_2022_manifest_pathway_is_multi_gauge() -> None:
    mod = _load_export_module()
    cfg = yaml.safe_load(
        (REPO_ROOT / "configs" / "sf_citywide.yml").read_text(encoding="utf-8")
    )
    slr_cfg = cfg["slr_increments"]["tide_ar_20221231"]
    assert slr_cfg["opc_2024_pathway"] == "Observed Hindcast (NOAA Multi-Gauge)"

    entry = mod.build_manifest_entry(
        scenario_cfg={"id": "sf_citywide_ar_20221231", "rain": "rain_ar_20221231"},
        rain_cfg=cfg["rain_events"]["rain_ar_20221231"],
        slr_cfg=slr_cfg,
        cog_filename="sf_citywide_ar_20221231_depth_3857.tif",
    )
    assert entry["opc_2024_pathway"] == "Observed Hindcast (NOAA Multi-Gauge)"
