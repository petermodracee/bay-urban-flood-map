"""Offline unit tests for v0.6 Phase 2A reproducible terrain QA controls."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import yaml
from rasterio.transform import from_origin
from shapely.geometry import LineString

REPO_ROOT = Path(__file__).resolve().parent.parent
PREP_SCRIPT_PATH = REPO_ROOT / "pipeline" / "01_prep_terrain.py"
SF_CONFIG_PATH = REPO_ROOT / "configs" / "sf_citywide.yml"


def _load_prep_module():
    spec = importlib.util.spec_from_file_location(
        "phase1_prep_terrain_v06_p2a", PREP_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep_mod = _load_prep_module()


def test_identify_crossing_corridors_respects_exclude_osm_ids() -> None:
    roads = gpd.GeoDataFrame(
        [
            {
                "osm_id": 1001,
                "highway": "motorway",
                "railway": None,
                "bridge": "yes",
                "tunnel": None,
                "layer": "1",
                "name": "US 101",
                "geometry": LineString([(550002.0, 4176020.0), (550038.0, 4176020.0)]),
            },
            {
                "osm_id": 2001,
                "highway": "primary",
                "railway": None,
                "bridge": None,
                "tunnel": None,
                "layer": None,
                "name": "Keep Ave",
                "geometry": LineString([(550015.0, 4176002.0), (550015.0, 4176038.0)]),
            },
            {
                "osm_id": 2002,
                "highway": "tertiary",
                "railway": None,
                "bridge": None,
                "tunnel": None,
                "layer": None,
                "name": "Exclude St",
                "geometry": LineString([(550030.0, 4176002.0), (550030.0, 4176038.0)]),
            },
        ],
        crs="EPSG:26910",
    )

    all_corridors = prep_mod.identify_crossing_corridors(
        roads_gdf_utm=roads,
        bridge_buffer_m=4.0,
        approach_sample_m=4.0,
    )
    assert len(all_corridors) == 2

    filtered_corridors = prep_mod.identify_crossing_corridors(
        roads_gdf_utm=roads,
        bridge_buffer_m=4.0,
        approach_sample_m=4.0,
        exclude_osm_ids=[2002],
    )
    assert len(filtered_corridors) == 1
    assert abs(filtered_corridors[0][0].centroid.x - 550015.0) < 1e-6


def test_build_manual_underpass_corridors_and_burn() -> None:
    xmin, ymax = 550000.0, 4176040.0
    transform = from_origin(xmin, ymax, 1.0, 1.0)
    dem = np.full((40, 40), 10.0, dtype=np.float32)
    dem[17:23, :] = 12.0  # 2 m artificial dam

    manual_corridors = prep_mod.build_manual_underpass_corridors(
        [
            {
                "name": "test_cut_utm",
                "coords_epsg26910": [[550020.0, 4176012.0], [550020.0, 4176028.0]],
            }
        ],
        target_crs="EPSG:26910",
    )
    assert len(manual_corridors) == 1
    assert manual_corridors[0][3] == "manual:test_cut_utm"

    burned, stats = prep_mod.burn_underpasses_into_dem(
        dem_m=dem,
        transform=transform,
        nodata_value=-9999.0,
        corridors=manual_corridors,
        carve_half_width_m=2.0,
        min_bump_m=0.15,
        max_bump_m=6.0,
    )
    assert stats["corridors_burned"] == 1
    assert burned[20, 20] < 10.5

    lonlat_corridors = prep_mod.build_manual_underpass_corridors(
        [
            {
                "name": "test_cut_lonlat",
                "coords_lonlat": [[-122.43, 37.75], [-122.429, 37.75]],
            }
        ],
        target_crs="EPSG:26910",
    )
    assert len(lonlat_corridors) == 1
    assert lonlat_corridors[0][0].length > 50.0

    with pytest.raises(ValueError):
        prep_mod.build_manual_underpass_corridors([{"name": "bad"}], "EPSG:26910")


def test_sf_citywide_config_has_phase2a_terrain_controls() -> None:
    with SF_CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tprep = cfg["terrain_prep"]
    assert "carve_exclude_osm_ids" in tprep
    assert isinstance(tprep["carve_exclude_osm_ids"], list)
    assert "manual_underpass_cuts" in tprep
    assert isinstance(tprep["manual_underpass_cuts"], list)
    assert "shoreline_crest_half_width_m" in tprep
