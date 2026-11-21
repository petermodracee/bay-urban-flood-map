"""Prepare hydro-enforced DEM, Manning's n roughness, and Curve Number grids for SFINCS."""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import yaml
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine, from_origin, rowcol, xy
from rasterio.warp import reproject
from rasterio.windows import Window
from rasterio.windows import transform as window_transform
from shapely.geometry import LineString, Point, box
from shapely.ops import linemerge

HSG_CODE_MAP: dict[str, int] = {"A": 1, "B": 2, "C": 3, "D": 4}

# Pervious TR-55 Curve Numbers indexed by (A, B, C, D) for NLCD land cover classes.
# Developed classes 21-24 use class 21 (urban open space) as the pervious end-member so
# blending with nlcd_impervious_pct does not double-count imperviousness.
PERVIOUS_CN_TABLE: dict[int, tuple[float, float, float, float]] = {
    21: (49.0, 69.0, 79.0, 84.0),
    31: (77.0, 86.0, 91.0, 94.0),
    41: (30.0, 55.0, 70.0, 77.0),
    42: (30.0, 55.0, 70.0, 77.0),
    43: (30.0, 55.0, 70.0, 77.0),
    52: (35.0, 56.0, 70.0, 77.0),
    71: (39.0, 61.0, 74.0, 80.0),
    81: (49.0, 69.0, 79.0, 84.0),
    82: (49.0, 69.0, 79.0, 84.0),
}

ELEVATED_LAYERS = frozenset({"1", "2", "3", "4"})
FREEWAY_HIGHWAYS = frozenset({"motorway", "motorway_link", "trunk", "trunk_link"})
BRIDGE_RAILWAYS = frozenset({"rail", "light_rail"})
SURFACE_HIGHWAYS = frozenset(
    {
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "unclassified",
        "service",
        "primary_link",
        "secondary_link",
        "tertiary_link",
    }
)
VEGETATION_NLCD_CLASSES = frozenset({41, 42, 43, 52, 71, 81, 82, 90, 95})


def parse_hsg_code(hydgrpdcd: Any) -> int:
    """Convert a USDA HSG string (e.g. 'D', 'A/D') to 1=A, 2=B, 3=C, 4=D, or 0=unmapped."""
    if hydgrpdcd is None or (isinstance(hydgrpdcd, float) and math.isnan(hydgrpdcd)):
        return 0
    text = str(hydgrpdcd).strip().upper()
    if not text:
        return 0
    # Dual groups like A/D represent drained/undrained; take undrained class conservatively.
    if "/" in text:
        text = text.split("/")[-1].strip()
    return HSG_CODE_MAP.get(text, 0)


def assert_crs_equals(actual_crs: Any, expected_crs: str, label: str) -> None:
    """Assert that a dataset or configuration CRS matches the expected CRS."""
    if actual_crs is None:
        raise ValueError(f"{label} has no CRS; expected {expected_crs}")
    if CRS.from_user_input(actual_crs) != CRS.from_user_input(expected_crs):
        raise ValueError(
            f"{label} CRS mismatch: got {actual_crs}, expected {expected_crs}"
        )


def compute_target_grid(
    bbox_utm: Sequence[float], resolution_m: float
) -> tuple[int, int, Affine]:
    """Compute target raster (height, width, Affine transform) from EPSG:26910 bbox."""
    if len(bbox_utm) != 4:
        raise ValueError(f"Expected 4 coordinates in bbox, got {len(bbox_utm)}")
    if resolution_m <= 0:
        raise ValueError(f"resolution_m must be positive, got {resolution_m}")
    xmin, ymin, xmax, ymax = (float(v) for v in bbox_utm)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError(f"Invalid bbox ordering: {bbox_utm}")

    width_f = (xmax - xmin) / resolution_m
    height_f = (ymax - ymin) / resolution_m
    width = round(width_f)
    height = round(height_f)
    if not (
        math.isclose(width_f, width, abs_tol=1e-6)
        and math.isclose(height_f, height, abs_tol=1e-6)
    ):
        raise ValueError(
            f"bbox {(xmin, ymin, xmax, ymax)} is not evenly divisible by resolution_m={resolution_m}"
        )
    transform = from_origin(xmin, ymax, resolution_m, resolution_m)
    return height, width, transform


