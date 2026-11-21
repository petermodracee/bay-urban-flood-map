"""Offline unit tests for v0.2 Phase 0 citywide fetch helpers and config."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest
import yaml
from pyproj import CRS

REPO_ROOT = Path(__file__).resolve().parent.parent
CITYWIDE_CONFIG_PATH = REPO_ROOT / "configs" / "sf_citywide.yml"
FETCH_SCRIPT_PATH = REPO_ROOT / "pipeline" / "00_fetch_data.py"


def _load_fetch_module():
    spec = importlib.util.spec_from_file_location(
        "phase0_fetch_data_v02", FETCH_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_mod = _load_fetch_module()


def test_generate_design_tidal_hydrograph() -> None:
    rows = fetch_mod.generate_design_tidal_hydrograph(
        duration_hr=24.0,
        timestep_min=10.0,
        mhhw_m=1.85,
        surge_m=0.35,
        tidal_amp_m=0.85,
        slr_m=0.9144,
        peak_time_hr=12.0,
    )
    assert len(rows) == 145
    assert rows[0][0] == 0.0
    assert rows[-1][0] == 1440.0
    peak_idx = next(i for i, (t, _) in enumerate(rows) if math.isclose(t, 720.0))
    assert math.isclose(rows[peak_idx][1], 1.85 + 0.35 + 0.9144, abs_tol=1e-6)
    assert math.isclose(max(w for _, w in rows), 1.85 + 0.35 + 0.9144, abs_tol=1e-6)


def test_parse_noaa_coops_water_levels() -> None:
    payload = {
        "data": [
            {"t": "2022-12-31 00:06", "v": "1.105"},
            {"t": "2022-12-31 00:00", "v": "1.050"},
            {"t": "2022-12-31 00:12", "v": ""},
        ]
    }
    rows = fetch_mod.parse_noaa_coops_water_levels(payload, "2022-12-31T00:00:00")
    assert rows == [(0.0, 1.050), (6.0, 1.105)]

    with pytest.raises(ValueError):
        fetch_mod.parse_noaa_coops_water_levels(
            {"error": {"message": "bad"}}, "2022-12-31T00:00:00"
        )


def test_parse_open_meteo_hourly_precip() -> None:
    payload = {
        "hourly": {
            "time": ["2022-12-31T00:00", "2022-12-31T01:00", "2022-12-31T02:00"],
            "precipitation": [0.5, 12.2, None],
        }
    }
    rows = fetch_mod.parse_open_meteo_hourly_precip(payload)
    assert rows == [(0.0, 0.5, 0.5), (60.0, 12.2, 12.2), (120.0, 0.0, 0.0)]


def test_parse_datasf_311_flood_reports() -> None:
    records = [
        {
            "service_request_id": "1001",
            "requested_datetime": "2022-12-31T10:15:00",
            "service_name": "Sewer Issues",
            "service_subtype": "Flooding",
            "lat": "37.7521",
            "long": "-122.4150",
            "address": "24TH ST",
            "neighborhoods_sffind_boundaries": "Mission",
        },
        {"service_request_id": "1002", "lat": None, "long": None},
    ]
    gdf = fetch_mod.parse_datasf_311_flood_reports(records)
    assert len(gdf) == 1
    assert gdf.crs == CRS.from_epsg(4326)
    assert gdf.loc[0, "service_request_id"] == "1001"
    assert gdf.geometry.iloc[0].geom_type == "Point"


def test_split_wgs84_bbox_for_overpass_and_citywide_config() -> None:
    small = (-122.43, 37.72, -122.38, 37.77)
    assert len(fetch_mod._split_wgs84_bbox_for_overpass(small)) == 1
    large = (-122.52, 37.70, -122.36, 37.82)
    assert len(fetch_mod._split_wgs84_bbox_for_overpass(large)) == 4

    with CITYWIDE_CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["project"]["crs"] == "EPSG:26910"
    assert cfg["domain"]["grid_resolution_m"] == 20.0
    assert cfg["domain"]["subgrid_resolution_m"] == 2.0
    assert cfg["terrain_prep"]["manning_n_paved"] == 0.030
    assert cfg["sfincs_model"]["drainage_rate_mm_hr"] == 5.0
    assert len(cfg["scenarios"]) == 9
