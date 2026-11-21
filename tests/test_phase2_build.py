"""Offline unit tests for Phase 2 SFINCS model build helpers and config."""

from __future__ import annotations

import importlib.util
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "configs" / "sf_benchmark.yml"
BUILD_SCRIPT_PATH = REPO_ROOT / "pipeline" / "02_build_sfincs.py"


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "phase2_build_sfincs", BUILD_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_mod = _load_build_module()


def test_resolve_scenarios() -> None:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    default_list = build_mod.resolve_scenarios(cfg)
    assert len(default_list) == 1
    assert default_list[0]["id"] == "sf_100yr_slr0ft"

    explicit = build_mod.resolve_scenarios(cfg, scenario_id="sf_10yr_slr0ft")
    assert len(explicit) == 1
    assert explicit[0]["rain"] == "rain_10yr"

    all_sc = build_mod.resolve_scenarios(cfg, run_all=True)
    assert len(all_sc) == 8

    with pytest.raises(ValueError):
        build_mod.resolve_scenarios(cfg, scenario_id="nonexistent_scenario")


def test_compute_sfincs_grid_spec() -> None:
    spec = build_mod.compute_sfincs_grid_spec(
        bbox_utm=[550000.0, 4175800.0, 554200.0, 4180600.0],
        grid_resolution_m=10.0,
        subgrid_resolution_m=1.0,
        project_crs="EPSG:26910",
    )
    assert spec["x0"] == 550000.0
    assert spec["y0"] == 4175800.0
    assert spec["mmax"] == 420
    assert spec["nmax"] == 480
    assert spec["epsg"] == 26910
    assert spec["nr_subgrid_pixels"] == 10

    with pytest.raises(ValueError):
        build_mod.compute_sfincs_grid_spec(
            bbox_utm=[550000.0, 4175800.0, 554200.0, 4180600.0],
            grid_resolution_m=10.0,
            subgrid_resolution_m=1.0,
            project_crs="EPSG:3857",
        )

    with pytest.raises(ValueError):
        build_mod.compute_sfincs_grid_spec(
            bbox_utm=[550000.0, 4175800.0, 554205.0, 4180600.0],
            grid_resolution_m=10.0,
            subgrid_resolution_m=1.0,
            project_crs="EPSG:26910",
        )


def test_apply_urban_drainage_subtraction_and_build_precip_series() -> None:
    rows = [
        {"time_min": 0.0, "depth_mm": 1.0, "intensity_mm_hr": 4.0},
        {"time_min": 705.0, "depth_mm": 16.7, "intensity_mm_hr": 66.8},
        {"time_min": 1425.0, "depth_mm": 0.5, "intensity_mm_hr": 2.0},
    ]
    net = build_mod.apply_urban_drainage_subtraction(rows, drainage_rate_mm_hr=15.0)
    assert net[0] == (0.0, 0.0)
    assert math.isclose(net[1][1], 51.8, rel_tol=1e-12)
    assert net[2] == (1425.0, 0.0)

    series = build_mod.build_precip_series(
        hyetograph_rows=rows,
        reference_time_utc="2020-01-01T00:00:00",
        duration_hr=24.0,
        drainage_rate_mm_hr=15.0,
    )
    assert isinstance(series, pd.Series)
    assert series.name == "precip"
    assert series.index.name == "time"
    assert series.index[0] == datetime(2020, 1, 1, 0, 0, 0)
    assert series.index[-1] == datetime(2020, 1, 2, 0, 0, 0)
    assert float(series.iloc[-1]) == 0.0
    assert math.isclose(float(series.max()), 51.8, rel_tol=1e-12)

    with pytest.raises(ValueError):
        build_mod.apply_urban_drainage_subtraction(rows, drainage_rate_mm_hr=-1.0)


def test_build_docker_run_command_and_config_schema(tmp_path: Path) -> None:
    cmd = build_mod.build_docker_run_command(tmp_path, "deltares/sfincs-cpu:latest")
    assert cmd == [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{tmp_path.resolve()}:/data",
        "deltares/sfincs-cpu:latest",
    ]

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sfincs_cfg = cfg["sfincs_model"]
    assert sfincs_cfg["docker_image"] == "deltares/sfincs-cpu:latest"
    assert sfincs_cfg["drainage_rate_mm_hr"] == 5.0
    assert sfincs_cfg["dtout_s"] == 3600.0
    assert sfincs_cfg["dtmaxout_s"] == 86400.0
    assert sfincs_cfg["outflow_perimeter_bounds"] is True