def _extract_linestrings(geom: Any) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if hasattr(geom, "geoms"):
        lines: list[LineString] = []
        for part in geom.geoms:
            lines.extend(_extract_linestrings(part))
        return lines
    return []


def identify_crossing_corridors(
    roads_gdf_utm: gpd.GeoDataFrame,
    bridge_buffer_m: float,
    approach_sample_m: float,
) -> list[tuple[LineString, Point, Point, str]]:
    """Identify surface-street corridors passing under freeway/rail bridges or through tunnels."""
    if roads_gdf_utm.empty:
        return []
    assert_crs_equals(roads_gdf_utm.crs, "EPSG:26910", "roads_gdf_utm")

    layer_str = roads_gdf_utm["layer"].fillna("").astype(str).str.strip()
    is_elevated = roads_gdf_utm["bridge"].notna() | layer_str.isin(ELEVATED_LAYERS)

    freeway_bridges = roads_gdf_utm[
        is_elevated
        & (
            roads_gdf_utm["highway"].isin(FREEWAY_HIGHWAYS)
            | roads_gdf_utm["railway"].isin(BRIDGE_RAILWAYS)
        )
    ]
    surface_streets = roads_gdf_utm[
        roads_gdf_utm["highway"].isin(SURFACE_HIGHWAYS) & (~is_elevated)
    ]

    corridors: list[tuple[LineString, Point, Point, str]] = []

    # Include explicit surface-street tunnels.
    surface_tunnels = surface_streets[surface_streets["tunnel"].isin(["yes"])]
    for _, row in surface_tunnels.iterrows():
        for line in _extract_linestrings(row.geometry):
            if line.length < 1.0:
                continue
            start_pt = Point(line.coords[0])
            end_pt = Point(line.coords[-1])
            label = str(row.get("name") or row.get("highway") or "surface_tunnel")
            corridors.append((line, start_pt, end_pt, f"tunnel:{label}"))

    if freeway_bridges.empty or surface_streets.empty:
        return corridors

    bridge_union = freeway_bridges.geometry.union_all().buffer(bridge_buffer_m)
    surface_lines_raw: list[LineString] = []
    for geom in surface_streets.geometry:
        surface_lines_raw.extend(_extract_linestrings(geom))
    if not surface_lines_raw:
        return corridors

    merged = linemerge(surface_lines_raw)
    merged_lines = _extract_linestrings(merged)

    for parent_line in merged_lines:
        if not parent_line.intersects(bridge_union):
            continue
        inter = parent_line.intersection(bridge_union)
        for seg in _extract_linestrings(inter):
            if seg.length < 1.0:
                continue
            d0 = parent_line.project(Point(seg.coords[0]))
            d1 = parent_line.project(Point(seg.coords[-1]))
            lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
            # Skip segments trapped completely inside the bridge buffer with no outside ground reference.
            if lo <= 0.5 and hi >= parent_line.length - 0.5:
                continue
            if d0 > d1:
                seg = LineString(list(seg.coords)[::-1])
            start_pt = parent_line.interpolate(max(0.0, lo - approach_sample_m))
            end_pt = parent_line.interpolate(
                min(parent_line.length, hi + approach_sample_m)
            )
            corridors.append((seg, start_pt, end_pt, "bridge_underpass"))

    return corridors


def _sample_local_median(
    dem_m: np.ndarray,
    transform: Affine,
    nodata_value: float,
    pt: Point,
) -> float | None:
    height, width = dem_m.shape
    r, c = rowcol(transform, pt.x, pt.y)
    if r < 0 or r >= height or c < 0 or c >= width:
        return None
    r0 = max(0, r - 1)
    r1 = min(height, r + 2)
    c0 = max(0, c - 1)
    c1 = min(width, c + 2)
    patch = dem_m[r0:r1, c0:c1]
    valid = patch[(patch != nodata_value) & np.isfinite(patch)]
    if valid.size == 0:
        return None
    return float(np.median(valid))


def _max_centerline_elevation(
    dem_m: np.ndarray,
    transform: Affine,
    nodata_value: float,
    line: LineString,
) -> float | None:
    height, width = dem_m.shape
    n_samples = max(5, math.ceil(line.length) + 1)
    vals: list[float] = []
    for frac in np.linspace(0.0, 1.0, n_samples):
        pt = line.interpolate(float(frac), normalized=True)
        r, c = rowcol(transform, pt.x, pt.y)
        if 0 <= r < height and 0 <= c < width:
            val = float(dem_m[r, c])
            if val != nodata_value and math.isfinite(val):
                vals.append(val)
    return max(vals) if vals else None


