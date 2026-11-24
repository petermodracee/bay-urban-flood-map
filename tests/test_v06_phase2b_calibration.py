"""Offline unit tests for v0.6 Phase 2B physics calibration primitives."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FETCH_SCRIPT_PATH = REPO_ROOT / "pipeline" / "00_fetch_data.py"
BUILD_SCRIPT_PATH = REPO_ROOT / "pipeline" / "02_build_sfincs.py"
SF_CONFIG_PATH = REPO_ROOT / "configs" / "sf_citywide.yml"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_mod = _load_module(FETCH_SCRIPT_PATH, "phase0_fetch_v06_p2b")
build_mod = _load_module(BUILD_SCRIPT_PATH, "phase2_build_v06_p2b")


def test_compute_antecedent_precip_window() -> None:
    assert fetch_mod.compute_antecedent_precip_window("2021-10-24", 5) == (
        "2021-10-19",
        "2021-10-23",
    )
    assert fetch_mod.compute_antecedent_precip_window("2023-01-09", 5) == (
        "2023-01-04",
        "2023-01-08",
    )
    with pytest.raises(ValueError):
        fetch_mod.compute_antecedent_precip_window("2023-01-09", 0)


def test_apply_amc_iii_curve_number_neh630() -> None:
    cn_ii = np.array([[70.0, 85.0], [98.0, -9999.0]], dtype=np.float32)
    cn_iii = build_mod.apply_amc_iii_curve_number(cn_ii, nodata_value=-9999.0)

    expected_70 = (23.0 * 70.0) / (10.0 + 0.13 * 70.0)
    expected_85 = (23.0 * 85.0) / (10.0 + 0.13 * 85.0)
    expected_98 = (23.0 * 98.0) / (10.0 + 0.13 * 98.0)

    assert cn_iii[0, 0] == pytest.approx(expected_70, rel=1e-5)
    assert cn_iii[0, 1] == pytest.approx(expected_85, rel=1e-5)
    assert cn_iii[1, 0] == pytest.approx(expected_98, rel=1e-5)
    assert cn_iii[1, 1] == pytest.approx(-9999.0)


def test_select_amc_condition_threshold() -> None:
    assert build_mod.select_amc_condition(None) == "AMC-II"
    assert build_mod.select_amc_condition(28.0) == "AMC-II"
    assert build_mod.select_amc_condition(28.01) == "AMC-III"
    assert build_mod.select_amc_condition(45.2, threshold_mm=28.0) == "AMC-III"


def test_normalize_tier1a_mean_rate_matches_target_urban_mean() -> None:
    imp = np.array([[0.0, 50.0, 100.0], [20.0, 80.0, 0.0]], dtype=np.float32)
    lc = np.array([[11, 22, 24], [21, 23, 41]], dtype=np.int32)
    raw = build_mod.compute_tier1a_drainage_rate_grid(
        impervious_pct=imp,
        landcover=lc,
        base_rate_mm_hr=2.0,
        max_rate_mm_hr=10.0,
    )
    matched = build_mod.normalize_tier1a_mean_rate(
        drainage_1a_mm_hr=raw,
        landcover=lc,
        impervious_pct=imp,
        target_mean_mm_hr=5.0,
    )
    # Urban cells are (0,1), (0,2), (1,0), (1,1); (0,0) is water, (1,2) is forest
    assert matched[0, 0] == 0.0
    assert matched[1, 2] == 0.0
    urban_vals = np.array([matched[0, 1], matched[0, 2], matched[1, 0], matched[1, 1]])
    assert float(np.mean(urban_vals)) == pytest.approx(5.0, rel=1e-5)
    # Relative spatial ordering preserved
    assert matched[0, 2] > matched[1, 1] > matched[0, 1] > matched[1, 0]


def test_sf_citywide_config_includes_second_holdout_ar_20241122() -> None:
    cfg = yaml.safe_load(SF_CONFIG_PATH.read_text(encoding="utf-8"))
    assert "rain_ar_20241122" in cfg["rain_events"]
    assert "tide_ar_20241122" in cfg["slr_increments"]
    scenario_ids = {s["id"] for s in cfg["scenarios"]}
    assert "sf_citywide_ar_20241122" in scenario_ids
    ensemble_ids = {e["id"] for e in cfg["validation"]["hindcast_ensemble"]}
    assert "ar_20241122" in ensemble_ids
    # Phase 3 promoted validated ar_20241122 to public COG export; ar_20240204 remains excluded.
    assert set(cfg["export"]["holdout_storms"]) == {"ar_20240204"}
