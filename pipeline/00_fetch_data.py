"""Fetch and generate Phase 0 raw datasets and design-storm hyetographs."""

from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import rasterio
import requests
import shapely.wkt
import yaml
from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import linemerge, polygonize

TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
TNM_DEM_DATASET = "Digital Elevation Model (DEM) 1 meter"
MRLC_WCS_URL = "https://www.mrlc.gov/geoserver/mrlc_download/wcs"
NLCD_LANDCOVER_COVERAGE = "mrlc_download__NLCD_2021_Land_Cover_L48"
NLCD_IMPERVIOUS_COVERAGE = "mrlc_download__NLCD_2021_Impervious_L48"
USDA_SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
DATASF_100YR_URL = (
    "https://data.sf.gov/api/geospatial/jzu3-4yxp?method=export&format=GeoJSON"
)
DEFAULT_HTTP_TIMEOUT_S = 180


def buffer_bbox_utm(
    bbox_utm: Sequence[float], buffer_m: float
) -> tuple[float, float, float, float]:
    """Expand a 4-element [xmin, ymin, xmax, ymax] bounding box by buffer_m."""
    if len(bbox_utm) != 4:
        raise ValueError(f"Expected 4 coordinates in bbox, got {len(bbox_utm)}")
    if buffer_m < 0:
        raise ValueError(f"buffer_m must be non-negative, got {buffer_m}")
    xmin, ymin, xmax, ymax = (float(v) for v in bbox_utm)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError(f"Invalid bbox ordering: {bbox_utm}")
    return (xmin - buffer_m, ymin - buffer_m, xmax + buffer_m, ymax + buffer_m)


def transform_bbox(
    bbox: Sequence[float], src_crs: str, dst_crs: str
) -> tuple[float, float, float, float]:
    """Reproject a [xmin, ymin, xmax, ymax] bounding box between coordinate reference systems."""
    if len(bbox) != 4:
        raise ValueError(f"Expected 4 coordinates in bbox, got {len(bbox)}")
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    out = transformer.transform_bounds(xmin, ymin, xmax, ymax, densify_pts=21)
    return (float(out[0]), float(out[1]), float(out[2]), float(out[3]))


def interpolate_idf_depth_mm(
    duration_min: float, idf_depths_mm: Mapping[int | float | str, float]
) -> float:
    """Interpolate cumulative precipitation depth (mm) via piecewise log-log IDF knots."""
    if duration_min <= 0:
        raise ValueError(f"duration_min must be positive, got {duration_min}")
    if len(idf_depths_mm) < 2:
        raise ValueError("At least two IDF duration-depth knots are required.")

    knots = sorted((float(d), float(v)) for d, v in idf_depths_mm.items())
    for d_min, depth_mm in knots:
        if d_min <= 0 or depth_mm <= 0:
            raise ValueError(
                f"IDF knot duration and depth must be positive: {(d_min, depth_mm)}"
            )
        if math.isclose(duration_min, d_min, rel_tol=1e-12, abs_tol=1e-12):
            return depth_mm

    # Select bounding segment (or boundary segment if outside knot range).
    idx = 0
    for i in range(len(knots) - 1):
        if duration_min >= knots[i][0]:
            idx = i
    idx = min(idx, len(knots) - 2)

    d0, p0 = knots[idx]
    d1, p1 = knots[idx + 1]
    frac = (math.log(duration_min) - math.log(d0)) / (math.log(d1) - math.log(d0))
    log_depth = math.log(p0) + frac * (math.log(p1) - math.log(p0))
    return math.exp(log_depth)