def burn_underpasses_into_dem(
    dem_m: np.ndarray,
    transform: Affine,
    nodata_value: float,
    corridors: Sequence[tuple[LineString, Point, Point, str]],
    carve_half_width_m: float,
    min_bump_m: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    """Burn linear street grades downward through artificial bridge/dam spikes in the DEM."""
    out_dem = dem_m.astype(np.float32, copy=True)
    height, width = out_dem.shape
    corridors_burned = 0
    pixels_modified = 0
    max_carve_depth_m = 0.0

    for line, start_pt, end_pt, _label in corridors:
        z_start = _sample_local_median(out_dem, transform, nodata_value, start_pt)
        z_end = _sample_local_median(out_dem, transform, nodata_value, end_pt)
        if z_start is None or z_end is None:
            continue

        z_center_max = _max_centerline_elevation(out_dem, transform, nodata_value, line)
        if z_center_max is None or (z_center_max - max(z_start, z_end)) < min_bump_m:
            continue

        corridor_poly = line.buffer(carve_half_width_m)
        minx, miny, maxx, maxy = corridor_poly.bounds
        r_top, c_left = rowcol(transform, minx, maxy)
        r_bot, c_right = rowcol(transform, maxx, miny)

        r0 = max(0, min(r_top, r_bot) - 1)
        r1 = min(height, max(r_top, r_bot) + 2)
        c0 = max(0, min(c_left, c_right) - 1)
        c1 = min(width, max(c_left, c_right) + 2)
        if r0 >= r1 or c0 >= c1:
            continue

        win = Window(col_off=c0, row_off=r0, width=c1 - c0, height=r1 - r0)
        win_tf = window_transform(win, transform)
        mask = rasterize(
            [(corridor_poly, 1)],
            out_shape=(r1 - r0, c1 - c0),
            transform=win_tf,
            all_touched=True,
            fill=0,
            dtype=np.uint8,
        )
        local_rows, local_cols = np.where(mask == 1)
        if local_rows.size == 0:
            continue

        corridor_modified = False
        xs, ys = xy(win_tf, local_rows, local_cols, offset="center")
        for lr, lc, x_coord, y_coord in zip(
            local_rows, local_cols, xs, ys, strict=True
        ):
            gr = r0 + int(lr)
            gc = c0 + int(lc)
            current_z = float(out_dem[gr, gc])
            if current_z == nodata_value or not math.isfinite(current_z):
                continue
            t = float(
                line.project(Point(float(x_coord), float(y_coord)), normalized=True)
            )
            z_target = (1.0 - t) * z_start + t * z_end
            if z_target < current_z:
                carve_depth_m = current_z - z_target
                out_dem[gr, gc] = np.float32(z_target)
                pixels_modified += 1
                max_carve_depth_m = max(max_carve_depth_m, carve_depth_m)
                corridor_modified = True

        if corridor_modified:
            corridors_burned += 1

    stats: dict[str, float | int] = {
        "corridors_evaluated": len(corridors),
        "corridors_burned": corridors_burned,
        "pixels_modified": pixels_modified,
        "max_carve_depth_m": round(max_carve_depth_m, 4),
    }
    return out_dem, stats


def apply_building_footprints(
    dem_m: np.ndarray,
    manning_n: np.ndarray,
    building_mask: np.ndarray,
    handling: str,
    building_elevation_bump_m: float,
    building_manning_n: float,
    nodata_value: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Enforce building footprints via elevation bump and/or elevated Manning's n."""
    if handling not in {"elevation_bump", "high_manning"}:
        raise ValueError(f"Unsupported building_handling mode: {handling}")

    out_dem = dem_m.astype(np.float32, copy=True)
    out_manning = manning_n.astype(np.float32, copy=True)

    valid_bldg = (
        building_mask.astype(bool) & (out_dem != nodata_value) & np.isfinite(out_dem)
    )
    if handling == "elevation_bump":
        out_dem[valid_bldg] += np.float32(building_elevation_bump_m)
    out_manning[valid_bldg] = np.float32(building_manning_n)
    return out_dem, out_manning


def compute_curve_number_grid(
    nlcd_landcover: np.ndarray,
    nlcd_impervious_pct: np.ndarray,
    hsg_code: np.ndarray,
    valid_mask: np.ndarray,
    default_urban_hsg: str = "D",
    nodata_value: float = -9999.0,
) -> np.ndarray:
    """Compute TR-55 composite Curve Number grid from NLCD land cover, impervious %, and HSG."""
    default_code = parse_hsg_code(default_urban_hsg)
    if default_code == 0:
        raise ValueError(f"Invalid default_urban_hsg: {default_urban_hsg}")

    hsg_idx = np.where((hsg_code >= 1) & (hsg_code <= 4), hsg_code, default_code) - 1
    f_imp = np.clip(nlcd_impervious_pct.astype(np.float32), 0.0, 100.0) / np.float32(
        100.0
    )

    urban_open_table = np.array(PERVIOUS_CN_TABLE[21], dtype=np.float32)
    pervious_cn = urban_open_table[hsg_idx]
    for nlcd_cls, cn_tuple in PERVIOUS_CN_TABLE.items():
        if nlcd_cls == 21:
            continue
        cls_mask = nlcd_landcover == nlcd_cls
        if np.any(cls_mask):
            cls_table = np.array(cn_tuple, dtype=np.float32)
            pervious_cn[cls_mask] = cls_table[hsg_idx[cls_mask]]

    cn = (np.float32(1.0) - f_imp) * pervious_cn + f_imp * np.float32(98.0)

    cn[nlcd_landcover == 11] = np.float32(100.0)
    wetland_mask = (nlcd_landcover == 90) | (nlcd_landcover == 95)
    cn[wetland_mask] = np.float32(98.0)

    cn = np.clip(cn, np.float32(30.0), np.float32(100.0)).astype(np.float32)
    cn[~valid_mask] = np.float32(nodata_value)
    return cn


def compute_manning_n_grid(
    nlcd_landcover: np.ndarray,
    nlcd_impervious_pct: np.ndarray,
    valid_mask: np.ndarray,
    manning_n_paved: float = 0.020,
    manning_n_water: float = 0.020,
    manning_n_open_space: float = 0.035,
    manning_n_vegetation: float = 0.060,
    nodata_value: float = -9999.0,
) -> np.ndarray:
    """Compute surface Manning's n roughness grid from NLCD land cover and imperviousness."""
    f_imp = np.clip(nlcd_impervious_pct.astype(np.float32), 0.0, 100.0) / np.float32(
        100.0
    )
    manning = np.full(
        nlcd_landcover.shape, np.float32(manning_n_open_space), dtype=np.float32
    )

    manning[nlcd_landcover == 11] = np.float32(manning_n_water)
    manning[nlcd_landcover == 31] = np.float32(0.030)

    developed_mask = (
        (nlcd_landcover == 22) | (nlcd_landcover == 23) | (nlcd_landcover == 24)
    )
    manning[developed_mask] = (np.float32(1.0) - f_imp[developed_mask]) * np.float32(
        manning_n_open_space
    ) + f_imp[developed_mask] * np.float32(manning_n_paved)

    veg_mask = np.isin(nlcd_landcover, list(VEGETATION_NLCD_CLASSES))
    manning[veg_mask] = np.float32(manning_n_vegetation)

    manning[~valid_mask] = np.float32(nodata_value)
    return manning


def _reproject_to_grid(
    src_path: Path,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: str,
    resampling: Resampling,
    dst_dtype: np.dtype[Any],
    dst_nodata: float | None,
    expected_src_crs: str | None = None,
    ignore_src_nodata: bool = False,
) -> np.ndarray:
    with rasterio.open(src_path) as src:
        if expected_src_crs is not None:
            assert_crs_equals(src.crs, expected_src_crs, str(src_path))
        elif src.crs is None:
            raise ValueError(f"{src_path} is missing CRS")

        src_data = src.read(1)
        src_nodata = None if ignore_src_nodata else src.nodata
        fill_val = 0 if dst_nodata is None else dst_nodata
        dst_data = np.full(dst_shape, fill_val, dtype=dst_dtype)

        reproject(
            source=src_data,
            destination=dst_data,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src_nodata,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=None if ignore_src_nodata else dst_nodata,
            resampling=resampling,
        )
    return dst_data


def _write_geotiff(
    output_path: Path,
    data: np.ndarray,
    transform: Affine,
    crs: str,
    nodata_value: float,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.tif")
    height, width = data.shape
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": nodata_value,
        "compress": "DEFLATE",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(tmp_path, "w", **profile) as dst:
        dst.write(data.astype(np.float32, copy=False), 1)
    tmp_path.replace(output_path)

    with rasterio.open(output_path) as verify_ds:
        assert_crs_equals(verify_ds.crs, crs, str(output_path))


def prep_terrain(config_path: Path, force: bool = False) -> None:
    """Load config and prepare hydro-enforced terrain, roughness, and Curve Number grids."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    project = cfg["project"]
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]

    project_crs = str(project["crs"])
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")

    hydro_dem_path = Path(tprep["hydro_dem_file"])
    manning_n_path = Path(tprep["manning_n_file"])
    curve_number_path = Path(tprep["curve_number_file"])
    if (
        not force
        and hydro_dem_path.exists()
        and manning_n_path.exists()
        and curve_number_path.exists()
    ):
        print(
            "[01_prep_terrain] Skipping existing processed terrain grids (use --force to rebuild)."
        )
        return

    bbox_utm = domain["bbox_epsg26910"]
    subgrid_res_m = float(domain["subgrid_resolution_m"])
    nodata_value = float(tprep.get("nodata_value", -9999.0))
    height, width, dst_transform = compute_target_grid(bbox_utm, subgrid_res_m)
    dst_shape = (height, width)

    print(
        f"[01_prep_terrain] Project: {project['name']} ({project_crs}), "
        f"grid={width}x{height} @ {subgrid_res_m} m"
    )

    # 1. Reproject/clip rasters to target EPSG:26910 grid.
    dem_m = _reproject_to_grid(
        src_path=Path(domain["dem_source"]),
        dst_shape=dst_shape,
        dst_transform=dst_transform,
        dst_crs=project_crs,
        resampling=Resampling.bilinear,
        dst_dtype=np.dtype(np.float32),
        dst_nodata=nodata_value,
        expected_src_crs=project_crs,
    )
    nlcd_lc = _reproject_to_grid(
        src_path=Path(domain["nlcd_landcover_source"]),
        dst_shape=dst_shape,
        dst_transform=dst_transform,
        dst_crs=project_crs,
        resampling=Resampling.nearest,
        dst_dtype=np.dtype(np.uint8),
        dst_nodata=0,
    )
    # MRLC NLCD impervious uses 0 as NoData for 0% impervious; treat as 0% rather than missing.
    nlcd_imp = _reproject_to_grid(
        src_path=Path(domain["nlcd_impervious_source"]),
        dst_shape=dst_shape,
        dst_transform=dst_transform,
        dst_crs=project_crs,
        resampling=Resampling.nearest,
        dst_dtype=np.dtype(np.uint8),
        dst_nodata=None,
        ignore_src_nodata=True,
    )

    valid_mask = (dem_m != nodata_value) & np.isfinite(dem_m)

    # 2. Reproject/clip vector layers to EPSG:26910.
    study_poly = box(*bbox_utm)
    roads_clip_poly = study_poly.buffer(50.0)

    hsg_gdf = gpd.read_file(Path(domain["hsg_soils_source"]))
    if hsg_gdf.crs is None:
        raise ValueError("hsg_soils_source is missing CRS")
    hsg_utm = hsg_gdf.to_crs(project_crs)
    hsg_utm = hsg_utm[hsg_utm.intersects(study_poly)].copy()
    hsg_utm["hsg_code"] = hsg_utm["hydgrpdcd"].map(parse_hsg_code)
    hsg_shapes = [
        (geom, int(code))
        for geom, code in zip(hsg_utm.geometry, hsg_utm["hsg_code"], strict=True)
        if geom is not None and not geom.is_empty and int(code) > 0
    ]
    hsg_grid = (
        rasterize(
            hsg_shapes,
            out_shape=dst_shape,
            transform=dst_transform,
            fill=0,
            dtype=np.uint8,
        )
        if hsg_shapes
        else np.zeros(dst_shape, dtype=np.uint8)
    )

    bldgs_gdf = gpd.read_file(Path(domain["buildings_source"]))
    if bldgs_gdf.crs is None:
        raise ValueError("buildings_source is missing CRS")
    bldgs_utm = bldgs_gdf.to_crs(project_crs)
    bldgs_utm = bldgs_utm[bldgs_utm.intersects(study_poly)]
    bldg_shapes = [
        (geom, 1)
        for geom in bldgs_utm.geometry
        if geom is not None and not geom.is_empty
    ]
    building_mask = (
        rasterize(
            bldg_shapes,
            out_shape=dst_shape,
            transform=dst_transform,
            fill=0,
            dtype=np.uint8,
        ).astype(bool)
        if bldg_shapes
        else np.zeros(dst_shape, dtype=bool)
    )

    roads_gdf = gpd.read_file(Path(domain["roads_rail_source"]))
    if roads_gdf.crs is None:
        raise ValueError("roads_rail_source is missing CRS")
    roads_utm = roads_gdf.to_crs(project_crs)
    roads_utm = roads_utm[roads_utm.intersects(roads_clip_poly)].copy()

    # 3. Compute Curve Number & Manning's n grids, enforce buildings, then burn underpasses.
    cn_grid = compute_curve_number_grid(
        nlcd_landcover=nlcd_lc,
        nlcd_impervious_pct=nlcd_imp,
        hsg_code=hsg_grid,
        valid_mask=valid_mask,
        default_urban_hsg=str(tprep.get("default_urban_hsg", "D")),
        nodata_value=nodata_value,
    )
    manning_grid = compute_manning_n_grid(
        nlcd_landcover=nlcd_lc,
        nlcd_impervious_pct=nlcd_imp,
        valid_mask=valid_mask,
        manning_n_paved=float(tprep.get("manning_n_paved", 0.020)),
        manning_n_water=float(tprep.get("manning_n_water", 0.020)),
        manning_n_open_space=float(tprep.get("manning_n_open_space", 0.035)),
        manning_n_vegetation=float(tprep.get("manning_n_vegetation", 0.060)),
        nodata_value=nodata_value,
    )

    dem_bldg_m, manning_bldg = apply_building_footprints(
        dem_m=dem_m,
        manning_n=manning_grid,
        building_mask=building_mask,
        handling=str(tprep.get("building_handling", "elevation_bump")),
        building_elevation_bump_m=float(tprep.get("building_elevation_bump_m", 3.0)),
        building_manning_n=float(tprep.get("building_manning_n", 0.30)),
        nodata_value=nodata_value,
    )

    corridors = identify_crossing_corridors(
        roads_gdf_utm=roads_utm,
        bridge_buffer_m=float(tprep.get("bridge_buffer_m", 12.0)),
        approach_sample_m=float(tprep.get("approach_sample_m", 10.0)),
    )
    hydro_dem_m, burn_stats = burn_underpasses_into_dem(
        dem_m=dem_bldg_m,
        transform=dst_transform,
        nodata_value=nodata_value,
        corridors=corridors,
        carve_half_width_m=float(tprep.get("carve_half_width_m", 5.0)),
        min_bump_m=float(tprep.get("min_bump_m", 0.15)),
    )

    print(
        "[01_prep_terrain] Underpass burning stats: "
        f"evaluated={burn_stats['corridors_evaluated']}, "
        f"burned={burn_stats['corridors_burned']}, "
        f"pixels_modified={burn_stats['pixels_modified']}, "
        f"max_carve_depth_m={burn_stats['max_carve_depth_m']}"
    )

    _write_geotiff(
        hydro_dem_path, hydro_dem_m, dst_transform, project_crs, nodata_value
    )
    _write_geotiff(
        manning_n_path, manning_bldg, dst_transform, project_crs, nodata_value
    )
    _write_geotiff(curve_number_path, cn_grid, dst_transform, project_crs, nodata_value)

    print(
        f"[01_prep_terrain] Wrote processed outputs -> "
        f"{hydro_dem_path}, {manning_n_path}, {curve_number_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare terrain inputs for SFINCS.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/sf_benchmark.yml"),
        help="Path to scenario configuration YAML.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute and overwrite existing processed terrain grids.",
    )
    args = parser.parse_args()
    prep_terrain(args.config, force=args.force)


if __name__ == "__main__":
    main()
