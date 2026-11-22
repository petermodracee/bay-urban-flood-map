"""Offline unit tests for v0.2 Phase 1 shoreline structure crest enforcement."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point

REPO_ROOT = Path(__file__).resolve().parent.parent
PREP_SCRIPT_PATH = REPO_ROOT / "pipeline" / "01_prep_terrain.py"


def _load_prep_module():
    spec = importlib.util.spec_from_file_location(
        "phase1_prep_terrain_v02", PREP_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep_mod = _load_prep_module()


def test_enforce_shoreline_crests_upward_only_and_class_filtering() -> None:
    xmin, ymax = 550000.0, 4180020.0
    tf = from_origin(xmin, ymax, 1.0, 1.0)
    dem = np.full((20, 20), 2.0, dtype=np.float32)
    dem[5, 5] = -9999.0  # Keep NoData untouched
    dem[5, 10] = 5.0  # Keep higher ground untouched

    structures = gpd.GeoDataFrame(
        [
            {
                "Class": "Shoreline Protection Structure",
                "Z_Mean": 3.5,
                "geometry": LineString(
                    [(xmin + 2.0, ymax - 5.5), (xmin + 18.0, ymax - 5.5)]
                ),
            },
            {
                "Class": "Channel or Opening",
                "Z_Mean": 8.0,
                "geometry": LineString(
                    [(xmin + 2.0, ymax - 12.5), (xmin + 18.0, ymax - 12.5)]
                ),
            },
            {
                "Class": "Natural Shoreline",
                "Z_Mean": 8.0,
                "geometry": LineString(
                    [(xmin + 2.0, ymax - 16.5), (xmin + 18.0, ymax - 16.5)]
                ),
            },
        ],
        crs="EPSG:26910",
    )

    out_dem, stats = prep_mod.enforce_shoreline_crests(
        dem_m=dem,
        transform=tf,
        nodata_value=-9999.0,
        structures_gdf_utm=structures,
        elevation_col="Z_Mean",
        half_width_m=1.0,
    )
    assert stats["structures_used"] == 1
    assert stats["pixels_raised"] > 0
    assert math.isclose(float(stats["max_raise_m"]), 1.5, abs_tol=1e-5)

    # Raised along seawall line
    assert math.isclose(float(out_dem[5, 7]), 3.5, abs_tol=1e-5)
    # NoData and higher cell preserved
    assert float(out_dem[5, 5]) == -9999.0
    assert float(out_dem[5, 10]) == 5.0
    # Channel opening and natural shoreline ignored
    assert float(out_dem[12, 10]) == 2.0
    assert float(out_dem[16, 10]) == 2.0


def test_burn_underpasses_skips_bored_hillside_tunnel_above_max_bump():
    transform = from_origin(0.0, 40.0, 2.0, 2.0)  # 20x20 grid, 2 m resolution
    dem = np.full((20, 20), 10.0, dtype=np.float32)

    # Row 4 (y ~ 31): shallow 2.0 m bridge/overpass dam across flat street
    dem[3:6, 8:12] = 12.0
    overpass_corridor = (
        LineString([(10.0, 31.0), (26.0, 31.0)]),
        Point(10.0, 31.0),
        Point(26.0, 31.0),
        "bridge_dam",
    )

    # Row 14 (y ~ 11): 20.0 m natural hillside over a bored tunnel
    dem[13:16, 8:12] = 30.0
    tunnel_corridor = (
        LineString([(10.0, 11.0), (26.0, 11.0)]),
        Point(10.0, 11.0),
        Point(26.0, 11.0),
        "bored_tunnel",
    )

    out_dem, stats = prep_mod.burn_underpasses_into_dem(
        dem_m=dem,
        transform=transform,
        nodata_value=-9999.0,
        corridors=[overpass_corridor, tunnel_corridor],
        carve_half_width_m=2.0,
        min_bump_m=0.15,
        max_bump_m=6.0,
    )

    assert stats["corridors_evaluated"] == 2
    assert stats["corridors_burned"] == 1
    assert stats["corridors_skipped_max_bump"] == 1
    assert math.isclose(float(stats["max_carve_depth_m"]), 2.0, abs_tol=1e-5)

    # Shallow overpass carved to 10.0 m; deep hillside tunnel untouched at 30.0 m
    assert math.isclose(float(out_dem[4, 10]), 10.0, abs_tol=1e-5)
    assert math.isclose(float(out_dem[14, 10]), 30.0, abs_tol=1e-5)
