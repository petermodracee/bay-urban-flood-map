"""Offline unit tests for Phase 0 data fetch helpers and benchmark config."""

from __future__ import annotations

import csv
import importlib.util
import math
from pathlib import Path

import pytest
import yaml
from pyproj import CRS

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "configs" / "sf_benchmark.yml"
FETCH_SCRIPT_PATH = REPO_ROOT / "pipeline" / "00_fetch_data.py"


def _load_fetch_module():
    spec = importlib.util.spec_from_file_location(
        "phase0_fetch_data", FETCH_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_mod = _load_fetch_module()

IDF_10YR_MM = {
    5: 6.3,
    10: 9.1,
    15: 11.0,
    30: 15.0,
    60: 21.2,
    120: 29.5,
    180: 36.6,
    360: 50.3,
    720: 67.8,
    1440: 90.4,
}

IDF_100YR_MM = {
    5: 9.6,
    10: 13.8,
    15: 16.7,
    30: 22.9,
    60: 32.3,
    120: 44.5,
    180: 55.1,
    360: 76.2,
    720: 105.9,
    1440: 144.0,
}


def test_interpolate_idf_knot_recovery_and_monotonicity() -> None:
    for duration_min, expected_mm in IDF_100YR_MM.items():
        actual_mm = fetch_mod.interpolate_idf_depth_mm(duration_min, IDF_100YR_MM)
        assert math.isclose(actual_mm, expected_mm, rel_tol=1e-12, abs_tol=1e-12)

    d_45 = fetch_mod.interpolate_idf_depth_mm(45.0, IDF_100YR_MM)
    assert IDF_100YR_MM[30] < d_45 < IDF_100YR_MM[60]

    with pytest.raises(ValueError):
        fetch_mod.interpolate_idf_depth_mm(0.0, IDF_100YR_MM)


@pytest.mark.parametrize(
    ("idf_depths_mm", "expected_total_mm"),
    [
        (IDF_10YR_MM, 90.4),
        (IDF_100YR_MM, 144.0),
    ],
)
def test_generate_alternating_block_hyetograph_and_csv_roundtrip(
    tmp_path: Path, idf_depths_mm: dict[int, float], expected_total_mm: float
) -> None:
    rows = fetch_mod.generate_alternating_block_hyetograph(
        idf_depths_mm=idf_depths_mm,
        duration_hr=24.0,
        timestep_min=15.0,
    )
    assert len(rows) == 96

    times = [r[0] for r in rows]
    depths_mm = [r[1] for r in rows]
    intensities_mm_hr = [r[2] for r in rows]

    assert times[0] == 0.0
    assert times[-1] == 1425.0
    assert all(d >= 0.0 for d in depths_mm)
    assert math.isclose(sum(depths_mm), expected_total_mm, rel_tol=0.0, abs_tol=1e-6)

    for depth_mm, intensity_mm_hr in zip(depths_mm, intensities_mm_hr, strict=True):
        assert math.isclose(
            intensity_mm_hr, depth_mm * 4.0, rel_tol=1e-12, abs_tol=1e-12
        )

    center_idx = (len(rows) - 1) // 2
    peak_idx = depths_mm.index(max(depths_mm))
    assert peak_idx == center_idx
    assert math.isclose(depths_mm[peak_idx], idf_depths_mm[15], rel_tol=1e-12)

    out_csv = tmp_path / "hyetograph.csv"
    fetch_mod._write_hyetograph_csv(out_csv, rows)
    with out_csv.open("r", encoding="utf-8", newline="") as f:
        reader = list(csv.reader(f))

    assert reader[0] == ["time_min", "depth_mm", "intensity_mm_hr"]
    assert len(reader) == 97
    csv_sum_mm = sum(float(row[1]) for row in reader[1:])
    assert math.isclose(csv_sum_mm, expected_total_mm, rel_tol=0.0, abs_tol=1e-4)


def test_buffer_and_transform_bbox() -> None:
    bbox_utm = [550000.0, 4175800.0, 554200.0, 4180600.0]
    buffered = fetch_mod.buffer_bbox_utm(bbox_utm, 500.0)
    assert buffered == (549500.0, 4175300.0, 554700.0, 4181100.0)

    west, south, east, north = fetch_mod.transform_bbox(
        buffered, "EPSG:26910", "EPSG:4326"
    )
    assert -122.5 < west < east < -122.3
    assert 37.65 < south < north < 37.85

    with pytest.raises(ValueError):
        fetch_mod.buffer_bbox_utm(bbox_utm, -10.0)


def test_filter_tnm_items() -> None:
    items = [
        {
            "title": "USGS_1M_10_x54y418_CA_SanFrancisco_B23_2023.tif",
            "format": "GeoTIFF",
            "downloadURL": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/TIFF/USGS_1M_10_x54y418_CA_SanFrancisco_B23.tif",
        },
        {
            "title": "USGS_1M_10_x54y418_CA_OtherProject_2020.tif",
            "format": "GeoTIFF",
            "downloadURL": "https://example.org/other.tif",
        },
        {
            "title": "USGS_1M_10_x55y419_CA_SanFrancisco_B23_2023.tif",
            "format": "IMG",
            "downloadURL": "https://example.org/x55y419.img",
        },
        {
            "title": "USGS_1M_10_x55y419_CA_SanFrancisco_B23_2023.tif",
            "format": "GeoTIFF",
            "downloadURL": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/TIFF/USGS_1M_10_x55y419_CA_SanFrancisco_B23.tif",
        },
    ]
    urls = fetch_mod.filter_tnm_items(items)
    assert len(urls) == 2
    assert any("x54y418" in u for u in urls)
    assert any("x55y419" in u for u in urls)


def test_parse_sda_hsg_response() -> None:
    payload = {
        "Table": [
            [
                "mupolygonkey",
                "mukey",
                "areasymbol",
                "musym",
                "muname",
                "hydgrpdcd",
                "wkt",
            ],
            [
                "12345",
                "460123",
                "CA689",
                "118",
                "Urban land",
                "D",
                "POLYGON ((-122.42 37.74, -122.41 37.74, -122.41 37.75, -122.42 37.75, -122.42 37.74))",
            ],
        ]
    }
    gdf = fetch_mod.parse_sda_hsg_response(payload)
    assert len(gdf) == 1
    assert gdf.crs == CRS.from_epsg(4326)
    assert gdf.loc[0, "areasymbol"] == "CA689"
    assert gdf.loc[0, "hydgrpdcd"] == "D"
    assert gdf.geometry.iloc[0].geom_type == "Polygon"

    empty_gdf = fetch_mod.parse_sda_hsg_response({"Table": []})
    assert empty_gdf.empty
    assert empty_gdf.crs == CRS.from_epsg(4326)


def test_parse_overpass_lines_and_buildings() -> None:
    roads_payload = {
        "elements": [
            {
                "type": "way",
                "id": 101,
                "geometry": [
                    {"lat": 37.74, "lon": -122.42},
                    {"lat": 37.75, "lon": -122.41},
                ],
                "tags": {
                    "highway": "motorway",
                    "bridge": "yes",
                    "layer": "1",
                    "name": "US 101",
                },
            },
            {
                "type": "way",
                "id": 102,
                "geometry": [{"lat": 37.74, "lon": -122.42}],
                "tags": {"railway": "rail"},
            },
        ]
    }
    roads_gdf = fetch_mod.parse_overpass_lines(roads_payload)
    assert len(roads_gdf) == 1
    assert roads_gdf.crs == CRS.from_epsg(4326)
    assert roads_gdf.loc[0, "osm_id"] == 101
    assert roads_gdf.loc[0, "highway"] == "motorway"
    assert roads_gdf.geometry.iloc[0].geom_type == "LineString"

    buildings_payload = {
        "elements": [
            {
                "type": "way",
                "id": 201,
                "geometry": [
                    {"lat": 37.740, "lon": -122.420},
                    {"lat": 37.740, "lon": -122.419},
                    {"lat": 37.741, "lon": -122.419},
                    {"lat": 37.741, "lon": -122.420},
                    {"lat": 37.740, "lon": -122.420},
                ],
                "tags": {"building": "yes", "height": "12", "building:levels": "3"},
            },
            {
                "type": "relation",
                "id": 202,
                "tags": {
                    "type": "multipolygon",
                    "building": "commercial",
                    "name": "Courtyard",
                },
                "members": [
                    {
                        "type": "way",
                        "role": "outer",
                        "geometry": [
                            {"lat": 37.742, "lon": -122.420},
                            {"lat": 37.742, "lon": -122.418},
                            {"lat": 37.744, "lon": -122.418},
                            {"lat": 37.744, "lon": -122.420},
                            {"lat": 37.742, "lon": -122.420},
                        ],
                    },
                    {
                        "type": "way",
                        "role": "inner",
                        "geometry": [
                            {"lat": 37.7425, "lon": -122.4195},
                            {"lat": 37.7425, "lon": -122.4185},
                            {"lat": 37.7435, "lon": -122.4185},
                            {"lat": 37.7435, "lon": -122.4195},
                            {"lat": 37.7425, "lon": -122.4195},
                        ],
                    },
                ],
            },
        ]
    }
    bldg_gdf = fetch_mod.parse_overpass_buildings(buildings_payload)
    assert len(bldg_gdf) == 2
    assert bldg_gdf.crs == CRS.from_epsg(4326)
    assert list(bldg_gdf["osm_id"]) == [201, 202]
    assert len(bldg_gdf.geometry.iloc[1].interiors) == 1


def test_sf_benchmark_config_has_phase0_fields() -> None:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert cfg["project"]["crs"] == "EPSG:26910"
    assert cfg["project"]["output_crs"] == "EPSG:3857"
    assert cfg["project"]["vertical_datum"] == "NAVD88"

    domain = cfg["domain"]
    assert domain["bbox_epsg26910"] == [550000.0, 4175800.0, 554200.0, 4180600.0]
    assert domain["fetch_buffer_m"] == 500.0
    for key in (
        "dem_source",
        "nlcd_landcover_source",
        "nlcd_impervious_source",
        "hsg_soils_source",
        "roads_rail_source",
        "buildings_source",
        "validation_reference",
    ):
        assert isinstance(domain[key], str) and domain[key].startswith("data/raw/")

    rain_events = cfg["rain_events"]
    assert rain_events["rain_10yr"]["total_depth_mm"] == 90.4
    assert rain_events["rain_100yr"]["total_depth_mm"] == 144.0
    for key in ("rain_10yr", "rain_100yr"):
        ev = rain_events[key]
        assert ev["hyetograph_method"] == "alternating_block"
        assert ev["timestep_min"] == 15
        assert ev["hyetograph_file"].startswith("data/raw/")
        assert len(ev["idf_depths_mm"]) == 10