def generate_alternating_block_hyetograph(
    idf_depths_mm: Mapping[int | float | str, float],
    duration_hr: float,
    timestep_min: float,
) -> list[tuple[float, float, float]]:
    """Build an alternating-block design hyetograph as (time_min, depth_mm, intensity_mm_hr)."""
    if duration_hr <= 0 or timestep_min <= 0:
        raise ValueError("duration_hr and timestep_min must be positive.")

    total_duration_min = duration_hr * 60.0
    n_steps_float = total_duration_min / timestep_min
    n_steps = round(n_steps_float)
    if (
        not math.isclose(n_steps_float, n_steps, rel_tol=1e-9, abs_tol=1e-9)
        or n_steps < 1
    ):
        raise ValueError(
            f"duration_hr*60 ({total_duration_min}) must be divisible by timestep_min ({timestep_min})."
        )

    cum_depths_mm = [
        interpolate_idf_depth_mm((step + 1) * timestep_min, idf_depths_mm)
        for step in range(n_steps)
    ]
    increments_mm: list[float] = []
    prev_depth_mm = 0.0
    for cum_depth_mm in cum_depths_mm:
        inc_mm = cum_depth_mm - prev_depth_mm
        if inc_mm < -1e-9:
            raise ValueError(
                f"Non-monotonic cumulative IDF depth encountered: {inc_mm}"
            )
        increments_mm.append(max(0.0, inc_mm))
        prev_depth_mm = cum_depth_mm

    # Sort descending so the peak block sits at center and smaller blocks alternate right/left.
    ranked_blocks_mm = sorted(increments_mm, reverse=True)
    ordered_depths_mm = [0.0] * n_steps
    center_idx = (n_steps - 1) // 2
    ordered_depths_mm[center_idx] = ranked_blocks_mm[0]

    right_offset = 1
    left_offset = 1
    place_right = True
    for block_depth_mm in ranked_blocks_mm[1:]:
        if place_right and center_idx + right_offset < n_steps:
            ordered_depths_mm[center_idx + right_offset] = block_depth_mm
            right_offset += 1
        elif center_idx - left_offset >= 0:
            ordered_depths_mm[center_idx - left_offset] = block_depth_mm
            left_offset += 1
        else:
            ordered_depths_mm[center_idx + right_offset] = block_depth_mm
            right_offset += 1
        place_right = not place_right

    target_total_mm = interpolate_idf_depth_mm(total_duration_min, idf_depths_mm)
    if abs(sum(ordered_depths_mm) - target_total_mm) > 1e-6:
        raise ValueError(
            f"Hyetograph sum {sum(ordered_depths_mm):.8f} mm != target {target_total_mm:.8f} mm"
        )

    rows: list[tuple[float, float, float]] = []
    rate_scale = 60.0 / timestep_min
    for idx, depth_mm in enumerate(ordered_depths_mm):
        time_min = idx * timestep_min
        intensity_mm_hr = depth_mm * rate_scale
        rows.append((time_min, depth_mm, intensity_mm_hr))
    return rows


def filter_tnm_items(
    items: Sequence[Mapping[str, Any]], project_substring: str = "CA_SanFrancisco_B23"
) -> list[str]:
    """Filter USGS TNM product items to unique GeoTIFF download URLs for a target project."""
    urls: list[str] = []
    seen: set[str] = set()
    for item in items:
        title = str(item.get("title") or "")
        download_url = str(
            item.get("downloadURL") or item.get("urls", {}).get("TIFF") or ""
        )
        fmt = str(item.get("format") or "")
        if not download_url:
            continue
        if (
            project_substring
            and project_substring not in title
            and project_substring not in download_url
        ):
            continue
        if (
            fmt
            and "geotiff" not in fmt.lower()
            and not download_url.lower().endswith((".tif", ".tiff"))
        ):
            continue
        if download_url not in seen:
            seen.add(download_url)
            urls.append(download_url)
    return sorted(urls)


