"""Unit tests for v0.4 Phase 4b NOAA MRMS 1 km gridded QPE forcing."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_FETCH = _load_module("fetch_data", "pipeline/00_fetch_data.py")
_BUILD = _load_module("build_sfincs", "pipeline/02_build_sfincs.py")


def test_build_mrms_hourly_urls() -> None:
    entries = _FETCH.build_mrms_hourly_urls("2022-12-31", "2023-01-01")
    assert len(entries) == 48
    first_ts, first_urls = entries[0]
    last_ts, last_urls = entries[-1]
    assert first_ts == datetime(2022, 12, 31, 0, 0, 0)
    assert "20221231-010000.grib2.gz" in first_urls[0]
    assert "mtarchive.geol.iastate.edu/2022/12/31" in first_urls[1]
    assert last_ts == datetime(2023, 1, 1, 23, 0, 0)
    assert "20230102-000000.grib2.gz" in last_urls[0]


def test_build_mrms_net_precip_da() -> None:
    times = pd.date_range("2022-12-31T00:00:00", periods=2, freq="1h")
    mrms = xr.DataArray(
        np.array([[[2.0, 10.0]], [[8.0, 15.0]]], dtype=np.float32),
        dims=("time", "y", "x"),
        coords={"time": times, "y": [4175000.0], "x": [545000.0, 546000.0]},
        name="precip_mm_hr",
    )
    da = _BUILD.build_mrms_net_precip_da(
        mrms_da=mrms,
        drainage_mm_hr=5.0,
        tref=datetime(2022, 12, 31, 0, 0, 0),
        duration_hr=48.0,
        project_crs="EPSG:26910",
    )
    assert da.dims == ("time", "y", "x")
    assert da.rio.crs.to_string() == "EPSG:26910"
    assert da.shape == (3, 1, 2)  # extended to tstop
    assert da.values[0, 0, 0] == pytest.approx(0.0)
    assert da.values[0, 0, 1] == pytest.approx(5.0)
    assert da.values[2, 0, 1] == pytest.approx(10.0)

    with pytest.raises(ValueError, match="EPSG:26910"):
        _BUILD.build_mrms_net_precip_da(
            mrms_da=mrms,
            drainage_mm_hr=5.0,
            tref=datetime(2022, 12, 31, 0, 0, 0),
            duration_hr=48.0,
            project_crs="EPSG:4326",
        )
