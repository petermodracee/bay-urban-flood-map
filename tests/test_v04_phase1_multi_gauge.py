"""Unit tests for v0.4 Phase 1 multi-gauge SF Bay tidal forcing."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_FETCH = _load_module("fetch_data", "pipeline/00_fetch_data.py")
_BUILD = _load_module("build_sfincs", "pipeline/02_build_sfincs.py")


def test_generate_design_tidal_hydrograph_phase_lag() -> None:
    base = _FETCH.generate_design_tidal_hydrograph(
        duration_hr=24.0,
        timestep_min=60.0,
        mhhw_m=1.80,
        surge_m=0.35,
        tidal_amp_m=0.85,
        slr_m=0.0,
        peak_time_hr=12.0,
        phase_lag_min=0.0,
    )
    lagged = _FETCH.generate_design_tidal_hydrograph(
        duration_hr=24.0,
        timestep_min=60.0,
        mhhw_m=1.80,
        surge_m=0.35,
        tidal_amp_m=0.85,
        slr_m=0.0,
        peak_time_hr=12.0,
        phase_lag_min=60.0,
    )
    base_peak_min = max(base, key=lambda r: r[1])[0]
    lagged_peak_min = max(lagged, key=lambda r: r[1])[0]
    assert base_peak_min == pytest.approx(720.0)
    assert lagged_peak_min == pytest.approx(780.0)


def test_merge_multi_station_tidal_series_interpolates_offsets() -> None:
    series = {
        "9414290": [(0.0, 1.0), (10.0, 2.0)],
        "9414750": [(0.0, 1.5), (5.0, 2.0), (10.0, 2.5)],
    }
    rows = _FETCH.merge_multi_station_tidal_series(
        station_series=series,
        station_order=["9414290", "9414750"],
    )
    assert [r["time_min"] for r in rows] == [0.0, 5.0, 10.0]
    assert rows[1]["wl_9414290_m"] == pytest.approx(1.5)
    assert rows[1]["wl_9414750_m"] == pytest.approx(2.0)


def test_build_and_write_multi_station_waterlevel_files(tmp_path: Path) -> None:
    stations = [
        {"id": "9414290", "x_epsg26910": 547016.8, "y_epsg26910": 4184458.7},
        {"id": "9414750", "x_epsg26910": 561625.5, "y_epsg26910": 4180743.7},
        {"id": "9414523", "x_epsg26910": 569653.7, "y_epsg26910": 4151388.8},
    ]
    tidal_rows = [
        {
            "time_min": 0.0,
            "wl_9414290_m": 1.10,
            "wl_9414750_m": 1.25,
            "wl_9414523_m": 1.40,
        },
        {
            "time_min": 60.0,
            "wl_9414290_m": 1.80,
            "wl_9414750_m": 1.95,
            "wl_9414523_m": 2.15,
        },
    ]
    tref = datetime(2020, 1, 1, 0, 0, 0)
    bzs_df, bnd_gdf = _BUILD.build_multi_station_waterlevel_forcing_inputs(
        tidal_rows=tidal_rows,
        stations=stations,
        reference_time_utc=tref,
        duration_hr=2.0,
        project_crs="EPSG:26910",
    )
    assert list(bzs_df.columns) == [1, 2, 3]
    assert len(bnd_gdf) == 3
    assert (bzs_df.index[-1] - tref).total_seconds() == pytest.approx(7200.0)

    bnd_path, bzs_path = _BUILD.write_sfincs_waterlevel_files(
        run_dir=tmp_path,
        bzs_df=bzs_df,
        bnd_gdf=bnd_gdf,
        tref=tref,
    )
    bnd_lines = [ln.strip() for ln in bnd_path.read_text().splitlines() if ln.strip()]
    bzs_lines = [ln.strip() for ln in bzs_path.read_text().splitlines() if ln.strip()]
    assert len(bnd_lines) == 3
    assert bnd_lines[0] == "547016.8 4184458.7"
    assert bnd_lines[2] == "569653.7 4151388.8"
    assert len(bzs_lines) == 3
    first_cols = bzs_lines[0].split()
    assert len(first_cols) == 4
    assert float(first_cols[1]) == pytest.approx(1.10)
    assert float(first_cols[3]) == pytest.approx(1.40)


def test_build_multi_station_rejects_wrong_crs_and_missing_col() -> None:
    stations = [{"id": "9414290", "x_epsg26910": 547016.8, "y_epsg26910": 4184458.7}]
    with pytest.raises(ValueError, match="EPSG:26910"):
        _BUILD.build_multi_station_waterlevel_forcing_inputs(
            tidal_rows=[{"time_min": 0.0, "wl_9414290_m": 1.0}],
            stations=stations,
            reference_time_utc="2020-01-01T00:00:00",
            duration_hr=1.0,
            project_crs="EPSG:4326",
        )
    with pytest.raises(ValueError, match="missing required columns"):
        _BUILD.build_multi_station_waterlevel_forcing_inputs(
            tidal_rows=[{"time_min": 0.0, "water_level_m": 1.0}],
            stations=stations,
            reference_time_utc="2020-01-01T00:00:00",
            duration_hr=1.0,
            project_crs="EPSG:26910",
        )
