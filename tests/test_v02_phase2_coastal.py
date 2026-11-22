"""Offline unit tests for v0.2 Phase 2A coastal tidal forcing helpers."""

from __future__ import annotations

import importlib.util
import math
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT_PATH = REPO_ROOT / "pipeline" / "02_build_sfincs.py"


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "phase2_coastal_sfincs", BUILD_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_mod = _load_build_module()


def test_build_waterlevel_forcing_inputs_and_tstop_padding() -> None:
    rows = [
        {"time_min": 0.0, "water_level_m": 1.25},
        {"time_min": 60.0, "water_level_m": 1.85},
        {"time_min": 2820.0, "water_level_m": 0.40},
    ]
    bzs_df, bnd_gdf = build_mod.build_waterlevel_forcing_inputs(
        tidal_rows=rows,
        reference_time_utc="2022-12-31T00:00:00+00:00",
        duration_hr=48.0,
        location_xy=(549100.0, 4179200.0),
        project_crs="EPSG:26910",
    )

    assert isinstance(bzs_df, pd.DataFrame)
    assert list(bzs_df.columns) == [1]
    assert len(bzs_df) == 4
    assert bzs_df.index[0].to_pydatetime() == datetime(2022, 12, 31, 0, 0)
    assert bzs_df.index[-1].to_pydatetime() == datetime(2023, 1, 2, 0, 0)
    assert math.isclose(float(bzs_df.iloc[-1, 0]), 0.40, abs_tol=1e-9)

    assert isinstance(bnd_gdf, gpd.GeoDataFrame)
    assert str(bnd_gdf.crs) == "EPSG:26910"
    assert list(bnd_gdf["index"]) == [1]
    assert math.isclose(bnd_gdf.geometry.iloc[0].x, 549100.0, abs_tol=1e-6)
    assert math.isclose(bnd_gdf.geometry.iloc[0].y, 4179200.0, abs_tol=1e-6)

    with pytest.raises(ValueError):
        build_mod.build_waterlevel_forcing_inputs(
            tidal_rows=[],
            reference_time_utc="2022-12-31T00:00:00",
            duration_hr=24.0,
            location_xy=(549100.0, 4179200.0),
            project_crs="EPSG:26910",
        )


def test_resolve_reference_time_utc_precedence() -> None:
    sfincs_cfg = {"reference_time_utc": "2020-01-01T00:00:00"}
    rain_cfg = {"reference_time_utc": "2021-05-01T00:00:00"}
    slr_cfg = {"reference_time_utc": "2022-12-31T00:00:00+00:00"}

    assert build_mod.resolve_reference_time_utc(
        sfincs_cfg, rain_cfg, slr_cfg
    ) == datetime(2022, 12, 31, 0, 0)
    assert build_mod.resolve_reference_time_utc(sfincs_cfg, rain_cfg, {}) == datetime(
        2021, 5, 1, 0, 0
    )
    assert build_mod.resolve_reference_time_utc(sfincs_cfg, {}, {}) == datetime(
        2020, 1, 1, 0, 0
    )


def test_read_tidal_hydrograph_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "tide.csv"
    csv_path.write_text(
        "time_min,water_level_m\n0.0,1.10\n60.0,1.95\n",
        encoding="utf-8",
    )
    rows = build_mod._read_tidal_hydrograph_csv(csv_path)
    assert len(rows) == 2
    assert math.isclose(rows[0]["time_min"], 0.0)
    assert math.isclose(rows[1]["water_level_m"], 1.95)


def test_write_sfincs_waterlevel_files(tmp_path: Path) -> None:
    rows = [
        {"time_min": 0.0, "water_level_m": 1.25},
        {"time_min": 60.0, "water_level_m": 1.85},
    ]
    tref = datetime(2022, 12, 31, 0, 0)
    bzs_df, bnd_gdf = build_mod.build_waterlevel_forcing_inputs(
        tidal_rows=rows,
        reference_time_utc=tref,
        duration_hr=2.0,
        location_xy=(549100.0, 4179200.0),
        project_crs="EPSG:26910",
    )
    bnd_path, bzs_path = build_mod.write_sfincs_waterlevel_files(
        run_dir=tmp_path,
        bzs_df=bzs_df,
        bnd_gdf=bnd_gdf,
        tref=tref,
    )

    assert bnd_path.exists()
    assert bzs_path.exists()
    assert bnd_path.read_text(encoding="utf-8").strip() == "549100.0 4179200.0"

    bzs_lines = bzs_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(bzs_lines) == 3  # 0.0, 3600.0, and tstop padding at 7200.0
    assert bzs_lines[0].split() == ["0.0", "1.250"]
    assert bzs_lines[1].split() == ["3600.0", "1.850"]
    assert bzs_lines[2].split() == ["7200.0", "1.850"]


def test_compute_valid_active_mask_orientation_and_nodata() -> None:
    dem = np.full((4, 4), -9999.0, dtype=np.float32)
    # Top-left 2x2 coarse block has valid data; bottom-right 2x2 block has one valid subgrid cell
    dem[0:2, 0:2] = 5.0
    dem[3, 3] = 2.5

    mask_desc = build_mod.compute_valid_active_mask(
        dem_2m=dem,
        nodata_value=-9999.0,
        nr_subgrid_pixels=2,
        y_ascending=False,
    )
    assert mask_desc.tolist() == [[True, False], [False, True]]

    mask_asc = build_mod.compute_valid_active_mask(
        dem_2m=dem,
        nodata_value=-9999.0,
        nr_subgrid_pixels=2,
        y_ascending=True,
    )
    assert mask_asc.tolist() == [[False, True], [True, False]]
