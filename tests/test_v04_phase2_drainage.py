"""Unit tests for v0.4 Phase 2 Tier 1a/1b 2D drainage and Tier 2 hook."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BUILD = _load_module("build_sfincs", "pipeline/02_build_sfincs.py")
_EXPORT = _load_module("export_cog", "pipeline/04_export_cog.py")


def test_compute_tier1a_drainage_rate_grid() -> None:
    imp = np.array([[0.0, 50.0, 100.0], [80.0, 0.0, 20.0]], dtype=np.float32)
    lc = np.array([[11, 22, 24], [41, 41, 21]], dtype=np.int32)
    rates = _BUILD.compute_tier1a_drainage_rate_grid(
        impervious_pct=imp,
        landcover=lc,
        base_rate_mm_hr=2.0,
        max_rate_mm_hr=10.0,
    )
    assert rates[0, 0] == pytest.approx(0.0)  # open water
    assert rates[0, 1] == pytest.approx(6.0)  # 50% imp -> 2 + 8*0.5
    assert rates[0, 2] == pytest.approx(10.0)  # 100% imp -> max
    assert rates[1, 0] == pytest.approx(8.4)  # imp > 0 -> urban
    assert rates[1, 1] == pytest.approx(0.0)  # pervious forest -> 0
    assert rates[1, 2] == pytest.approx(3.6)  # 20% imp -> 2 + 8*0.2


def test_compute_tier1b_effective_drainage_and_polder_mask() -> None:
    d1a = np.full((2, 2), 10.0, dtype=np.float32)
    dem = np.array([[0.5, 1.75], [2.5, 0.5]], dtype=np.float32)
    polder = np.array([[False, False], [False, True]], dtype=bool)
    eff = _BUILD.compute_tier1b_effective_drainage(
        drainage_1a_mm_hr=d1a,
        dem_m=dem,
        tide_m=1.0,
        head_full_m=1.5,
        polder_mask=polder,
    )
    assert eff[0, 0] == pytest.approx(0.0)  # sub-tide -> locked
    assert eff[0, 1] == pytest.approx(5.0)  # head=0.75 / 1.5 -> 0.5
    assert eff[1, 0] == pytest.approx(10.0)  # head=1.5 -> full
    assert eff[1, 1] == pytest.approx(10.0)  # polder_mask bypasses lock-up

    eff_3d = _BUILD.compute_tier1b_effective_drainage(
        drainage_1a_mm_hr=d1a,
        dem_m=dem,
        tide_m=np.array([0.0, 2.0], dtype=np.float32),
        head_full_m=1.5,
        polder_mask=polder,
    )
    assert eff_3d.shape == (2, 2, 2)
    assert eff_3d[0, 0, 0] == pytest.approx(10.0 / 3.0, rel=1e-4)
    assert eff_3d[1, 0, 0] == pytest.approx(0.0)
    assert eff_3d[1, 1, 1] == pytest.approx(10.0)


def test_build_gridded_net_precip_da_2d_and_3d() -> None:
    idx = pd.date_range("2020-01-01", periods=2, freq="1h")
    gross = pd.Series([5.0, 20.0], index=idx, name="precip")
    x = np.array([550020.0, 550060.0])
    y = np.array([4175020.0, 4175060.0])
    d2 = np.array([[2.0, 8.0], [10.0, 0.0]], dtype=np.float32)

    da2 = _BUILD.build_gridded_net_precip_da(gross, d2, x, y, "EPSG:26910")
    assert da2.dims == ("time", "y", "x")
    assert da2.rio.crs.to_string() == "EPSG:26910"
    assert da2.values[0, 0, 1] == pytest.approx(0.0)  # clamped >= 0
    assert da2.values[1, 0, 1] == pytest.approx(12.0)

    d3 = np.stack([d2, d2 * 0.5], axis=0)
    da3 = _BUILD.build_gridded_net_precip_da(gross, d3, x, y, "EPSG:26910")
    assert da3.values[1, 0, 1] == pytest.approx(16.0)

    with pytest.raises(ValueError, match="EPSG:26910"):
        _BUILD.build_gridded_net_precip_da(gross, d2, x, y, "EPSG:4326")


def test_build_manifest_drainage_tier_and_tier2_hook() -> None:
    m_legacy = _EXPORT.build_manifest([{"id": "s1"}], version="0.2")
    assert "drainage_tier" not in m_legacy
    m_v04 = _EXPORT.build_manifest(
        [{"id": "s1"}], version="0.4", drainage_tier="tier1a"
    )
    assert m_v04["drainage_tier"] == "tier1a"

    with pytest.raises(ValueError, match="storm_drain_source"):
        _BUILD._load_resampled_drainage_inputs(
            cfg={"domain": {"storm_drain_source": None}},
            bbox_epsg26910=[542600.0, 4173000.0, 542700.0, 4173100.0],
            grid_res_m=40.0,
            project_crs="EPSG:26910",
            drainage_tier="tier2",
        )