def parse_sda_hsg_response(payload: Mapping[str, Any]) -> gpd.GeoDataFrame:
    """Parse USDA Soil Data Access JSON+COLUMNNAME payload into an EPSG:4326 GeoDataFrame."""
    columns_expected = [
        "mupolygonkey",
        "mukey",
        "areasymbol",
        "musym",
        "muname",
        "hydgrpdcd",
    ]
    table = payload.get("Table")
    if not table or len(table) < 2:
        return gpd.GeoDataFrame(
            {col: [] for col in columns_expected},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )

    header = [str(col).lower() for col in table[0]]
    col_idx = {name: i for i, name in enumerate(header)}
    if "wkt" not in col_idx:
        raise ValueError(
            f"USDA SDA response missing 'wkt' column; got columns {header}"
        )

    records: list[dict[str, Any]] = []
    geometries: list[Any] = []
    for row in table[1:]:
        wkt_val = row[col_idx["wkt"]]
        if not wkt_val:
            continue
        geom = shapely.wkt.loads(str(wkt_val))
        if geom.is_empty:
            continue
        record = {
            col: (row[col_idx[col]] if col in col_idx else None)
            for col in columns_expected
        }
        records.append(record)
        geometries.append(geom)

    if not records:
        return gpd.GeoDataFrame(
            {col: [] for col in columns_expected},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


def parse_overpass_lines(payload: Mapping[str, Any]) -> gpd.GeoDataFrame:
    """Parse Overpass `out geom;` JSON road/rail ways into an EPSG:4326 LineString GeoDataFrame."""
    columns = ["osm_id", "highway", "railway", "bridge", "tunnel", "layer", "name"]
    records: list[dict[str, Any]] = []
    geometries: list[LineString] = []

    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        raw_geom = element.get("geometry") or []
        if len(raw_geom) < 2:
            continue
        coords = [(float(pt["lon"]), float(pt["lat"])) for pt in raw_geom]
        tags = element.get("tags") or {}
        records.append(
            {
                "osm_id": int(element["id"]),
                "highway": tags.get("highway"),
                "railway": tags.get("railway"),
                "bridge": tags.get("bridge"),
                "tunnel": tags.get("tunnel"),
                "layer": tags.get("layer"),
                "name": tags.get("name"),
            }
        )
        geometries.append(LineString(coords))

    if not records:
        return gpd.GeoDataFrame(
            {col: [] for col in columns},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


def _ring_coords_from_overpass(
    raw_geom: Sequence[Mapping[str, Any]],
) -> list[tuple[float, float]]:
    coords = [(float(pt["lon"]), float(pt["lat"])) for pt in raw_geom]
    if len(coords) >= 3 and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _polygon_from_overpass_relation(
    element: Mapping[str, Any],
) -> Polygon | MultiPolygon | None:
    outer_lines: list[LineString] = []
    inner_lines: list[LineString] = []
    for member in element.get("members") or []:
        if member.get("type") != "way":
            continue
        raw_geom = member.get("geometry") or []
        if len(raw_geom) < 2:
            continue
        line = LineString([(float(pt["lon"]), float(pt["lat"])) for pt in raw_geom])
        role = member.get("role", "outer")
        if role == "inner":
            inner_lines.append(line)
        else:
            outer_lines.append(line)

    if not outer_lines:
        return None

    outer_merged = linemerge(outer_lines) if len(outer_lines) > 1 else outer_lines[0]
    outer_geom_list = (
        [outer_merged]
        if isinstance(outer_merged, LineString)
        else list(outer_merged.geoms)
    )
    outer_polys = list(polygonize(outer_geom_list))
    if not outer_polys:
        return None

    inner_polys = []
    if inner_lines:
        inner_merged = (
            linemerge(inner_lines) if len(inner_lines) > 1 else inner_lines[0]
        )
        inner_geom_list = (
            [inner_merged]
            if isinstance(inner_merged, LineString)
            else list(inner_merged.geoms)
        )
        inner_polys = list(polygonize(inner_geom_list))

    built: list[Polygon] = []
    for outer in outer_polys:
        holes = [
            list(inner.exterior.coords)
            for inner in inner_polys
            if outer.contains(inner)
        ]
        poly = Polygon(outer.exterior.coords, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty and isinstance(poly, (Polygon, MultiPolygon)):
            if isinstance(poly, Polygon):
                built.append(poly)
            else:
                built.extend(poly.geoms)

    if not built:
        return None
    return built[0] if len(built) == 1 else MultiPolygon(built)


def parse_overpass_buildings(payload: Mapping[str, Any]) -> gpd.GeoDataFrame:
    """Parse Overpass `out geom;` building ways and multipolygon relations into EPSG:4326 polygons."""
    columns = ["osm_id", "building", "height", "building_levels", "name"]
    records: list[dict[str, Any]] = []
    geometries: list[Polygon | MultiPolygon] = []

    for element in payload.get("elements", []):
        el_type = element.get("type")
        tags = element.get("tags") or {}
        geom: Polygon | MultiPolygon | None = None

        if el_type == "way":
            raw_geom = element.get("geometry") or []
            if len(raw_geom) < 3:
                continue
            coords = _ring_coords_from_overpass(raw_geom)
            if len(coords) < 4:
                continue
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty and isinstance(poly, (Polygon, MultiPolygon)):
                geom = poly
        elif el_type == "relation" and tags.get("type") == "multipolygon":
            geom = _polygon_from_overpass_relation(element)

        if geom is None or geom.is_empty:
            continue

        records.append(
            {
                "osm_id": int(element["id"]),
                "building": tags.get("building"),
                "height": tags.get("height"),
                "building_levels": tags.get("building:levels"),
                "name": tags.get("name"),
            }
        )
        geometries.append(geom)

    if not records:
        return gpd.GeoDataFrame(
            {col: [] for col in columns},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


def _assert_crs(actual_crs: Any, expected_epsg: str, label: str) -> None:
    if actual_crs is None:
        raise ValueError(f"{label} is missing CRS; expected {expected_epsg}")
    if CRS.from_user_input(actual_crs) != CRS.from_user_input(expected_epsg):
        raise ValueError(
            f"{label} CRS mismatch: got {actual_crs}, expected {expected_epsg}"
        )


def _write_hyetograph_csv(
    output_path: Path, rows: Sequence[tuple[float, float, float]]
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_min", "depth_mm", "intensity_mm_hr"])
        for time_min, depth_mm, intensity_mm_hr in rows:
            writer.writerow(
                [f"{time_min:.1f}", f"{depth_mm:.6f}", f"{intensity_mm_hr:.6f}"]
            )


def _fetch_hyetographs(cfg: Mapping[str, Any], force: bool) -> None:
    for event_key, event_cfg in cfg.get("rain_events", {}).items():
        hyetograph_file = event_cfg.get("hyetograph_file")
        if not hyetograph_file:
            continue
        out_path = Path(hyetograph_file)
        if out_path.exists() and not force:
            print(f"[00_fetch_data] Skipping existing {out_path}")
            continue
        method = event_cfg.get("hyetograph_method", "alternating_block")
        if method != "alternating_block":
            raise ValueError(f"Unsupported hyetograph_method for {event_key}: {method}")
        rows = generate_alternating_block_hyetograph(
            idf_depths_mm=event_cfg["idf_depths_mm"],
            duration_hr=float(event_cfg["duration_hr"]),
            timestep_min=float(event_cfg["timestep_min"]),
        )
        _write_hyetograph_csv(out_path, rows)
        total_mm = sum(r[1] for r in rows)
        print(
            f"[00_fetch_data] Wrote {event_key} hyetograph ({len(rows)} steps, "
            f"sum={total_mm:.3f} mm) -> {out_path}"
        )


def _fetch_dem(
    output_path: Path,
    buffered_utm_bbox: tuple[float, float, float, float],
    buffered_wgs84_bbox: tuple[float, float, float, float],
    project_crs: str,
    subgrid_res_m: float,
    force: bool,
) -> None:
    if output_path.exists() and not force:
        print(f"[00_fetch_data] Skipping existing {output_path}")
        return

    west, south, east, north = buffered_wgs84_bbox
    resp = requests.get(
        TNM_PRODUCTS_URL,
        params={
            "datasets": TNM_DEM_DATASET,
            "bbox": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}",
            "prodFormats": "GeoTIFF",
            "outputFormat": "JSON",
            "max": 100,
        },
        timeout=DEFAULT_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    items = resp.json().get("items") or []
    tile_urls = filter_tnm_items(items, project_substring="CA_SanFrancisco_B23")
    if not tile_urls:
        raise RuntimeError(
            f"No CA_SanFrancisco_B23 3DEP 1m tiles returned by TNM for bbox {buffered_wgs84_bbox}"
        )

    required_tokens = ("x54y418", "x54y419", "x55y418", "x55y419")
    missing_tokens = [
        tok for tok in required_tokens if not any(tok in u for u in tile_urls)
    ]
    if missing_tokens:
        raise RuntimeError(
            f"Missing expected SF 3DEP tile(s) {missing_tokens}; found {tile_urls}"
        )

    print(f"[00_fetch_data] Streaming {len(tile_urls)} 3DEP 1m tiles via /vsicurl/:")
    for url in tile_urls:
        print(f"  - {url}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.tif")
    xmin, ymin, xmax, ymax = buffered_utm_bbox
    cmd = [
        "gdalwarp",
        "-t_srs",
        project_crs,
        "-te",
        str(xmin),
        str(ymin),
        str(xmax),
        str(ymax),
        "-tr",
        str(subgrid_res_m),
        str(subgrid_res_m),
        "-r",
        "bilinear",
        "-dstnodata",
        "-9999.0",
        "-co",
        "COMPRESS=DEFLATE",
        "-co",
        "TILED=YES",
        "-co",
        "BIGTIFF=IF_SAFER",
        "-overwrite",
        *[f"/vsicurl/{url}" for url in tile_urls],
        str(tmp_path),
    ]
    env = os.environ.copy()
    env.update(
        {
            "GDAL_HTTP_MULTIRANGE": "YES",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        }
    )
    subprocess.run(cmd, env=env, check=True)
    tmp_path.replace(output_path)

    with rasterio.open(output_path) as src:
        _assert_crs(src.crs, project_crs, str(output_path))
        print(
            f"[00_fetch_data] Wrote DEM {src.width}x{src.height} ({src.crs}) -> {output_path}"
        )


def _fetch_nlcd_wcs(
    output_path: Path,
    coverage_id: str,
    bbox_5070: tuple[float, float, float, float],
    force: bool,
) -> None:
    if output_path.exists() and not force:
        print(f"[00_fetch_data] Skipping existing {output_path}")
        return

    # Pad 100 m in EPSG:5070 so 30 m grid edges cleanly enclose the buffered study bbox.
    xmin_5070, ymin_5070, xmax_5070, ymax_5070 = buffer_bbox_utm(bbox_5070, 100.0)
    params = [
        ("SERVICE", "WCS"),
        ("VERSION", "2.0.1"),
        ("REQUEST", "GetCoverage"),
        ("COVERAGEID", coverage_id),
        ("FORMAT", "image/tiff"),
        ("SUBSET", f"X({xmin_5070:.3f},{xmax_5070:.3f})"),
        ("SUBSET", f"Y({ymin_5070:.3f},{ymax_5070:.3f})"),
    ]
    resp = requests.get(MRLC_WCS_URL, params=params, timeout=DEFAULT_HTTP_TIMEOUT_S)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()
    if "xml" in content_type or "html" in content_type:
        raise RuntimeError(
            f"MRLC WCS returned unexpected content type {content_type} for {coverage_id}: "
            f"{resp.text[:400]}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.tif")
    tmp_path.write_bytes(resp.content)
    with rasterio.open(tmp_path) as src:
        if src.crs is None:
            raise ValueError(f"NLCD raster {coverage_id} has no CRS")
        _assert_crs(src.crs, "EPSG:5070", str(output_path))
        shape_info = f"{src.width}x{src.height} ({src.crs})"
    tmp_path.replace(output_path)
    print(f"[00_fetch_data] Wrote NLCD {coverage_id} {shape_info} -> {output_path}")


def _fetch_usda_hsg(
    output_path: Path,
    buffered_wgs84_bbox: tuple[float, float, float, float],
    force: bool,
) -> None:
    if output_path.exists() and not force:
        print(f"[00_fetch_data] Skipping existing {output_path}")
        return

    west, south, east, north = buffered_wgs84_bbox
    aoi_wkt = (
        f"POLYGON(({west:.6f} {south:.6f}, {east:.6f} {south:.6f}, "
        f"{east:.6f} {north:.6f}, {west:.6f} {north:.6f}, {west:.6f} {south:.6f}))"
    )
    sql = (
        "SELECT mp.mupolygonkey, mp.mukey, mp.areasymbol, mp.musym, mu.muname, "
        "ag.hydgrpdcd, mp.mupolygongeo.STAsText() AS wkt "
        "FROM mupolygon mp "
        f"INNER JOIN SDA_Get_Mupolygonkey_from_intersection_with_WktWgs84('{aoi_wkt}') k "
        "ON mp.mupolygonkey = k.mupolygonkey "
        "INNER JOIN mapunit mu ON mp.mukey = mu.mukey "
        "LEFT JOIN muaggatt ag ON mp.mukey = ag.mukey"
    )
    resp = requests.post(
        USDA_SDA_URL,
        json={"query": sql, "format": "JSON+COLUMNNAME"},
        timeout=DEFAULT_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    gdf = parse_sda_hsg_response(resp.json())
    if gdf.empty:
        raise RuntimeError(
            f"USDA SDA returned zero soil polygons for bbox {buffered_wgs84_bbox}"
        )
    _assert_crs(gdf.crs, "EPSG:4326", str(output_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(output_path, driver="GPKG")
    areas = sorted({str(a) for a in gdf["areasymbol"].dropna()})
    print(
        f"[00_fetch_data] Wrote USDA HSG ({len(gdf)} polygons, areasymbols={areas}) -> {output_path}"
    )


def _query_overpass_with_retry(query: str, max_attempts: int = 6) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        mirror = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            resp = requests.post(
                mirror,
                data={"data": query},
                headers={"User-Agent": "bay-urban-flood-map/0.1 (phase0-fetch)"},
                timeout=DEFAULT_HTTP_TIMEOUT_S,
            )
            if resp.status_code in {429, 502, 503, 504}:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} from {mirror}", response=resp
                )
            resp.raise_for_status()
            payload = resp.json()
            if "elements" not in payload:
                raise ValueError(f"Unexpected Overpass response from {mirror}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_err = exc
            backoff_s = min(30.0, 2.0**attempt)
            print(
                f"[00_fetch_data] Overpass attempt {attempt + 1}/{max_attempts} failed "
                f"on {mirror}: {exc}. Retrying in {backoff_s:.0f}s..."
            )
            time.sleep(backoff_s)
    raise RuntimeError(f"All Overpass attempts failed: {last_err}") from last_err


def _fetch_osm_layers(
    roads_output_path: Path,
    buildings_output_path: Path,
    buffered_wgs84_bbox: tuple[float, float, float, float],
    force: bool,
) -> None:
    west, south, east, north = buffered_wgs84_bbox
    overpass_bbox = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"

    if roads_output_path.exists() and not force:
        print(f"[00_fetch_data] Skipping existing {roads_output_path}")
    else:
        roads_query = (
            "[out:json][timeout:120];\n"
            "(\n"
            f'  way["highway"]({overpass_bbox});\n'
            f'  way["railway"]({overpass_bbox});\n'
            ");\n"
            "out geom;\n"
        )
        roads_payload = _query_overpass_with_retry(roads_query)
        roads_gdf = parse_overpass_lines(roads_payload)
        if roads_gdf.empty:
            raise RuntimeError("Overpass returned zero road/rail lines.")
        _assert_crs(roads_gdf.crs, "EPSG:4326", str(roads_output_path))
        roads_output_path.parent.mkdir(parents=True, exist_ok=True)
        roads_gdf.to_file(roads_output_path, driver="GPKG")
        print(
            f"[00_fetch_data] Wrote OSM roads/rail ({len(roads_gdf)} features) -> "
            f"{roads_output_path}"
        )

    if buildings_output_path.exists() and not force:
        print(f"[00_fetch_data] Skipping existing {buildings_output_path}")
    else:
        buildings_query = (
            "[out:json][timeout:180];\n"
            "(\n"
            f'  way["building"]({overpass_bbox});\n'
            f'  relation["building"]["type"="multipolygon"]({overpass_bbox});\n'
            ");\n"
            "out geom;\n"
        )
        buildings_payload = _query_overpass_with_retry(buildings_query)
        buildings_gdf = parse_overpass_buildings(buildings_payload)
        if buildings_gdf.empty:
            raise RuntimeError("Overpass returned zero building polygons.")
        _assert_crs(buildings_gdf.crs, "EPSG:4326", str(buildings_output_path))
        buildings_output_path.parent.mkdir(parents=True, exist_ok=True)
        buildings_gdf.to_file(buildings_output_path, driver="GPKG")
        print(
            f"[00_fetch_data] Wrote OSM buildings ({len(buildings_gdf)} features) -> "
            f"{buildings_output_path}"
        )


def _fetch_datasf_flood_risk(output_path: Path, force: bool) -> None:
    if output_path.exists() and not force:
        print(f"[00_fetch_data] Skipping existing {output_path}")
        return

    resp = requests.get(DATASF_100YR_URL, timeout=DEFAULT_HTTP_TIMEOUT_S)
    resp.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_geojson = Path(tmp.name)

    try:
        gdf = gpd.read_file(tmp_geojson)
    finally:
        tmp_geojson.unlink(missing_ok=True)

    if gdf.empty:
        raise RuntimeError("DataSF 100-year flood risk export returned zero features.")
    _assert_crs(gdf.crs, "EPSG:4326", str(output_path))
    gdf.to_file(output_path, driver="GPKG")
    print(
        f"[00_fetch_data] Wrote DataSF 100-yr flood risk ({len(gdf)} features) -> {output_path}"
    )


def fetch_data(config_path: Path, force: bool = False) -> None:
    """Fetch all Phase 0 raw datasets and generate design-storm hyetographs."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    project = cfg["project"]
    domain = cfg["domain"]
    project_crs = str(project["crs"])
    _assert_crs(project_crs, "EPSG:26910", "config.project.crs")

    study_bbox_utm = domain["bbox_epsg26910"]
    buffer_m = float(domain.get("fetch_buffer_m", 0.0))
    buffered_utm_bbox = buffer_bbox_utm(study_bbox_utm, buffer_m)
    buffered_wgs84_bbox = transform_bbox(buffered_utm_bbox, project_crs, "EPSG:4326")
    buffered_albers_bbox = transform_bbox(buffered_utm_bbox, project_crs, "EPSG:5070")

    print(f"[00_fetch_data] Project: {project['name']} ({project_crs})")
    print(f"[00_fetch_data] Buffered UTM bbox (+{buffer_m} m): {buffered_utm_bbox}")
    print(f"[00_fetch_data] Buffered WGS84 bbox: {buffered_wgs84_bbox}")

    _fetch_hyetographs(cfg, force=force)
    _fetch_dem(
        output_path=Path(domain["dem_source"]),
        buffered_utm_bbox=buffered_utm_bbox,
        buffered_wgs84_bbox=buffered_wgs84_bbox,
        project_crs=project_crs,
        subgrid_res_m=float(domain["subgrid_resolution_m"]),
        force=force,
    )
    _fetch_nlcd_wcs(
        output_path=Path(domain["nlcd_landcover_source"]),
        coverage_id=NLCD_LANDCOVER_COVERAGE,
        bbox_5070=buffered_albers_bbox,
        force=force,
    )
    _fetch_nlcd_wcs(
        output_path=Path(domain["nlcd_impervious_source"]),
        coverage_id=NLCD_IMPERVIOUS_COVERAGE,
        bbox_5070=buffered_albers_bbox,
        force=force,
    )
    _fetch_usda_hsg(
        output_path=Path(domain["hsg_soils_source"]),
        buffered_wgs84_bbox=buffered_wgs84_bbox,
        force=force,
    )
    _fetch_osm_layers(
        roads_output_path=Path(domain["roads_rail_source"]),
        buildings_output_path=Path(domain["buildings_source"]),
        buffered_wgs84_bbox=buffered_wgs84_bbox,
        force=force,
    )
    _fetch_datasf_flood_risk(
        output_path=Path(domain["validation_reference"]),
        force=force,
    )
    print("[00_fetch_data] Phase 0 fetch complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Phase 0 raw geospatial inputs and generate design-storm hyetographs."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/sf_benchmark.yml"),
        help="Path to scenario configuration YAML.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download and overwrite existing output files.",
    )
    args = parser.parse_args()
    fetch_data(args.config, force=args.force)


if __name__ == "__main__":
    main()
