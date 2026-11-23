"""Offline unit tests for v0.5 Phase 4 Tier E (OpenFEMA NFIP tract-level crosscheck)."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
import yaml
from rasterio.transform import from_origin
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(script_name: str, mod_name: str):
    script_path = REPO_ROOT / "pipeline" / script_name
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_FETCH = _load_module("00_fetch_data.py", "v05_phase4_fetch")
_VAL = _load_module("03_validate_sf.py", "v05_phase4_validate")


def test_parse_openfema_nfip_claims_and_join_tracts() -> None:
    payload = {
        "FimaNfipClaims": [
            {
                "censusTract": "06075010100",
                "countyCode": "06075",
                "yearOfLoss": 2022,
                "amountPaidOnBuildingClaim": 12000.5,
                "amountPaidOnContentsClaim": 3000.0,
                "amountPaidOnIncreasedCostOfComplianceClaim": 0.0,
            },
            {
                "censusTract": "6075010100",  # missing leading zero
                "countyCode": "6075",
                "yearOfLoss": 2023,
                "amountPaidOnBuildingClaim": 0.0,
                "amountPaidOnContentsClaim": None,
                "amountPaidOnIncreasedCostOfComplianceClaim": None,
            },
            {
                "censusTract": "06075010200",
                "countyCode": "06075",
                "yearOfLoss": 2015,  # filtered out by start_year=2018
                "amountPaidOnBuildingClaim": 50000.0,
            },
            {
                "censusTract": "06001400100",  # Alameda County -> filtered out
                "countyCode": "06001",
                "yearOfLoss": 2022,
                "amountPaidOnBuildingClaim": 9000.0,
            },
            {
                "censusTract": None,
                "countyCode": "06075",
                "yearOfLoss": 2022,
                "amountPaidOnBuildingClaim": 1000.0,
            },
        ]
    }
    df = _FETCH.parse_openfema_nfip_claims(
        payload, county_fips="06075", start_year=2018
    )
    assert len(df) == 1
    row = df.iloc[0]
    assert row["census_tract"] == "06075010100"
    assert row["claim_count"] == 2
    assert row["paid_claim_count"] == 1
    assert math.isclose(row["paid_building_usd"], 12000.5)
    assert math.isclose(row["paid_contents_usd"], 3000.0)
    assert math.isclose(row["total_paid_usd"], 15000.5)

    tracts_wgs84 = gpd.GeoDataFrame(
        [
            {"GEOID": "06075010100", "geometry": box(-122.42, 37.75, -122.41, 37.76)},
            {"GEOID": "06075010200", "geometry": box(-122.41, 37.75, -122.40, 37.76)},
        ],
        crs="EPSG:4326",
    )
    joined = _FETCH.join_nfip_claims_to_tracts(
        df, tracts_wgs84, target_crs="EPSG:26910"
    )
    assert str(joined.crs) == "EPSG:26910"
    assert len(joined) == 2
    t1 = joined[joined["census_tract"] == "06075010100"].iloc[0]
    t2 = joined[joined["census_tract"] == "06075010200"].iloc[0]
    assert t1["claim_count"] == 2
    assert t2["claim_count"] == 0
    assert math.isclose(t2["total_paid_usd"], 0.0)


def test_compute_tract_building_exposure_and_spearman() -> None:
    xmin, ymin = 550000.0, 4176000.0
    tf = from_origin(xmin, ymin + 200.0, 10.0, 10.0)
    depth_m = np.zeros((20, 20), dtype=np.float32)
    valid_land = np.ones((20, 20), dtype=bool)
    # Mask top-left 5x5 as open water; even if depth > 0, it should be excluded
    valid_land[0:5, 0:5] = False
    depth_m[0:5, 0:5] = 1.5

    # 4 quadrants (100m x 100m each) with increasing flood extent & claims:
    # Tract 1 (top-left): 0 exposed buildings, 1 claim
    # Tract 2 (top-right): 1 exposed building, 3 claims
    # Tract 3 (bottom-left): 2 exposed buildings, 6 claims
    # Tract 4 (bottom-right): 3 exposed buildings, 10 claims
    depth_m[2:4, 12:14] = 0.25  # Tract 2 flooded patch
    depth_m[12:18, 2:6] = 0.30  # Tract 3 flooded patch
    depth_m[11:19, 11:19] = 0.40  # Tract 4 flooded patch

    tracts_gdf = gpd.GeoDataFrame(
        [
            {
                "census_tract": "06075010100",
                "claim_count": 1,
                "total_paid_usd": 5000.0,
                "geometry": box(xmin, ymin + 100.0, xmin + 100.0, ymin + 200.0),
            },
            {
                "census_tract": "06075010200",
                "claim_count": 3,
                "total_paid_usd": 15000.0,
                "geometry": box(xmin + 100.0, ymin + 100.0, xmin + 200.0, ymin + 200.0),
            },
            {
                "census_tract": "06075010300",
                "claim_count": 6,
                "total_paid_usd": 30000.0,
                "geometry": box(xmin, ymin, xmin + 100.0, ymin + 100.0),
            },
            {
                "census_tract": "06075010400",
                "claim_count": 10,
                "total_paid_usd": 60000.0,
                "geometry": box(xmin + 100.0, ymin, xmin + 200.0, ymin + 100.0),
            },
        ],
        crs="EPSG:26910",
    )

    bldgs = [
        # Tract 1: 1 building in open water (excluded) + 1 dry building
        box(xmin + 10.0, ymin + 160.0, xmin + 30.0, ymin + 180.0),
        box(xmin + 60.0, ymin + 120.0, xmin + 80.0, ymin + 140.0),
        # Tract 2: 1 flooded building
        box(xmin + 125.0, ymin + 165.0, xmin + 135.0, ymin + 175.0),
        # Tract 3: 2 flooded buildings
        box(xmin + 25.0, ymin + 30.0, xmin + 35.0, ymin + 40.0),
        box(xmin + 45.0, ymin + 50.0, xmin + 55.0, ymin + 60.0),
        # Tract 4: 3 flooded buildings
        box(xmin + 115.0, ymin + 15.0, xmin + 125.0, ymin + 25.0),
        box(xmin + 140.0, ymin + 40.0, xmin + 150.0, ymin + 50.0),
        box(xmin + 165.0, ymin + 65.0, xmin + 175.0, ymin + 75.0),
    ]
    bldgs_gdf = gpd.GeoDataFrame(
        [{"id": i, "geometry": g} for i, g in enumerate(bldgs)],
        crs="EPSG:26910",
    )

    tract_df = _VAL.compute_tract_building_exposure(
        tracts_gdf_utm=tracts_gdf,
        buildings_gdf_utm=bldgs_gdf,
        modeled_depth_m=depth_m,
        valid_land_mask=valid_land,
        transform=tf,
        depth_threshold_m=0.05,
    )
    assert list(tract_df["exposed_buildings"]) == [0, 1, 2, 3]
    assert list(tract_df["total_buildings"]) == [2, 1, 2, 3]

    corr = _VAL.compute_nfip_spearman_correlation(tract_df)
    assert corr["n_tracts"] == 4
    assert corr["n_tracts_with_claims"] == 4
    assert corr["total_claims"] == 20
    assert corr["primary_exposure_metric"] == "exposed_buildings"
    assert math.isclose(corr["spearman_rho"], 1.0, abs_tol=1e-6)
    assert corr["p_value"] is not None and corr["p_value"] < 0.01

    # Fallback to flooded_land_area_m2 when no buildings are provided
    tract_no_bldg = _VAL.compute_tract_building_exposure(
        tracts_gdf_utm=tracts_gdf,
        buildings_gdf_utm=None,
        modeled_depth_m=depth_m,
        valid_land_mask=valid_land,
        transform=tf,
        depth_threshold_m=0.05,
    )
    corr_land = _VAL.compute_nfip_spearman_correlation(tract_no_bldg)
    assert corr_land["primary_exposure_metric"] == "flooded_land_area_m2"
    assert math.isclose(corr_land["spearman_rho"], 1.0, abs_tol=1e-6)

    # Constant claims -> graceful None
    const_df = pd.DataFrame({"claim_count": [2, 2, 2], "exposed_buildings": [1, 2, 3]})
    corr_const = _VAL.compute_nfip_spearman_correlation(const_df)
    assert corr_const["spearman_rho"] is None
    assert corr_const["p_value"] is None


def test_validate_region_wires_tier_e_nfip_end_to_end(tmp_path: Path) -> None:
    xmin, ymin, xmax, ymax = 550000.0, 4176000.0, 550200.0, 4176200.0
    tf = from_origin(xmin, ymax, 2.0, 2.0)

    dem_path = tmp_path / "hydro_dem.tif"
    dem = np.full((100, 100), 10.0, dtype=np.float32)
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(dem, 1)

    nlcd_path = tmp_path / "nlcd.tif"
    nlcd = np.full((100, 100), 23, dtype=np.uint8)
    with rasterio.open(
        nlcd_path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="uint8",
        crs="EPSG:26910",
        transform=tf,
        nodata=0,
    ) as dst:
        dst.write(nlcd, 1)

    ref_path = tmp_path / "sfpuc.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "geometry": box(xmin + 20.0, ymin + 20.0, xmin + 180.0, ymin + 180.0),
            }
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326").to_file(ref_path, driver="GPKG")

    pts_311_path = tmp_path / "pts_311.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "service_request_id": "1",
                "service_subtype": "Flooding",
                "geometry": box(
                    xmin + 40.0, ymin + 40.0, xmin + 41.0, ymin + 41.0
                ).centroid,
            },
            {
                "service_request_id": "2",
                "service_subtype": "Flooding",
                "geometry": box(
                    xmin + 140.0, ymin + 140.0, xmin + 141.0, ymin + 141.0
                ).centroid,
            },
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326").to_file(pts_311_path, driver="GPKG")

    bldgs_path = tmp_path / "buildings.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "geometry": box(xmin + 110.0, ymin + 110.0, xmin + 120.0, ymin + 120.0),
            },
            {
                "id": 2,
                "geometry": box(xmin + 130.0, ymin + 130.0, xmin + 140.0, ymin + 140.0),
            },
            {
                "id": 3,
                "geometry": box(xmin + 150.0, ymin + 150.0, xmin + 160.0, ymin + 160.0),
            },
            {
                "id": 4,
                "geometry": box(xmin + 110.0, ymin + 20.0, xmin + 120.0, ymin + 30.0),
            },
        ],
        crs="EPSG:26910",
    ).to_file(bldgs_path, driver="GPKG")

    nfip_path = tmp_path / "nfip_tracts.gpkg"
    gpd.GeoDataFrame(
        [
            {
                "census_tract": "06075010100",
                "claim_count": 0,
                "paid_claim_count": 0,
                "paid_building_usd": 0.0,
                "paid_contents_usd": 0.0,
                "total_paid_usd": 0.0,
                "geometry": box(xmin, ymin + 100.0, xmin + 100.0, ymax),
            },
            {
                "census_tract": "06075010200",
                "claim_count": 2,
                "paid_claim_count": 2,
                "paid_building_usd": 10000.0,
                "paid_contents_usd": 0.0,
                "total_paid_usd": 10000.0,
                "geometry": box(xmin + 100.0, ymin, xmax, ymin + 100.0),
            },
            {
                "census_tract": "06075010300",
                "claim_count": 8,
                "paid_claim_count": 6,
                "paid_building_usd": 50000.0,
                "paid_contents_usd": 5000.0,
                "total_paid_usd": 55000.0,
                "geometry": box(xmin + 100.0, ymin + 100.0, xmax, ymax),
            },
        ],
        crs="EPSG:26910",
    ).to_file(nfip_path, driver="GPKG")

    runs_dir = tmp_path / "runs"
    for scen in ("synth_100yr_slr0ft", "synth_ar_20221231"):
        run_dir = runs_dir / scen
        run_dir.mkdir(parents=True, exist_ok=True)
        xs = np.arange(550010.0, 550200.0, 20.0)
        ys = np.arange(4176190.0, 4176000.0, -20.0)
        zsmax = np.full((1, len(ys), len(xs)), 10.0, dtype=np.float32)
        # Flood top-right quadrant (tract 010300, 3 buildings) and part of bottom-right (tract 010200, 1 building)
        zsmax[0, 5:10, 5:10] = 10.40
        zsmax[0, 0:3, 5:7] = 10.25
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
            "buildings_source": str(bldgs_path),
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
                }
            ],
            "sources": {
                "nfip_claims_file": str(nfip_path),
            },
        },
    }
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    _VAL.validate_region(cfg_path)
    payload = json.loads(ensemble_json.read_text(encoding="utf-8"))

    assert payload["n_events"] == 1
    assert payload["total_311_points"] == 2
    nfip_res = payload["regional_crosschecks"]["nfip_tract_spearman_rho"]
    assert nfip_res is not None
    assert nfip_res["n_tracts"] == 3
    assert nfip_res["total_claims"] == 10
    assert math.isclose(nfip_res["spearman_rho"], 1.0, abs_tol=1e-6)
