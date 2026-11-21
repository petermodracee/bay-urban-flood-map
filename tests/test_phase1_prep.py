"""Offline unit tests for Phase 1 terrain hydro-enforcement and infiltration prep."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import yaml
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon

REPO_ROOT = Path(__file__).resolve().parent.parent
PREP_SCRIPT_PATH = REPO_ROOT / "pipeline" / "01_prep_terrain.py"


def _load_prep_module():
    spec = importlib.util.spec_from_file_location(
        "phase1_prep_terrain", PREP_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep_mod = _load_prep_module()


def test_parse_hsg_code_and_target_grid() -> None:
    assert prep_mod.parse_hsg_code("A") == 1
    assert prep_mod.parse_hsg_code("B/D") == 4
    assert prep_mod.parse_hsg_code(" c ") == 3
    assert prep_mod.parse_hsg_code(None) == 0
    assert prep_mod.parse_hsg_code(float("nan")) == 0

    height, width, transform = prep_mod.compute_target_grid(
        [550000.0, 4175800.0, 554200.0, 4180600.0], 1.0
    )
    assert (height, width) == (4800, 4200)
    assert transform == from_origin(550000.0, 4180600.0, 1.0, 1.0)

    with pytest.raises(ValueError):
        prep_mod.compute_target_grid([0.0, 0.0, 10.3, 10.0], 1.0)


def test_identify_and_burn_underpasses_downward_only() -> None:
    # 40x40 m synthetic grid at 1 m resolution
    xmin, ymax = 550000.0, 4176040.0
    transform = from_origin(xmin, ymax, 1.0, 1.0)
    dem = np.full((40, 40), 10.0, dtype=np.float32)

    # Artificial 2 m highway dam across rows 17..22 on the street crossing at x=550020
    dem[17:23, :] = 12.0
    # Keep one NoData pixel inside the bridge crossing to ensure NoData is never overwritten
    dem[20, 20] = -9999.0

    # Elevated freeway bridge running E-W at y=4176020 (row 20)
    # Surface street 1 crosses N-S at x=550020 (col 20)
    # Surface street 2 crosses N-S at x=550032 (col 32), where DEM is flat (carved below)
    dem[17:23, 29:36] = 10.0
    roads = gpd.GeoDataFrame(
        [
            {
                "highway": "motorway",
                "railway": None,
                "bridge": "yes",
                "tunnel": None,
                "layer": "1",
                "name": "US 101",
                "geometry": LineString([(550002.0, 4176020.0), (550038.0, 4176020.0)]),
            },
            {
                "highway": "primary",
                "railway": None,
                "bridge": None,
                "tunnel": None,
                "layer": None,
                "name": "Blocked Ave",
                "geometry": LineString([(550020.0, 4176002.0), (550020.0, 4176038.0)]),
            },
            {
                "highway": "residential",
                "railway": None,
                "bridge": None,
                "tunnel": None,
                "layer": None,
                "name": "Flat St",
                "geometry": LineString([(550032.0, 4176002.0), (550032.0, 4176038.0)]),
            },
        ],
        crs="EPSG:26910",
    )

    corridors = prep_mod.identify_crossing_corridors(
        roads_gdf_utm=roads,
        bridge_buffer_m=4.0,
        approach_sample_m=4.0,
    )
    assert len(corridors) == 2

    burned, stats = prep_mod.burn_underpasses_into_dem(
        dem_m=dem,
        transform=transform,
        nodata_value=-9999.0,
        corridors=corridors,
        carve_half_width_m=2.0,
        min_bump_m=0.15,
    )
    assert stats["corridors_evaluated"] == 2
    assert stats["corridors_burned"] == 1
    assert stats["pixels_modified"] > 0
    assert math.isclose(float(stats["max_carve_depth_m"]), 2.0, abs_tol=1e-4)

    # Dam at Blocked Ave (row 20, col 19) should now be carved to ~10.0 m
    assert math.isclose(float(burned[20, 19]), 10.0, abs_tol=1e-4)
    # NoData pixel preserved
    assert float(burned[20, 20]) == -9999.0
    # Flat St untouched
    assert np.allclose(burned[:, 32], 10.0)


def test_apply_building_footprints_modes() -> None:
    dem = np.full((6, 6), 12.0, dtype=np.float32)
    dem[0, 0] = -9999.0
    manning = np.full((6, 6), 0.020, dtype=np.float32)
    bldg_mask = np.zeros((6, 6), dtype=bool)
    bldg_mask[0, 0] = True
    bldg_mask[2:4, 2:4] = True

    dem_bump, man_bump = prep_mod.apply_building_footprints(
        dem, manning, bldg_mask, "elevation_bump", 3.0, 0.30, -9999.0
    )
    assert float(dem_bump[0, 0]) == -9999.0
    assert float(dem_bump[2, 2]) == 15.0
    assert float(dem_bump[1, 1]) == 12.0
    assert math.isclose(float(man_bump[2, 2]), 0.30, abs_tol=1e-6)
    assert math.isclose(float(man_bump[0, 0]), 0.020, abs_tol=1e-6)

    dem_hi, man_hi = prep_mod.apply_building_footprints(
        dem, manning, bldg_mask, "high_manning", 3.0, 0.30, -9999.0
    )
    assert float(dem_hi[2, 2]) == 12.0
    assert math.isclose(float(man_hi[2, 2]), 0.30, abs_tol=1e-6)

    with pytest.raises(ValueError):
        prep_mod.apply_building_footprints(
            dem, manning, bldg_mask, "invalid", 3.0, 0.30, -9999.0
        )


def test_compute_curve_number_and_manning_grids() -> None:
    # 2x3 grid covering water, urban HSG A vs D, 100% impervious, vegetation, and NoData
    nlcd_lc = np.array([[11, 21, 21], [24, 42, 21]], dtype=np.uint8)
    nlcd_imp = np.array([[0, 0, 0], [100, 0, 0]], dtype=np.uint8)
    hsg = np.array([[4, 1, 4], [4, 4, 0]], dtype=np.uint8)
    valid = np.array([[True, True, True], [True, True, False]], dtype=bool)

    cn = prep_mod.compute_curve_number_grid(
        nlcd_landcover=nlcd_lc,
        nlcd_impervious_pct=nlcd_imp,
        hsg_code=hsg,
        valid_mask=valid,
        default_urban_hsg="D",
        nodata_value=-9999.0,
    )
    assert float(cn[0, 0]) == 100.0  # Open water
    assert float(cn[0, 1]) == 49.0  # Urban open space HSG A
    assert float(cn[0, 2]) == 84.0  # Urban open space HSG D
    assert cn[0, 1] < cn[0, 2]
    assert float(cn[1, 0]) == 98.0  # 100% impervious
    assert float(cn[1, 1]) == 77.0  # Evergreen forest HSG D
    assert float(cn[1, 2]) == -9999.0

    # Verify unmapped HSG (0) falls back to default_urban_hsg ("D")
    cn_unmapped = prep_mod.compute_curve_number_grid(
        nlcd_landcover=np.array([[21]], dtype=np.uint8),
        nlcd_impervious_pct=np.array([[0]], dtype=np.uint8),
        hsg_code=np.array([[0]], dtype=np.uint8),
        valid_mask=np.array([[True]], dtype=bool),
        default_urban_hsg="D",
    )
    assert float(cn_unmapped[0, 0]) == 84.0

    manning = prep_mod.compute_manning_n_grid(
        nlcd_landcover=nlcd_lc,
        nlcd_impervious_pct=nlcd_imp,
        valid_mask=valid,
        manning_n_paved=0.020,
        manning_n_water=0.020,
        manning_n_open_space=0.035,
        manning_n_vegetation=0.060,
        nodata_value=-9999.0,
    )
    assert math.isclose(float(manning[0, 0]), 0.020, abs_tol=1e-6)
    assert math.isclose(float(manning[0, 1]), 0.035, abs_tol=1e-6)
    assert math.isclose(float(manning[1, 0]), 0.020, abs_tol=1e-6)
    assert math.isclose(float(manning[1, 1]), 0.060, abs_tol=1e-6)
    assert float(manning[1, 2]) == -9999.0


def test_prep_terrain_end_to_end_synthetic(tmp_path: Path) -> None:
    xmin, ymin, xmax, ymax = 550000.0, 4175800.0, 550040.0, 4175840.0
    tf = from_origin(xmin, ymax, 1.0, 1.0)

    dem_path = tmp_path / "dem.tif"
    lc_path = tmp_path / "lc.tif"
    imp_path = tmp_path / "imp.tif"
    hsg_path = tmp_path / "hsg.gpkg"
    roads_path = tmp_path / "roads.gpkg"
    bldgs_path = tmp_path / "bldgs.gpkg"

    dem_arr = np.full((40, 40), 10.0, dtype=np.float32)
    dem_arr[18:22, :] = 12.0  # Dam across N-S street at x=550020
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="float32",
        crs="EPSG:26910",
        transform=tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(dem_arr, 1)

    with rasterio.open(
        lc_path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="uint8",
        crs="EPSG:26910",
        transform=tf,
        nodata=0,
    ) as dst:
        dst.write(np.full((40, 40), 24, dtype=np.uint8), 1)

    # Set nodata=0 as MRLC does so 0% areas also stay valid
    with rasterio.open(
        imp_path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="uint8",
        crs="EPSG:26910",
        transform=tf,
        nodata=0,
    ) as dst:
        imp_arr = np.full((40, 40), 80, dtype=np.uint8)
        imp_arr[0:5, 0:5] = 0
        dst.write(imp_arr, 1)

    # Vector layers in EPSG:4326 to exercise reprojection into EPSG:26910
    hsg_gdf = gpd.GeoDataFrame(
        [
            {
                "hydgrpdcd": "D",
                "geometry": Polygon(
                    [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
                ),
            }
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326")
    hsg_gdf.to_file(hsg_path, driver="GPKG")

    roads_gdf = gpd.GeoDataFrame(
        [
            {
                "highway": "motorway",
                "railway": None,
                "bridge": "yes",
                "tunnel": None,
                "layer": "1",
                "name": "Bridge",
                "geometry": LineString(
                    [(xmin + 2.0, ymin + 20.0), (xmax - 2.0, ymin + 20.0)]
                ),
            },
            {
                "highway": "secondary",
                "railway": None,
                "bridge": None,
                "tunnel": None,
                "layer": None,
                "name": "Street",
                "geometry": LineString(
                    [(xmin + 20.0, ymin + 2.0), (xmin + 20.0, ymax - 2.0)]
                ),
            },
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326")
    roads_gdf.to_file(roads_path, driver="GPKG")

    bldgs_gdf = gpd.GeoDataFrame(
        [
            {
                "building": "yes",
                "geometry": Polygon(
                    [
                        (xmin + 4.0, ymin + 4.0),
                        (xmin + 8.0, ymin + 4.0),
                        (xmin + 8.0, ymin + 8.0),
                        (xmin + 4.0, ymin + 8.0),
                    ]
                ),
            }
        ],
        crs="EPSG:26910",
    ).to_crs("EPSG:4326")
    bldgs_gdf.to_file(bldgs_path, driver="GPKG")

    hydro_out = tmp_path / "out" / "hydro_dem.tif"
    manning_out = tmp_path / "out" / "manning.tif"
    cn_out = tmp_path / "out" / "cn.tif"

    cfg = {
        "project": {"name": "synth", "crs": "EPSG:26910"},
        "domain": {
            "bbox_epsg26910": [xmin, ymin, xmax, ymax],
            "subgrid_resolution_m": 1.0,
            "dem_source": str(dem_path),
            "nlcd_landcover_source": str(lc_path),
            "nlcd_impervious_source": str(imp_path),
            "hsg_soils_source": str(hsg_path),
            "roads_rail_source": str(roads_path),
            "buildings_source": str(bldgs_path),
        },
        "terrain_prep": {
            "hydro_dem_file": str(hydro_out),
            "manning_n_file": str(manning_out),
            "curve_number_file": str(cn_out),
            "bridge_buffer_m": 4.0,
            "approach_sample_m": 4.0,
            "carve_half_width_m": 2.0,
            "min_bump_m": 0.15,
            "building_handling": "elevation_bump",
            "building_elevation_bump_m": 3.0,
            "building_manning_n": 0.30,
            "manning_n_paved": 0.020,
            "manning_n_water": 0.020,
            "manning_n_open_space": 0.035,
            "manning_n_vegetation": 0.060,
            "default_urban_hsg": "D",
            "nodata_value": -9999.0,
        },
    }
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    prep_mod.prep_terrain(cfg_path, force=False)
    for out_file in (hydro_out, manning_out, cn_out):
        assert out_file.exists()
        with rasterio.open(out_file) as ds:
            assert ds.crs == CRS.from_epsg(26910)
            assert (ds.height, ds.width) == (40, 40)

    with rasterio.open(hydro_out) as ds:
        arr = ds.read(1)
        assert float(arr[20, 20]) < 10.5  # Underpass burned down
        assert (
            float(arr[34, 6]) == 13.0
        )  # Building bumped +3 m (y=4175806 => row 34, col 6)

    with rasterio.open(cn_out) as ds:
        cn_arr = ds.read(1)
        assert np.all((cn_arr >= 30.0) & (cn_arr <= 100.0))
        # Check 0% impervious corner did not become NoData and evaluated to HSG D open space (84)
        assert math.isclose(float(cn_arr[0, 0]), 84.0, abs_tol=1e-4)

    # Idempotent skip without --force preserves existing file mtime
    mtime_before = hydro_out.stat().st_mtime_ns
    prep_mod.prep_terrain(cfg_path, force=False)
    assert hydro_out.stat().st_mtime_ns == mtime_before
