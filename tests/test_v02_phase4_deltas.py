"""Offline unit tests for v0.2 Phase 4 derived SLR deltas and multi-scenario export."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT_SCRIPT_PATH = REPO_ROOT / "pipeline" / "04_export_cog.py"


def _load_export_module():
    spec = importlib.util.spec_from_file_location(
        "phase4_export_v02", EXPORT_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exp_mod = _load_export_module()


def test_compute_slr_delta_m_clamping_and_nodata() -> None:
    base = np.array([[0.2, 0.5], [-9999.0, -9999.0]], dtype=np.float32)
    slr = np.array([[0.8, 0.3], [0.4, -9999.0]], dtype=np.float32)

    delta = exp_mod.compute_slr_delta_m(slr, base, nodata_value=-9999.0)
    assert math.isclose(float(delta[0, 0]), 0.6, abs_tol=1e-6)
    assert math.isclose(float(delta[0, 1]), 0.0, abs_tol=1e-6)
    # Wet cell over dry/nodata baseline gets full slr depth; both-nodata stays nodata
    assert math.isclose(float(delta[1, 0]), 0.4, abs_tol=1e-6)
    assert float(delta[1, 1]) == -9999.0


def test_build_manifest_entry_historical_delta_and_opc() -> None:
    ar_entry = exp_mod.build_manifest_entry(
        scenario_cfg={"id": "sf_citywide_ar_20221231", "rain": "rain_ar_20221231"},
        rain_cfg={"label": "Dec 31, 2022 Atmospheric River", "return_period_yr": None},
        slr_cfg={
            "slr_ft": 0.0,
            "opc_2024_pathway": "Observed Hindcast (NOAA 9414290)",
            "opc_2024_horizon": "2022-12-31",
        },
        cog_filename="sf_citywide_ar_20221231_depth_3857.tif",
        metric="depth",
    )
    assert ar_entry["id"] == "sf_citywide_ar_20221231_depth"
    assert ar_entry["storm"] == "ar_20221231"
    assert ar_entry["label"] == "Dec 31, 2022 Atmospheric River"
    assert ar_entry["opc_2024_pathway"] == "Observed Hindcast (NOAA 9414290)"

    delta_entry = exp_mod.build_manifest_entry(
        scenario_cfg={"id": "sf_citywide_100yr_slr3ft", "rain": "rain_100yr"},
        rain_cfg={"label": "100-Year Design Storm", "return_period_yr": 100},
        slr_cfg={
            "slr_ft": 3.0,
            "opc_2024_pathway": "2024 CA OPC Intermediate (~2100)",
            "opc_2024_horizon": "2080-2100",
        },
        cog_filename="sf_citywide_100yr_slr3ft_delta_3857.tif",
        metric="delta",
    )
    assert delta_entry["id"] == "sf_citywide_100yr_slr3ft_delta"
    assert delta_entry["metric"] == "delta"
    assert delta_entry["slr_ft"] == 3
    assert "(Added Depth)" in delta_entry["label"]


def test_export_311_geojson(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "calls.gpkg"
    out_geojson = tmp_path / "calls.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "service_request_id": ["101", "102"],
            "requested_datetime": ["2022-12-31T10:00:00Z", "2022-12-31T12:00:00Z"],
            "service_subtype": ["Flooding", "Flooding"],
            "service_details": ["On_street", "On_property"],
            "address": ["100 Main St", "Outside Box St"],
            "neighborhood": ["SOMA", "Outside"],
        },
        geometry=[Point(552000.0, 4180000.0), Point(560000.0, 4190000.0)],
        crs="EPSG:26910",
    )
    gdf.to_file(gpkg_path, driver="GPKG")

    exp_mod.export_311_geojson(
        source_path=gpkg_path,
        bbox_utm=[542600.0, 4173000.0, 555600.0, 4185400.0],
        project_crs="EPSG:26910",
        output_path=out_geojson,
    )
    payload = json.loads(out_geojson.read_text(encoding="utf-8"))
    assert len(payload["features"]) == 1
    assert payload["features"][0]["properties"]["service_request_id"] == "101"
