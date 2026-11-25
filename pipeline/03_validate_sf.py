"""Validate SFINCS max flood depth against the SFPUC 100-Year Storm Flood Risk Map."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import rasterio  # rasterio must precede geopandas/pyogrio to avoid GDAL 3.10 double-free

# isort: split
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
import yaml
from hydromt_sfincs import SfincsModel
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine, from_origin, rowcol
from rasterio.warp import reproject
from scipy import ndimage, stats
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree

REPO_ROOT = Path(__file__).resolve().parent.parent

SF_NEIGHBORHOOD_BOXES_UTM: dict[str, tuple[float, float, float, float]] = {
    "Mission": (550400.0, 4177000.0, 552400.0, 4179600.0),
    "Alemany": (550000.0, 4175400.0, 552600.0, 4177200.0),
    "SOMA / Mission Bay": (551600.0, 4179200.0, 554400.0, 4181600.0),
    "Marina / Crissy Field": (548200.0, 4183000.0, 551000.0, 4184600.0),
    "Sunset / Lake Merced": (543200.0, 4174000.0, 547600.0, 4180200.0),
    "Bayview / Islais Creek": (552200.0, 4174800.0, 555200.0, 4178200.0),
    "Visitacion Valley": (550600.0, 4173400.0, 553600.0, 4175000.0),
}


def _load_pipeline_module(script_name: str, module_name: str) -> Any:
    script_path = REPO_ROOT / "pipeline" / script_name
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_crs_equals(actual_crs: Any, expected_crs: str, label: str) -> None:
    """Assert that a dataset or config CRS matches the expected CRS."""
    if actual_crs is None:
        raise ValueError(f"{label} is missing CRS; expected {expected_crs}")
    if CRS.from_user_input(actual_crs) != CRS.from_user_input(expected_crs):
        raise ValueError(
            f"{label} CRS mismatch: got {actual_crs}, expected {expected_crs}"
        )


def downscale_zsmax_to_depth_m(
    zsmax_south_up: np.ndarray,
    src_transform: Affine,
    src_crs: Any,
    dem_m: np.ndarray,
    dst_transform: Affine,
    dst_crs: Any,
    dem_nodata: float = -9999.0,
) -> np.ndarray:
    """Bilinearly downscale south-up SFINCS zsmax onto a north-up 1m DEM to compute depth (m)."""
    assert_crs_equals(src_crs, "EPSG:26910", "zsmax src_crs")
    assert_crs_equals(dst_crs, "EPSG:26910", "dem dst_crs")

    # SFINCS NetCDF rows run south-to-north; flip vertically to match north-up GeoTIFFs.
    zsmax_north_up = np.ascontiguousarray(zsmax_south_up[::-1, :], dtype=np.float32)
    zsmax_1m = np.full(dem_m.shape, np.nan, dtype=np.float32)

    reproject(
        source=zsmax_north_up,
        destination=zsmax_1m,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=np.nan,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )

    valid_dem = (dem_m != dem_nodata) & np.isfinite(dem_m)
    has_water = valid_dem & np.isfinite(zsmax_1m)
    depth_m = np.where(has_water, np.maximum(0.0, zsmax_1m - dem_m), 0.0).astype(
        np.float32
    )
    depth_m = np.where(valid_dem, depth_m, np.float32(dem_nodata)).astype(np.float32)
    return depth_m


def compute_flood_metrics(
    depth_m: np.ndarray,
    obs_mask: np.ndarray,
    valid_mask: np.ndarray,
    depth_threshold_m: float = 0.1524,
) -> dict[str, float | int]:
    """Compute Hit Rate, False Alarm Ratio, and Critical Success Index over valid_mask."""
    valid = valid_mask.astype(bool)
    mod = (depth_m >= depth_threshold_m) & valid
    obs = obs_mask.astype(bool) & valid

    tp = int(np.sum(mod & obs))
    fp = int(np.sum(mod & (~obs)))
    fn = int(np.sum((~mod) & obs))

    hit_rate = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    false_alarm_ratio = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    csi = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "hit_rate": round(hit_rate, 6),
        "false_alarm_ratio": round(false_alarm_ratio, 6),
        "csi": round(csi, 6),
    }


def compute_flood_metrics_from_mask(
    mod_mask: np.ndarray,
    obs_mask: np.ndarray,
    valid_mask: np.ndarray,
) -> dict[str, float | int]:
    """Compute Hit Rate, False Alarm Ratio, and CSI from a pre-filtered boolean model mask."""
    valid = valid_mask.astype(bool)
    mod = mod_mask.astype(bool) & valid
    obs = obs_mask.astype(bool) & valid

    tp = int(np.sum(mod & obs))
    fp = int(np.sum(mod & (~obs)))
    fn = int(np.sum((~mod) & obs))

    hit_rate = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    false_alarm_ratio = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    csi = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "hit_rate": round(hit_rate, 6),
        "false_alarm_ratio": round(false_alarm_ratio, 6),
        "csi": round(csi, 6),
    }


def filter_deep_and_contiguous_mask(
    depth_m: np.ndarray,
    valid_mask: np.ndarray,
    cell_area_m2: float,
    depth_threshold_m: float = 0.1524,
    min_area_m2: float = 5574.0,
) -> np.ndarray:
    """Keep only connected flooded components (>= depth_threshold_m) with area >= min_area_m2."""
    raw_mask = (
        np.isfinite(depth_m)
        & (depth_m >= float(depth_threshold_m))
        & valid_mask.astype(bool)
    )
    if float(min_area_m2) <= 0.0 or not np.any(raw_mask):
        return raw_mask
    min_cells = math.ceil(float(min_area_m2) / float(cell_area_m2))
    labeled, num_features = ndimage.label(
        raw_mask, structure=np.ones((3, 3), dtype=int)
    )
    if num_features == 0:
        return np.zeros_like(raw_mask, dtype=bool)
    counts = np.bincount(labeled.ravel())
    keep_labels = np.where(counts >= min_cells)[0]
    keep_labels = keep_labels[keep_labels != 0]
    if keep_labels.size == 0:
        return np.zeros_like(raw_mask, dtype=bool)
    return np.isin(labeled, keep_labels)


def compute_benchmark_depth_diagnostics(
    depth_m: np.ndarray,
    obs_mask: np.ndarray,
    valid_mask: np.ndarray,
    nodata_value: float = -9999.0,
) -> dict[str, Any]:
    """Compute continuous modeled depth statistics inside vs. outside benchmark polygons."""
    valid = valid_mask.astype(bool) & np.isfinite(depth_m) & (depth_m != nodata_value)
    obs = obs_mask.astype(bool) & valid
    non_obs = (~obs) & valid

    def _stats(arr: np.ndarray) -> dict[str, float | int]:
        if arr.size == 0:
            return {
                "n_cells": 0,
                "mean_m": 0.0,
                "median_m": 0.0,
                "p75_m": 0.0,
                "p90_m": 0.0,
                "frac_ge_05m": 0.0,
                "frac_ge_1524m": 0.0,
            }
        return {
            "n_cells": int(arr.size),
            "mean_m": round(float(np.mean(arr)), 4),
            "median_m": round(float(np.median(arr)), 4),
            "p75_m": round(float(np.percentile(arr, 75)), 4),
            "p90_m": round(float(np.percentile(arr, 90)), 4),
            "frac_ge_05m": round(float(np.mean(arr >= 0.05)), 4),
            "frac_ge_1524m": round(float(np.mean(arr >= 0.1524)), 4),
        }

    return {
        "inside_benchmark": _stats(depth_m[obs]),
        "outside_benchmark": _stats(depth_m[non_obs]),
    }


SFCD_TO_NAVD88_FT = 11.35
FT_TO_M = 0.3048


def _safe_corr_pair(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    """Return (pearson_r, spearman_rho), or None where variance is zero or n < 3."""
    if x.size < 3 or np.std(x) == 0.0 or np.std(y) == 0.0:
        return None, None
    r_val, _ = stats.pearsonr(x, y)
    rho_val, _ = stats.spearmanr(x, y)
    r_out = round(float(r_val), 4) if np.isfinite(r_val) else None
    rho_out = round(float(rho_val), 4) if np.isfinite(rho_val) else None
    return r_out, rho_out


def compute_sfpuc_elevation_metrics(
    model_depth_m: np.ndarray,
    dem_m: np.ndarray,
    sfpuc_wse_navd88_m: np.ndarray,
    valid_land_mask: np.ndarray,
    min_model_depth_m: float = 0.05,
    nodata_value: float = -9999.0,
) -> dict[str, Any]:
    """Compare modeled WSE and depth against rasterized SFPUC flood elevations (NAVD88 m)."""
    valid = (
        valid_land_mask.astype(bool)
        & np.isfinite(sfpuc_wse_navd88_m)
        & np.isfinite(dem_m)
        & (dem_m != nodata_value)
        & np.isfinite(model_depth_m)
        & (model_depth_m != nodata_value)
    )

    sfpuc_depth_m = np.maximum(0.0, sfpuc_wse_navd88_m - dem_m)
    model_wse_m = dem_m + model_depth_m

    def _empty_all() -> dict[str, Any]:
        return {
            "n_cells": 0,
            "sfpuc_depth_mean_m": None,
            "sfpuc_depth_median_m": None,
            "model_depth_mean_m": None,
            "model_depth_median_m": None,
            "depth_rmse_m": None,
            "depth_mae_m": None,
            "depth_bias_m": None,
            "depth_spearman_rho": None,
        }

    def _empty_wet() -> dict[str, Any]:
        return {
            "n_cells": 0,
            "wse_rmse_m": None,
            "wse_mae_m": None,
            "wse_bias_m": None,
            "wse_pearson_r": None,
            "wse_spearman_rho": None,
            "depth_rmse_m": None,
            "depth_mae_m": None,
            "depth_bias_m": None,
            "depth_pearson_r": None,
            "depth_spearman_rho": None,
        }

    if not np.any(valid):
        return {"all_zone_cells": _empty_all(), "mutually_wet_cells": _empty_wet()}

    mod_d_all = model_depth_m[valid].astype(np.float64)
    sf_d_all = sfpuc_depth_m[valid].astype(np.float64)
    diff_d_all = mod_d_all - sf_d_all
    _, rho_d_all = _safe_corr_pair(mod_d_all, sf_d_all)

    all_zone = {
        "n_cells": int(mod_d_all.size),
        "sfpuc_depth_mean_m": round(float(np.mean(sf_d_all)), 4),
        "sfpuc_depth_median_m": round(float(np.median(sf_d_all)), 4),
        "model_depth_mean_m": round(float(np.mean(mod_d_all)), 4),
        "model_depth_median_m": round(float(np.median(mod_d_all)), 4),
        "depth_rmse_m": round(float(np.sqrt(np.mean(diff_d_all**2))), 4),
        "depth_mae_m": round(float(np.mean(np.abs(diff_d_all))), 4),
        "depth_bias_m": round(float(np.mean(diff_d_all)), 4),
        "depth_spearman_rho": rho_d_all,
    }

    wet = (
        valid
        & (model_depth_m >= min_model_depth_m)
        & (sfpuc_depth_m >= min_model_depth_m)
    )
    if not np.any(wet):
        return {"all_zone_cells": all_zone, "mutually_wet_cells": _empty_wet()}

    mod_w_wet = model_wse_m[wet].astype(np.float64)
    sf_w_wet = sfpuc_wse_navd88_m[wet].astype(np.float64)
    diff_w_wet = mod_w_wet - sf_w_wet
    r_w_wet, rho_w_wet = _safe_corr_pair(mod_w_wet, sf_w_wet)

    mod_d_wet = model_depth_m[wet].astype(np.float64)
    sf_d_wet = sfpuc_depth_m[wet].astype(np.float64)
    diff_d_wet = mod_d_wet - sf_d_wet
    r_d_wet, rho_d_wet = _safe_corr_pair(mod_d_wet, sf_d_wet)

    mutually_wet = {
        "n_cells": int(mod_w_wet.size),
        "wse_rmse_m": round(float(np.sqrt(np.mean(diff_w_wet**2))), 4),
        "wse_mae_m": round(float(np.mean(np.abs(diff_w_wet))), 4),
        "wse_bias_m": round(float(np.mean(diff_w_wet)), 4),
        "wse_pearson_r": r_w_wet,
        "wse_spearman_rho": rho_w_wet,
        "depth_rmse_m": round(float(np.sqrt(np.mean(diff_d_wet**2))), 4),
        "depth_mae_m": round(float(np.mean(np.abs(diff_d_wet))), 4),
        "depth_bias_m": round(float(np.mean(diff_d_wet)), 4),
        "depth_pearson_r": r_d_wet,
        "depth_spearman_rho": rho_d_wet,
    }

    return {"all_zone_cells": all_zone, "mutually_wet_cells": mutually_wet}


def find_top_false_positive_clusters(
    depth_m: np.ndarray,
    obs_mask: np.ndarray,
    valid_mask: np.ndarray,
    dst_transform: Affine,
    depth_threshold_m: float = 0.1524,
    downsample_factor: int = 10,
    top_n: int = 10,
) -> list[dict[str, float]]:
    """Identify the largest connected false-positive clusters and their UTM centroids."""
    fp_mask = (
        (depth_m >= depth_threshold_m)
        & (~obs_mask.astype(bool))
        & valid_mask.astype(bool)
    )
    height, width = fp_mask.shape

    factor = downsample_factor
    if factor > 1 and height >= factor and width >= factor:
        bh = height // factor
        bw = width // factor
        trimmed = fp_mask[: bh * factor, : bw * factor]
        grid = trimmed.reshape(bh, factor, bw, factor).mean(axis=(1, 3)) > 0.25
    else:
        factor = 1
        grid = fp_mask

    labeled, num_clusters = ndimage.label(grid)
    if num_clusters == 0:
        return []

    sizes = ndimage.sum(grid, labeled, range(1, num_clusters + 1))
    order = np.argsort(sizes)[::-1][:top_n]
    cell_area_m2 = (abs(dst_transform.a) * factor) * (abs(dst_transform.e) * factor)

    clusters: list[dict[str, float]] = []
    for cid in order:
        cluster_idx = int(cid) + 1
        rows, cols = np.where(labeled == cluster_idx)
        if rows.size == 0:
            continue
        col_px = (float(cols.mean()) + 0.5) * factor
        row_px = (float(rows.mean()) + 0.5) * factor
        centroid_x_m, centroid_y_m = dst_transform @ (col_px, row_px)
        clusters.append(
            {
                "area_m2": round(float(sizes[cid]) * cell_area_m2, 1),
                "centroid_x_m": round(float(centroid_x_m), 1),
                "centroid_y_m": round(float(centroid_y_m), 1),
            }
        )
    return clusters


def build_validation_summary(
    region: str,
    drainage_rate_mm_hr: float,
    manning_n_paved: float,
    depth_threshold_m: float,
    metrics: Mapping[str, float | int],
) -> dict[str, Any]:
    """Format validation results according to docs/DATA_CONTRACTS.md Section 4."""
    return {
        "region": str(region),
        "drainage_rate_mm_hr": float(drainage_rate_mm_hr),
        "manning_n_paved": float(manning_n_paved),
        "depth_threshold_m": float(depth_threshold_m),
        "metrics": {
            "hit_rate": float(metrics["hit_rate"]),
            "false_alarm_ratio": float(metrics["false_alarm_ratio"]),
            "csi": float(metrics["csi"]),
        },
    }


def sample_max_depth_within_buffers(
    depth_m: np.ndarray,
    transform: Affine,
    nodata_value: float,
    xs_m: Sequence[float] | np.ndarray,
    ys_m: Sequence[float] | np.ndarray,
    radii_m: Sequence[float],
) -> dict[float, np.ndarray]:
    """Sample maximum valid depth within exact circular UTM buffers around query points."""
    xs_arr = np.asarray(xs_m, dtype=np.float64)
    ys_arr = np.asarray(ys_m, dtype=np.float64)
    n_pts = xs_arr.size
    results: dict[float, np.ndarray] = {
        float(r): np.zeros(n_pts, dtype=np.float32) for r in radii_m
    }
    if n_pts == 0 or not radii_m:
        return results

    height, width = depth_m.shape
    res_x = abs(float(transform.a))
    res_y = abs(float(transform.e))
    valid = (depth_m != nodata_value) & np.isfinite(depth_m)

    sorted_radii = sorted(float(r) for r in radii_m)
    max_r = max(sorted_radii)
    pad_r = math.ceil(max_r / res_y) + 1 if max_r > 0 else 0
    pad_c = math.ceil(max_r / res_x) + 1 if max_r > 0 else 0

    for i, (x_pt, y_pt) in enumerate(zip(xs_arr, ys_arr, strict=True)):
        r_c, c_c = rowcol(transform, float(x_pt), float(y_pt))
        center_valid = 0 <= r_c < height and 0 <= c_c < width
        center_val = (
            float(depth_m[r_c, c_c]) if center_valid and valid[r_c, c_c] else 0.0
        )

        if max_r <= 0.0:
            for r_m in sorted_radii:
                results[r_m][i] = np.float32(center_val)
            continue

        r0 = max(0, r_c - pad_r)
        r1 = min(height, r_c + pad_r + 1)
        c0 = max(0, c_c - pad_c)
        c1 = min(width, c_c + pad_c + 1)
        if r0 >= r1 or c0 >= c1:
            continue

        sub_valid = valid[r0:r1, c0:c1]
        if not sub_valid.any():
            continue
        sub_depth = depth_m[r0:r1, c0:c1]

        x_centers = (
            transform.c + (np.arange(c0, c1, dtype=np.float64) + 0.5) * transform.a
        )
        y_centers = (
            transform.f + (np.arange(r0, r1, dtype=np.float64) + 0.5) * transform.e
        )
        dist2 = (y_centers[:, None] - y_pt) ** 2 + (x_centers[None, :] - x_pt) ** 2

        for r_m in sorted_radii:
            if r_m <= 0.0:
                results[r_m][i] = np.float32(center_val)
                continue
            in_circle = (dist2 <= r_m * r_m) & sub_valid
            if in_circle.any():
                val = float(np.max(sub_depth[in_circle]))
                results[r_m][i] = np.float32(max(val, center_val))
            else:
                results[r_m][i] = np.float32(center_val)

    return results


def sample_mask_within_buffers(
    mask_bool: np.ndarray,
    transform: Affine,
    xs_m: Sequence[float] | np.ndarray,
    ys_m: Sequence[float] | np.ndarray,
    radii_m: Sequence[float],
) -> dict[float, np.ndarray]:
    """Return boolean capture arrays indicating whether mask_bool has True inside each buffer."""
    sampled = sample_max_depth_within_buffers(
        depth_m=mask_bool.astype(np.float32),
        transform=transform,
        nodata_value=-9999.0,
        xs_m=xs_m,
        ys_m=ys_m,
        radii_m=radii_m,
    )
    return {r_m: vals >= 0.5 for r_m, vals in sampled.items()}


def compute_roc_auc(
    pos_scores: Sequence[float] | np.ndarray,
    neg_scores: Sequence[float] | np.ndarray,
) -> float:
    """Compute Mann-Whitney U ROC-AUC comparing positive vs. negative control scores."""
    pos = np.asarray(pos_scores, dtype=np.float64)
    neg = np.asarray(neg_scores, dtype=np.float64)
    pos = pos[np.isfinite(pos)]
    neg = neg[np.isfinite(neg)]
    n_pos = pos.size
    n_neg = neg.size
    if n_pos == 0 or n_neg == 0:
        return 0.5
    combined = np.concatenate([pos, neg])
    ranks = stats.rankdata(combined, method="average")
    pos_rank_sum = float(np.sum(ranks[:n_pos]))
    auc = (pos_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return round(float(auc), 6)


def compute_land_valid_mask(
    dem_m: np.ndarray,
    dem_nodata: float = -9999.0,
    min_land_elevation_m: float = 0.5,
    nlcd_landcover: np.ndarray | None = None,
    exclude_open_water: bool = True,
) -> np.ndarray:
    """Build valid urban land mask excluding DEM NoData, sub-tidal cells, and NLCD open water."""
    valid_dem = (dem_m != dem_nodata) & np.isfinite(dem_m)
    land_valid_mask = valid_dem & (dem_m >= float(min_land_elevation_m))
    if exclude_open_water and nlcd_landcover is not None:
        land_valid_mask = land_valid_mask & (nlcd_landcover != 11)
    return land_valid_mask


def load_land_valid_mask(
    hydro_dem_path: Path,
    project_crs: str,
    min_land_elevation_m: float = 0.5,
    nlcd_landcover_path: Path | None = None,
    exclude_open_water: bool = True,
) -> tuple[np.ndarray, Affine]:
    """Load hydro DEM (+ optional NLCD) and build the valid urban land mask and transform."""
    with rasterio.open(hydro_dem_path) as dem_ds:
        assert_crs_equals(dem_ds.crs, project_crs, str(hydro_dem_path))
        dem_m = dem_ds.read(1).astype(np.float32)
        dem_nodata = dem_ds.nodata if dem_ds.nodata is not None else -9999.0
        dst_transform = dem_ds.transform

    nlcd_arr: np.ndarray | None = None
    if (
        exclude_open_water
        and nlcd_landcover_path is not None
        and nlcd_landcover_path.exists()
    ):
        nlcd_arr = np.zeros(dem_m.shape, dtype=np.uint8)
        with rasterio.open(nlcd_landcover_path) as nlcd_ds:
            reproject(
                source=nlcd_ds.read(1),
                destination=nlcd_arr,
                src_transform=nlcd_ds.transform,
                src_crs=nlcd_ds.crs,
                dst_transform=dst_transform,
                dst_crs=project_crs,
                resampling=Resampling.nearest,
            )

    land_valid_mask = compute_land_valid_mask(
        dem_m=dem_m,
        dem_nodata=dem_nodata,
        min_land_elevation_m=min_land_elevation_m,
        nlcd_landcover=nlcd_arr,
        exclude_open_water=exclude_open_water,
    )
    return land_valid_mask, dst_transform


def rasterize_benchmark_polygons(
    gdf_utm: gpd.GeoDataFrame,
    out_shape: tuple[int, int],
    transform: Affine,
) -> np.ndarray:
    """Rasterize polygon benchmark geometries in target UTM CRS to a boolean mask."""
    shapes = [
        (geom, 1) for geom in gdf_utm.geometry if geom is not None and not geom.is_empty
    ]
    if not shapes:
        return np.zeros(out_shape, dtype=bool)
    return rasterize(
        shapes,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)


def compute_corridor_benchmark_metrics(
    depth_m: np.ndarray,
    obs_mask: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    depth_threshold_m: float = 0.1524,
    corridors_utm: Mapping[
        str, tuple[float, float, float, float]
    ] = SF_NEIGHBORHOOD_BOXES_UTM,
    label_key: str = "corridor",
) -> list[dict[str, Any]]:
    """Compute Tier B polygon-to-raster validation metrics across named UTM corridor bboxes."""
    height, width = depth_m.shape
    rows: list[dict[str, Any]] = []
    for name, (xmin, ymin, xmax, ymax) in corridors_utm.items():
        r_top, c_left = rowcol(transform, xmin, ymax)
        r_bot, c_right = rowcol(transform, xmax, ymin)
        r0 = max(0, min(r_top, r_bot))
        r1 = min(height, max(r_top, r_bot))
        c0 = max(0, min(c_left, c_right))
        c1 = min(width, max(c_left, c_right))
        if r0 >= r1 or c0 >= c1:
            continue

        sub_valid = valid_land_mask[r0:r1, c0:c1]
        sub_obs = obs_mask[r0:r1, c0:c1]
        sub_depth = depth_m[r0:r1, c0:c1]
        m = compute_flood_metrics(
            depth_m=sub_depth,
            obs_mask=sub_obs,
            valid_mask=sub_valid,
            depth_threshold_m=depth_threshold_m,
        )
        rows.append(
            {
                label_key: name,
                "bbox_epsg26910": [xmin, ymin, xmax, ymax],
                "land_cells": int(np.sum(sub_valid)),
                "obs_cells": int(np.sum(sub_obs & sub_valid)),
                "mod_cells": int(np.sum((sub_depth >= depth_threshold_m) & sub_valid)),
                **m,
            }
        )
    return rows


def compute_neighborhood_sfpuc_metrics(
    depth_m: np.ndarray,
    obs_mask: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    depth_threshold_m: float = 0.1524,
    neighborhoods_utm: Mapping[
        str, tuple[float, float, float, float]
    ] = SF_NEIGHBORHOOD_BOXES_UTM,
) -> list[dict[str, Any]]:
    """Compute SFPUC 100-year validation metrics across key San Francisco corridor bboxes."""
    return compute_corridor_benchmark_metrics(
        depth_m=depth_m,
        obs_mask=obs_mask,
        valid_land_mask=valid_land_mask,
        transform=transform,
        depth_threshold_m=depth_threshold_m,
        corridors_utm=neighborhoods_utm,
        label_key="neighborhood",
    )


def _summarize_point_group(
    indices: np.ndarray,
    ar_depths_eval: np.ndarray,
    sfpuc_eval: np.ndarray,
    ar_depths_25: np.ndarray | None = None,
) -> dict[str, Any]:
    count = int(indices.size)
    if count == 0:
        return {
            "count": 0,
            "ar_05_capture": 0.0,
            "ar_15_capture": 0.0,
            "sfpuc_capture": 0.0,
            "ar_05_capture_25m": 0.0,
        }
    d_eval = ar_depths_eval[indices]
    s_eval = sfpuc_eval[indices]
    res: dict[str, Any] = {
        "count": count,
        "ar_05_capture": round(float(np.mean(d_eval >= 0.05)), 4),
        "ar_15_capture": round(float(np.mean(d_eval >= 0.1524)), 4),
        "sfpuc_capture": round(float(np.mean(s_eval)), 4),
    }
    if ar_depths_25 is not None:
        res["ar_05_capture_25m"] = round(
            float(np.mean(ar_depths_25[indices] >= 0.05)), 4
        )
    return res


def extract_valid_land_points(
    points_gdf_utm: gpd.GeoDataFrame,
    valid_land_mask: np.ndarray,
    transform: Affine,
) -> tuple[gpd.GeoDataFrame, np.ndarray, np.ndarray]:
    """Filter a UTM GeoDataFrame to points inside valid_land_mask and return (gdf, xs_m, ys_m)."""
    height, width = valid_land_mask.shape
    keep_idx: list[int] = []
    xs_list: list[float] = []
    ys_list: list[float] = []

    for idx, geom in enumerate(points_gdf_utm.geometry):
        if geom is None or geom.is_empty:
            continue
        pt = geom.centroid if geom.geom_type != "Point" else geom
        r, c = rowcol(transform, float(pt.x), float(pt.y))
        if 0 <= r < height and 0 <= c < width and bool(valid_land_mask[r, c]):
            keep_idx.append(idx)
            xs_list.append(float(pt.x))
            ys_list.append(float(pt.y))

    filtered = points_gdf_utm.iloc[keep_idx].reset_index(drop=True)
    return (
        filtered,
        np.asarray(xs_list, dtype=np.float64),
        np.asarray(ys_list, dtype=np.float64),
    )


def sample_random_land_controls(
    valid_land_mask: np.ndarray,
    transform: Affine,
    n_controls: int = 2000,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample deterministic random UTM control coordinates from valid_land_mask."""
    land_rows, land_cols = np.where(valid_land_mask)
    if land_rows.size == 0:
        raise ValueError("Cannot sample random land controls from an empty land mask.")
    rng = np.random.default_rng(seed)
    n_ctrl_eff = min(int(n_controls), int(land_rows.size))
    ctrl_picks = rng.choice(land_rows.size, size=n_ctrl_eff, replace=False)
    xs_ctrl = (
        transform.c + (land_cols[ctrl_picks].astype(np.float64) + 0.5) * transform.a
    )
    ys_ctrl = (
        transform.f + (land_rows[ctrl_picks].astype(np.float64) + 0.5) * transform.e
    )
    return xs_ctrl, ys_ctrl


def compute_point_capture_metrics(
    points_gdf_utm: gpd.GeoDataFrame,
    ar_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    benchmark_mask: np.ndarray | None = None,
    nodata_value: float = -9999.0,
    radii_m: Sequence[float] = (0.0, 25.0, 50.0),
    thresholds_m: Sequence[float] = (0.05, 0.1524),
    n_controls: int = 2000,
    seed: int = 42,
    subtype_col: str = "service_subtype",
    group_col: str = "neighborhood",
    address_col: str = "address",
    control_coords: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Compute region-agnostic point-vs-raster capture, lift, and ROC-AUC metrics."""
    filtered, xs_pts, ys_pts = extract_valid_land_points(
        points_gdf_utm, valid_land_mask, transform
    )
    n_valid_pts = xs_pts.size
    if n_valid_pts == 0:
        raise ValueError(
            "Cannot compute point validation without valid land cells and points."
        )

    if control_coords is not None:
        xs_ctrl, ys_ctrl = (
            np.asarray(control_coords[0], dtype=np.float64),
            np.asarray(control_coords[1], dtype=np.float64),
        )
    else:
        xs_ctrl, ys_ctrl = sample_random_land_controls(
            valid_land_mask=valid_land_mask,
            transform=transform,
            n_controls=n_controls,
            seed=seed,
        )
    n_ctrl_eff = xs_ctrl.size

    sorted_radii = tuple(sorted(float(r) for r in radii_m))
    ar_pts = sample_max_depth_within_buffers(
        ar_depth_m, transform, nodata_value, xs_pts, ys_pts, sorted_radii
    )
    ar_ctrl = sample_max_depth_within_buffers(
        ar_depth_m, transform, nodata_value, xs_ctrl, ys_ctrl, sorted_radii
    )
    bench_pts = (
        sample_mask_within_buffers(
            benchmark_mask, transform, xs_pts, ys_pts, sorted_radii
        )
        if benchmark_mask is not None
        else None
    )
    bench_ctrl = (
        sample_mask_within_buffers(
            benchmark_mask, transform, xs_ctrl, ys_ctrl, sorted_radii
        )
        if benchmark_mask is not None
        else None
    )

    by_buffer: list[dict[str, Any]] = []
    for r_m in sorted_radii:
        entry: dict[str, Any] = {
            "radius_m": r_m,
            "ar_roc_auc": compute_roc_auc(ar_pts[r_m], ar_ctrl[r_m]),
            "thresholds": {},
        }
        for thr in thresholds_m:
            thr_key = f"{thr:g}m"
            hits_pts = ar_pts[r_m] >= float(thr)
            hits_ctrl = ar_ctrl[r_m] >= float(thr)
            rate_pts = float(np.mean(hits_pts))
            rate_ctrl = float(np.mean(hits_ctrl))
            lift = round(rate_pts / rate_ctrl, 3) if rate_ctrl > 0.0 else None
            entry["thresholds"][thr_key] = {
                "hit_count": int(np.sum(hits_pts)),
                "capture_rate": round(rate_pts, 4),
                "control_rate": round(rate_ctrl, 4),
                "lift": lift,
            }

        if bench_pts is not None and bench_ctrl is not None:
            s_hits_pts = bench_pts[r_m]
            s_hits_ctrl = bench_ctrl[r_m]
            s_rate_pts = float(np.mean(s_hits_pts))
            s_rate_ctrl = float(np.mean(s_hits_ctrl))
            entry["sfpuc"] = {
                "hit_count": int(np.sum(s_hits_pts)),
                "capture_rate": round(s_rate_pts, 4),
                "control_rate": round(s_rate_ctrl, 4),
                "lift": (
                    round(s_rate_pts / s_rate_ctrl, 3) if s_rate_ctrl > 0.0 else None
                ),
            }
            ar05_hits = ar_pts[r_m] >= 0.05
            entry["combined_ar05_or_sfpuc_rate"] = round(
                float(np.mean(ar05_hits | s_hits_pts)), 4
            )
            entry["ar05_only_not_sfpuc_count"] = int(np.sum(ar05_hits & (~s_hits_pts)))
            entry["sfpuc_only_not_ar05_count"] = int(np.sum(s_hits_pts & (~ar05_hits)))
        by_buffer.append(entry)

    eval_r = 50.0 if 50.0 in sorted_radii else max(sorted_radii)
    r25 = 25.0 if 25.0 in sorted_radii else eval_r
    ar_eval = ar_pts[eval_r]
    ar_25 = ar_pts[r25]
    s_eval = (
        bench_pts[eval_r]
        if bench_pts is not None
        else np.zeros(n_valid_pts, dtype=bool)
    )

    subtype_rows: list[dict[str, Any]] = []
    if subtype_col in filtered.columns:
        subtypes = filtered[subtype_col].fillna("unknown").astype(str)
        for st_val, idxs in subtypes.groupby(subtypes).groups.items():
            idx_arr = np.asarray(list(idxs), dtype=int)
            row_dict: dict[str, Any] = {subtype_col: st_val}
            row_dict.update(_summarize_point_group(idx_arr, ar_eval, s_eval, ar_25))
            subtype_rows.append(row_dict)
        subtype_rows.sort(key=lambda r: r["count"], reverse=True)

    neighborhood_rows: list[dict[str, Any]] = []
    if group_col in filtered.columns:
        nbhds = filtered[group_col].fillna("Unknown").astype(str)
        for nb_val, idxs in nbhds.groupby(nbhds).groups.items():
            idx_arr = np.asarray(list(idxs), dtype=int)
            row_dict = {group_col: nb_val}
            row_dict.update(_summarize_point_group(idx_arr, ar_eval, s_eval, ar_25))
            neighborhood_rows.append(row_dict)
        neighborhood_rows.sort(key=lambda r: r["count"], reverse=True)
        neighborhood_rows = neighborhood_rows[:12]

    missed_mask = ar_eval < 0.05
    missed_clusters: list[dict[str, Any]] = []
    if np.any(missed_mask):
        missed_df = filtered.loc[missed_mask].copy()
        nb_series = (
            missed_df[group_col].fillna("Unknown").astype(str)
            if group_col in missed_df.columns
            else np.repeat("Unknown", len(missed_df))
        )
        missed_df["_nb"] = nb_series
        total_by_nb = (
            filtered[group_col].fillna("Unknown").astype(str).value_counts().to_dict()
            if group_col in filtered.columns
            else {}
        )
        missed_sfpuc = s_eval[missed_mask]
        missed_df["_sfpuc_hit"] = missed_sfpuc
        for nb_name, grp in missed_df.groupby("_nb"):
            addrs = (
                [
                    str(a)
                    for a in grp[address_col].dropna().astype(str).unique()[:3]
                    if str(a).strip()
                ]
                if address_col in grp.columns
                else []
            )
            missed_clusters.append(
                {
                    group_col: str(nb_name),
                    "missed_count": len(grp),
                    "total_count": int(total_by_nb.get(str(nb_name), len(grp))),
                    "sfpuc_captured_count": int(grp["_sfpuc_hit"].sum()),
                    "sample_addresses": addrs,
                }
            )
        missed_clusters.sort(key=lambda c: c["missed_count"], reverse=True)
        missed_clusters = missed_clusters[:10]

    return {
        "total_raw_points": len(points_gdf_utm),
        "valid_land_points": int(n_valid_pts),
        "dropped_outside_land": int(len(points_gdf_utm) - n_valid_pts),
        "n_controls": int(n_ctrl_eff),
        "eval_buffer_m": eval_r,
        "by_buffer": by_buffer,
        "subtype_breakdown": subtype_rows,
        "neighborhood_breakdown": neighborhood_rows,
        "top_missed_clusters": missed_clusters,
    }


def compute_311_validation_metrics(
    points_gdf_utm: gpd.GeoDataFrame,
    ar_depth_m: np.ndarray,
    sfpuc_mask: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    nodata_value: float = -9999.0,
    radii_m: Sequence[float] = (0.0, 25.0, 50.0),
    thresholds_m: Sequence[float] = (0.05, 0.1524),
    n_controls: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute 3-way validation metrics comparing SF 311 calls against AR hindcast & SFPUC."""
    return compute_point_capture_metrics(
        points_gdf_utm=points_gdf_utm,
        ar_depth_m=ar_depth_m,
        valid_land_mask=valid_land_mask,
        transform=transform,
        benchmark_mask=sfpuc_mask,
        nodata_value=nodata_value,
        radii_m=radii_m,
        thresholds_m=thresholds_m,
        n_controls=n_controls,
        seed=seed,
    )


def build_design_benchmark_summary(
    source: str,
    depth_threshold_m: float,
    metrics: Mapping[str, float | int],
) -> dict[str, Any]:
    """Format Tier B design-storm benchmark metrics for validation_ensemble_metrics.json."""
    return {
        "source": str(source),
        "depth_threshold_m": float(depth_threshold_m),
        "hit_rate": float(metrics["hit_rate"]),
        "false_alarm_ratio": float(metrics["false_alarm_ratio"]),
        "csi": float(metrics["csi"]),
    }


FEET_TO_METERS = 0.3048
_SUPPORTED_VERTICAL_DATUMS = {"NAVD88", "NGVD29", "MLLW", "LMSL"}


def convert_elevation_to_navd88_m(
    elevation: float | np.ndarray,
    *,
    vertical_datum: str = "NAVD88",
    unit: str = "m",
    offset_to_navd88_m: float | None = None,
) -> float | np.ndarray:
    """Convert a water-surface or HWM elevation to NAVD88 meters.

    Any NGVD29, MLLW, or LMSL record requires an explicit station/local
    ``offset_to_navd88_m`` (in meters) so datum shifts are never guessed.
    """
    unit_norm = unit.strip().lower()
    if unit_norm in {"m", "meter", "meters"}:
        scale = 1.0
    elif unit_norm in {"ft", "foot", "feet"}:
        scale = FEET_TO_METERS
    else:
        raise ValueError(f"Unsupported elevation unit: {unit!r}")

    elev_m = (
        np.asarray(elevation, dtype=np.float64) * scale
        if isinstance(elevation, np.ndarray)
        else float(elevation) * scale
    )

    datum_norm = vertical_datum.strip().upper()
    if datum_norm not in _SUPPORTED_VERTICAL_DATUMS:
        raise ValueError(f"Unsupported vertical datum: {vertical_datum!r}")
    if datum_norm == "NAVD88":
        return elev_m
    if offset_to_navd88_m is None:
        raise ValueError(
            f"Datum {datum_norm} requires explicit offset_to_navd88_m to convert to NAVD88."
        )
    return elev_m + float(offset_to_navd88_m)


def compute_gauge_timeseries_metrics(
    time_min: Sequence[float] | np.ndarray,
    obs_wse_navd88_m: Sequence[float] | np.ndarray,
    mod_wse_navd88_m: Sequence[float] | np.ndarray,
    *,
    skip_initial_steps: int = 0,
) -> dict[str, Any]:
    """Compute Tier A gauge hydrograph error metrics in NAVD88 meters and minutes."""
    t_arr = np.asarray(time_min, dtype=np.float64)[skip_initial_steps:]
    obs_arr = np.asarray(obs_wse_navd88_m, dtype=np.float64)[skip_initial_steps:]
    mod_arr = np.asarray(mod_wse_navd88_m, dtype=np.float64)[skip_initial_steps:]

    if not (len(t_arr) == len(obs_arr) == len(mod_arr)):
        raise ValueError(
            f"Time series length mismatch: time={len(t_arr)}, obs={len(obs_arr)}, mod={len(mod_arr)}"
        )

    valid = np.isfinite(t_arr) & np.isfinite(obs_arr) & np.isfinite(mod_arr)
    n_samples = int(np.sum(valid))
    if n_samples == 0:
        return {
            "n_samples": 0,
            "rmse_m": None,
            "mae_m": None,
            "bias_m": None,
            "obs_peak_m": None,
            "mod_peak_m": None,
            "peak_error_m": None,
            "obs_peak_time_min": None,
            "mod_peak_time_min": None,
            "peak_timing_error_min": None,
            "nse": None,
        }

    t_v = t_arr[valid]
    obs_v = obs_arr[valid]
    mod_v = mod_arr[valid]
    err = mod_v - obs_v

    rmse_m = float(np.sqrt(np.mean(err**2)))
    mae_m = float(np.mean(np.abs(err)))
    bias_m = float(np.mean(err))

    obs_idx = int(np.argmax(obs_v))
    mod_idx = int(np.argmax(mod_v))
    obs_peak_m = float(obs_v[obs_idx])
    mod_peak_m = float(mod_v[mod_idx])
    obs_peak_time_min = float(t_v[obs_idx])
    mod_peak_time_min = float(t_v[mod_idx])

    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((obs_v - np.mean(obs_v)) ** 2))
    nse = round(1.0 - ss_res / ss_tot, 4) if ss_tot > 0.0 else None

    return {
        "n_samples": n_samples,
        "rmse_m": round(rmse_m, 4),
        "mae_m": round(mae_m, 4),
        "bias_m": round(bias_m, 4),
        "obs_peak_m": round(obs_peak_m, 4),
        "mod_peak_m": round(mod_peak_m, 4),
        "peak_error_m": round(mod_peak_m - obs_peak_m, 4),
        "obs_peak_time_min": round(obs_peak_time_min, 1),
        "mod_peak_time_min": round(mod_peak_time_min, 1),
        "peak_timing_error_min": round(mod_peak_time_min - obs_peak_time_min, 1),
        "nse": nse,
    }


def sample_raster_at_points(
    raster_m: np.ndarray,
    points_xy_utm: Sequence[tuple[float, float]],
    transform: Affine,
    *,
    nodata_value: float = -9999.0,
) -> np.ndarray:
    """Sample a 2D EPSG:26910 raster at (x_m, y_m) point coordinates."""
    if not points_xy_utm:
        return np.empty(0, dtype=np.float64)
    height, width = raster_m.shape
    out = np.full(len(points_xy_utm), np.nan, dtype=np.float64)
    for i, (x_m, y_m) in enumerate(points_xy_utm):
        r, c = rowcol(transform, float(x_m), float(y_m))
        if 0 <= r < height and 0 <= c < width:
            val = float(raster_m[r, c])
            if np.isfinite(val) and val != nodata_value:
                out[i] = val
    return out


def compute_hwm_metrics(
    obs_wse_navd88_m: Sequence[float] | np.ndarray,
    mod_wse_navd88_m: Sequence[float] | np.ndarray,
) -> dict[str, Any]:
    """Compute Tier A High-Water Mark (HWM) peak water-surface metrics in NAVD88 meters."""
    obs_arr = np.asarray(obs_wse_navd88_m, dtype=np.float64)
    mod_arr = np.asarray(mod_wse_navd88_m, dtype=np.float64)
    if len(obs_arr) != len(mod_arr):
        raise ValueError(
            f"HWM array length mismatch: obs={len(obs_arr)}, mod={len(mod_arr)}"
        )
    valid = np.isfinite(obs_arr) & np.isfinite(mod_arr)
    n_hwm = int(np.sum(valid))
    if n_hwm == 0:
        return {
            "n_hwm": 0,
            "rmse_m": None,
            "mae_m": None,
            "bias_m": None,
            "max_abs_error_m": None,
            "obs_mean_m": None,
            "mod_mean_m": None,
        }
    obs_v = obs_arr[valid]
    mod_v = mod_arr[valid]
    err = mod_v - obs_v
    return {
        "n_hwm": n_hwm,
        "rmse_m": round(float(np.sqrt(np.mean(err**2))), 4),
        "mae_m": round(float(np.mean(np.abs(err))), 4),
        "bias_m": round(float(np.mean(err)), 4),
        "max_abs_error_m": round(float(np.max(np.abs(err))), 4),
        "obs_mean_m": round(float(np.mean(obs_v)), 4),
        "mod_mean_m": round(float(np.mean(mod_v)), 4),
    }


def parse_usgs_nwis_iv_json(
    payload: Mapping[str, Any],
    *,
    parameter_cd: str = "00065",
    vertical_datum: str = "NAVD88",
    unit: str = "ft",
    offset_to_navd88_m: float | None = None,
) -> dict[str, Any]:
    """Parse USGS NWIS Instantaneous Values JSON into NAVD88-meter time series."""
    series_list = payload.get("value", {}).get("timeSeries", [])
    for ts in series_list:
        var_codes = [
            c.get("value") for c in ts.get("variable", {}).get("variableCode", [])
        ]
        if parameter_cd and parameter_cd not in var_codes:
            continue
        src_info = ts.get("sourceInfo", {})
        site_codes = src_info.get("siteCode", [{}])
        station_id = str(site_codes[0].get("value", "")) if site_codes else ""
        station_name = str(src_info.get("siteName", station_id))
        geo = src_info.get("geoLocation", {}).get("geogLocation", {})
        lat = float(geo["latitude"]) if "latitude" in geo else None
        lon = float(geo["longitude"]) if "longitude" in geo else None

        values_block = ts.get("values", [{}])
        raw_points = values_block[0].get("value", []) if values_block else []
        times_utc: list[str] = []
        wse_navd88_m: list[float] = []
        for pt in raw_points:
            raw_val = pt.get("value")
            if raw_val is None or str(raw_val) in {"", "-999999"}:
                continue
            val_m = convert_elevation_to_navd88_m(
                float(raw_val),
                vertical_datum=vertical_datum,
                unit=unit,
                offset_to_navd88_m=offset_to_navd88_m,
            )
            times_utc.append(str(pt["dateTime"]))
            wse_navd88_m.append(round(float(val_m), 4))

        return {
            "station_id": station_id,
            "station_name": station_name,
            "lat": lat,
            "lon": lon,
            "vertical_datum": "NAVD88",
            "times_utc": times_utc,
            "wse_navd88_m": wse_navd88_m,
        }

    raise ValueError(f"No USGS NWIS timeSeries found for parameter_cd={parameter_cd!r}")


def parse_usgs_stn_hwm_records(
    records: Sequence[Mapping[str, Any]],
    *,
    target_crs: str = "EPSG:26910",
) -> gpd.GeoDataFrame:
    """Parse USGS STN High-Water Mark records into an EPSG:26910 NAVD88 GeoDataFrame."""
    rows: list[dict[str, Any]] = []
    for rec in records:
        if "wse_m" in rec and rec["wse_m"] is not None:
            raw_elev = float(rec["wse_m"])
            unit = str(rec.get("unit", "m"))
        elif "elev_ft" in rec and rec["elev_ft"] is not None:
            raw_elev = float(rec["elev_ft"])
            unit = "ft"
        else:
            continue
        datum = str(rec.get("vertical_datum", rec.get("verticalDatumName", "NAVD88")))
        offset_m = rec.get("offset_to_navd88_m")
        wse_navd88_m = float(
            convert_elevation_to_navd88_m(
                raw_elev,
                vertical_datum=datum,
                unit=unit,
                offset_to_navd88_m=float(offset_m) if offset_m is not None else None,
            )
        )
        rows.append(
            {
                "hwm_id": str(rec.get("hwm_id", len(rows) + 1)),
                "source_datum": datum.upper(),
                "wse_navd88_m": round(wse_navd88_m, 4),
                "lon": float(rec["longitude"]),
                "lat": float(rec["latitude"]),
            }
        )

    if not rows:
        return gpd.GeoDataFrame(
            columns=["hwm_id", "source_datum", "wse_navd88_m", "geometry"],
            geometry="geometry",
            crs=target_crs,
        )

    df = pd.DataFrame(rows)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs="EPSG:4326",
    ).to_crs(target_crs)
    gdf["x_epsg26910"] = gdf.geometry.x
    gdf["y_epsg26910"] = gdf.geometry.y
    return gdf[
        [
            "hwm_id",
            "source_datum",
            "wse_navd88_m",
            "x_epsg26910",
            "y_epsg26910",
            "geometry",
        ]
    ]


class BenchmarkSpec(NamedTuple):
    """Configuration for a pluggable Tier B polygon hydraulic benchmark."""

    source: str
    vector_path: Path
    depth_threshold_m: float = 0.1524
    layer: str | None = None
    attribute_filter: Mapping[str, Sequence[Any]] | None = None
    corridors_utm: Mapping[str, tuple[float, float, float, float]] | None = None


def filter_benchmark_geodataframe(
    gdf: gpd.GeoDataFrame,
    attribute_filter: Mapping[str, Sequence[Any]] | None = None,
) -> gpd.GeoDataFrame:
    """Filter a Tier B benchmark GeoDataFrame by column allow-lists."""
    if not attribute_filter:
        return gdf
    mask = pd.Series(True, index=gdf.index)
    for col, allowed in attribute_filter.items():
        if col not in gdf.columns:
            raise ValueError(f"Benchmark attribute_filter column {col!r} not in layer")
        mask = mask & gdf[col].isin(list(allowed))
    return gdf.loc[mask].copy()


def evaluate_polygon_benchmark(
    depth_m: np.ndarray,
    benchmark_gdf_utm: gpd.GeoDataFrame,
    valid_land_mask: np.ndarray,
    transform: Affine,
    *,
    source: str = "sfpuc_100yr",
    depth_threshold_m: float = 0.1524,
    corridors_utm: Mapping[str, tuple[float, float, float, float]] | None = None,
    attribute_filter: Mapping[str, Sequence[Any]] | None = None,
    target_crs: str = "EPSG:26910",
) -> dict[str, Any]:
    """Evaluate modeled depth against a pluggable Tier B polygon hydraulic benchmark."""
    assert_crs_equals(benchmark_gdf_utm.crs, target_crs, f"benchmark[{source}]")
    filtered_gdf = filter_benchmark_geodataframe(benchmark_gdf_utm, attribute_filter)
    obs_mask = rasterize_benchmark_polygons(
        gdf_utm=filtered_gdf,
        out_shape=depth_m.shape,
        transform=transform,
    )
    metrics = compute_flood_metrics(
        depth_m=depth_m,
        obs_mask=obs_mask,
        valid_mask=valid_land_mask,
        depth_threshold_m=depth_threshold_m,
    )
    corridors = (
        compute_corridor_benchmark_metrics(
            depth_m=depth_m,
            obs_mask=obs_mask,
            valid_land_mask=valid_land_mask,
            transform=transform,
            depth_threshold_m=depth_threshold_m,
            corridors_utm=corridors_utm,
            label_key="corridor",
        )
        if corridors_utm
        else []
    )
    return {
        "summary": build_design_benchmark_summary(
            source=source,
            depth_threshold_m=depth_threshold_m,
            metrics=metrics,
        ),
        "metrics": metrics,
        "corridors": corridors,
    }


def load_benchmark_specs(cfg: Mapping[str, Any]) -> list[BenchmarkSpec]:
    """Load Tier B BenchmarkSpec list from config (supports pluggable benchmarks or legacy validation_reference)."""
    project = cfg.get("project", {})
    domain = cfg.get("domain", {})
    val_cfg = cfg.get("validation", {})
    default_thr_m = float(val_cfg.get("depth_threshold_m", 0.1524))
    default_corridors = (
        SF_NEIGHBORHOOD_BOXES_UTM
        if str(project.get("region_id", "")) == "sf_citywide"
        else None
    )

    specs: list[BenchmarkSpec] = []
    raw_benchmarks = val_cfg.get("benchmarks")
    if raw_benchmarks:
        for b in raw_benchmarks:
            specs.append(
                BenchmarkSpec(
                    source=str(b["source"]),
                    vector_path=Path(b["vector_file"]),
                    depth_threshold_m=float(b.get("depth_threshold_m", default_thr_m)),
                    layer=b.get("layer"),
                    attribute_filter=b.get("attribute_filter"),
                    corridors_utm=b.get("corridors_utm", default_corridors),
                )
            )
        return specs

    val_ref_raw = domain.get("validation_reference")
    if val_ref_raw:
        specs.append(
            BenchmarkSpec(
                source=str(val_cfg.get("benchmark_source_name", "sfpuc_100yr")),
                vector_path=Path(val_ref_raw),
                depth_threshold_m=default_thr_m,
                corridors_utm=default_corridors,
            )
        )
    return specs


def filter_right_of_way_lines(
    roads_gdf_utm: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    """Filter OSM roads/rail GeoDataFrame to public right-of-way (streets and sidewalks)."""
    if roads_gdf_utm is None or roads_gdf_utm.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:26910")
    if "highway" in roads_gdf_utm.columns:
        hw = roads_gdf_utm["highway"].astype(str).str.strip()
        mask = roads_gdf_utm["highway"].notna() & (hw != "") & (hw != "None")
        filtered = roads_gdf_utm.loc[mask].copy()
        if not filtered.empty:
            return filtered
    return roads_gdf_utm.copy()


def _extract_road_linestrings(
    roads_gdf_utm: gpd.GeoDataFrame | None,
) -> list[LineString]:
    """Flatten LineString/MultiLineString geometries into non-empty LineStrings."""
    if roads_gdf_utm is None or roads_gdf_utm.empty:
        return []
    lines: list[LineString] = []
    for geom in roads_gdf_utm.geometry:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, LineString) and geom.length > 0.0:
            lines.append(geom)
        elif isinstance(geom, MultiLineString):
            for part in geom.geoms:
                if isinstance(part, LineString) and part.length > 0.0:
                    lines.append(part)
    return lines


def snap_points_to_roads(
    points_gdf_utm: gpd.GeoDataFrame,
    roads_gdf_utm: gpd.GeoDataFrame | None,
    max_snap_distance_m: float = 50.0,
) -> tuple[gpd.GeoDataFrame, int]:
    """Snap closure points (or centroids) to nearest road within max_snap_distance_m."""
    if points_gdf_utm.empty:
        return points_gdf_utm.copy(), 0
    lines = _extract_road_linestrings(roads_gdf_utm)
    if not lines:
        out = points_gdf_utm.copy()
        out["geometry"] = [
            geom.centroid if geom is not None and geom.geom_type != "Point" else geom
            for geom in out.geometry
        ]
        return out, 0

    tree = STRtree(lines)
    snapped_geoms: list[Any] = []
    snapped_count = 0
    for geom in points_gdf_utm.geometry:
        if geom is None or geom.is_empty:
            snapped_geoms.append(geom)
            continue
        pt = geom.centroid if geom.geom_type != "Point" else geom
        nearest_idx = int(tree.nearest(pt))
        road_geom = lines[nearest_idx]
        dist_m = float(pt.distance(road_geom))
        if dist_m <= max_snap_distance_m:
            _, on_road = nearest_points(pt, road_geom)
            snapped_geoms.append(Point(float(on_road.x), float(on_road.y)))
            snapped_count += 1
        else:
            snapped_geoms.append(pt)

    out = points_gdf_utm.copy()
    out["geometry"] = snapped_geoms
    return out, snapped_count


def sample_random_road_controls(
    roads_gdf_utm: gpd.GeoDataFrame,
    valid_land_mask: np.ndarray,
    transform: Affine,
    n_controls: int = 2000,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample deterministic random UTM control coordinates along roads in valid_land_mask."""
    lines = _extract_road_linestrings(roads_gdf_utm)
    if not lines:
        raise ValueError("Cannot sample road controls from an empty road network.")

    lengths = np.asarray([line.length for line in lines], dtype=np.float64)
    cum_lengths = np.cumsum(lengths)
    total_length_m = float(cum_lengths[-1])
    if total_length_m <= 0.0:
        raise ValueError("Road network has zero total length.")

    height, width = valid_land_mask.shape
    rng = np.random.default_rng(seed)
    n_candidates = max(int(n_controls) * 4, 4000)
    samples_m = rng.uniform(0.0, total_length_m, size=n_candidates)

    xs_list: list[float] = []
    ys_list: list[float] = []
    for d_m in samples_m:
        line_idx = int(np.searchsorted(cum_lengths, d_m, side="right"))
        line_idx = min(line_idx, len(lines) - 1)
        prev_cum = float(cum_lengths[line_idx - 1]) if line_idx > 0 else 0.0
        pt = lines[line_idx].interpolate(float(d_m) - prev_cum)
        r, c = rowcol(transform, float(pt.x), float(pt.y))
        if 0 <= r < height and 0 <= c < width and bool(valid_land_mask[r, c]):
            xs_list.append(float(pt.x))
            ys_list.append(float(pt.y))
            if len(xs_list) >= int(n_controls):
                break

    if not xs_list:
        raise ValueError("No road control points fell inside valid_land_mask.")

    return np.asarray(xs_list, dtype=np.float64), np.asarray(ys_list, dtype=np.float64)


def compute_road_closure_metrics(
    closures_gdf_utm: gpd.GeoDataFrame,
    ar_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    roads_gdf_utm: gpd.GeoDataFrame | None = None,
    max_snap_distance_m: float = 50.0,
    nodata_value: float = -9999.0,
    radii_m: Sequence[float] = (25.0, 50.0),
    thresholds_m: Sequence[float] = (0.05, 0.1524),
    n_controls: int = 2000,
    seed: int = 42,
    corridor_col: str = "corridor",
) -> dict[str, Any]:
    """Score Tier C roadway flood closures (road-snapped capture, road-control lift, ROC-AUC)."""
    snapped_gdf, n_snapped = snap_points_to_roads(
        closures_gdf_utm,
        roads_gdf_utm,
        max_snap_distance_m=max_snap_distance_m,
    )
    filtered, xs_pts, ys_pts = extract_valid_land_points(
        snapped_gdf, valid_land_mask, transform
    )
    n_valid_pts = int(xs_pts.size)
    if n_valid_pts == 0:
        raise ValueError(
            "Cannot compute Tier C road-closure validation without valid land points."
        )

    control_type = "road_network"
    try:
        if roads_gdf_utm is not None and not roads_gdf_utm.empty:
            xs_ctrl, ys_ctrl = sample_random_road_controls(
                roads_gdf_utm=roads_gdf_utm,
                valid_land_mask=valid_land_mask,
                transform=transform,
                n_controls=n_controls,
                seed=seed,
            )
        else:
            raise ValueError("No road network provided.")
    except ValueError:
        control_type = "valid_land"
        xs_ctrl, ys_ctrl = sample_random_land_controls(
            valid_land_mask=valid_land_mask,
            transform=transform,
            n_controls=n_controls,
            seed=seed,
        )

    sorted_radii = tuple(sorted(float(r) for r in radii_m))
    ar_pts = sample_max_depth_within_buffers(
        ar_depth_m, transform, nodata_value, xs_pts, ys_pts, sorted_radii
    )
    ar_ctrl = sample_max_depth_within_buffers(
        ar_depth_m, transform, nodata_value, xs_ctrl, ys_ctrl, sorted_radii
    )

    by_buffer: list[dict[str, Any]] = []
    for r_m in sorted_radii:
        entry: dict[str, Any] = {
            "radius_m": r_m,
            "ar_roc_auc": compute_roc_auc(ar_pts[r_m], ar_ctrl[r_m]),
            "thresholds": {},
        }
        for thr in thresholds_m:
            thr_key = f"{thr:g}m"
            hits_pts = ar_pts[r_m] >= float(thr)
            hits_ctrl = ar_ctrl[r_m] >= float(thr)
            rate_pts = float(np.mean(hits_pts))
            rate_ctrl = float(np.mean(hits_ctrl))
            lift = round(rate_pts / rate_ctrl, 3) if rate_ctrl > 0.0 else None
            entry["thresholds"][thr_key] = {
                "hit_count": int(np.sum(hits_pts)),
                "capture_rate": round(rate_pts, 4),
                "control_rate": round(rate_ctrl, 4),
                "lift": lift,
            }
        by_buffer.append(entry)

    by_corridor: dict[str, Any] = {}
    if corridor_col in filtered.columns:
        primary_r = 25.0 if 25.0 in sorted_radii else sorted_radii[0]
        primary_thr = 0.05 if 0.05 in thresholds_m else float(thresholds_m[0])
        corridor_vals = filtered[corridor_col].fillna("unknown").astype(str)
        for corr_name in sorted(corridor_vals.unique()):
            mask = (corridor_vals == corr_name).to_numpy()
            if not np.any(mask):
                continue
            hits = ar_pts[primary_r][mask] >= primary_thr
            by_corridor[corr_name] = {
                "count": int(np.sum(mask)),
                "hit_count": int(np.sum(hits)),
                "capture_rate": round(float(np.mean(hits)), 4),
                "radius_m": primary_r,
                "threshold_m": primary_thr,
            }

    by_source_counts: dict[str, int] = {}
    if "source" in filtered.columns:
        src_vals = filtered["source"].fillna("unknown").astype(str)
        by_source_counts = {k: int(v) for k, v in src_vals.value_counts().items()}

    by_category_counts: dict[str, int] = {}
    if "category" in filtered.columns:
        cat_vals = filtered["category"].fillna("unknown").astype(str)
        by_category_counts = {k: int(v) for k, v in cat_vals.value_counts().items()}

    return {
        "valid_land_points": n_valid_pts,
        "snapped_points": int(n_snapped),
        "n_controls": int(xs_ctrl.size),
        "control_type": control_type,
        "by_buffer": by_buffer,
        "by_corridor": by_corridor,
        "by_source_counts": by_source_counts,
        "by_category_counts": by_category_counts,
    }


def filter_groundsource_records(
    gdf: gpd.GeoDataFrame,
    max_area_km2: float = 25.0,
    domain_bbox_utm: Sequence[float] | None = None,
    target_crs: str = "EPSG:26910",
) -> gpd.GeoDataFrame:
    """Filter Google Research Groundsource polygons to area_km2 <= max_area_km2 in UTM."""
    if gdf.empty:
        out = gdf.copy()
        if out.crs is None:
            out = out.set_crs(target_crs)
        elif CRS.from_user_input(out.crs) != CRS.from_user_input(target_crs):
            out = out.to_crs(target_crs)
        return out

    if gdf.crs is None:
        raise ValueError("Groundsource GeoDataFrame is missing CRS.")
    out = (
        gdf.to_crs(target_crs)
        if CRS.from_user_input(gdf.crs) != CRS.from_user_input(target_crs)
        else gdf.copy()
    )
    out = out[out.geometry.notna() & ~out.geometry.is_empty].copy()
    if out.empty:
        return out.reset_index(drop=True)

    if "area_km2" in out.columns:
        area_km2 = pd.to_numeric(out["area_km2"], errors="coerce")
        missing = area_km2.isna()
        if missing.any():
            area_km2 = area_km2.where(~missing, out.geometry.area / 1_000_000.0)
    else:
        area_km2 = out.geometry.area / 1_000_000.0
    out["area_km2"] = area_km2.astype(float)
    out = out[(out["area_km2"] > 0.0) & (out["area_km2"] <= float(max_area_km2))].copy()

    if domain_bbox_utm is not None and not out.empty:
        xmin, ymin, xmax, ymax = (float(v) for v in domain_bbox_utm)
        domain_box = box(xmin, ymin, xmax, ymax)
        out = out[out.geometry.intersects(domain_box)].copy()

    return out.reset_index(drop=True)


def build_subbasin_grid(
    domain_bbox_utm: Sequence[float],
    cell_size_m: float = 2000.0,
    crs: str = "EPSG:26910",
) -> gpd.GeoDataFrame:
    """Build a regular UTM sub-basin grid covering domain_bbox_utm."""
    xmin, ymin, xmax, ymax = (float(v) for v in domain_bbox_utm)
    step = float(cell_size_m)
    if step <= 0.0:
        raise ValueError("cell_size_m must be positive.")

    rows: list[dict[str, Any]] = []
    geoms: list[Any] = []
    x = xmin
    ix = 0
    while x < xmax:
        x_hi = min(x + step, xmax)
        y = ymin
        iy = 0
        while y < ymax:
            y_hi = min(y + step, ymax)
            rows.append({"subbasin_id": f"grid_{ix}_{iy}"})
            geoms.append(box(x, y, x_hi, y_hi))
            y += step
            iy += 1
        x += step
        ix += 1
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=crs)


def _parse_event_window_dates(
    ev: Mapping[str, Any], tolerance_days: int
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    start_raw = ev.get("validation_311_start_utc") or ev.get("start_date")
    end_raw = ev.get("validation_311_end_utc") or ev.get("end_date")
    if not start_raw:
        return None
    start_ts = pd.Timestamp(start_raw).tz_localize(None).normalize()
    end_ts = (
        pd.Timestamp(end_raw).tz_localize(None).normalize() if end_raw else start_ts
    )
    pad = pd.Timedelta(days=int(tolerance_days))
    return start_ts - pad, end_ts + pad


def discover_holdout_storm_candidates(
    groundsource_gdf_utm: gpd.GeoDataFrame,
    ensemble_events: Sequence[Mapping[str, Any]],
    tolerance_days: int = 2,
    min_records: int = 1,
) -> list[dict[str, Any]]:
    """Identify Groundsource storm dates outside calibration windows for v0.6 holdouts."""
    if groundsource_gdf_utm.empty or "start_date" not in groundsource_gdf_utm.columns:
        return []

    starts = pd.to_datetime(groundsource_gdf_utm["start_date"], errors="coerce")
    if "end_date" in groundsource_gdf_utm.columns:
        ends = pd.to_datetime(groundsource_gdf_utm["end_date"], errors="coerce").fillna(
            starts
        )
    else:
        ends = starts

    if starts.dt.tz is not None:
        starts = starts.dt.tz_localize(None)
    if ends.dt.tz is not None:
        ends = ends.dt.tz_localize(None)

    cal_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for ev in ensemble_events:
        if ev.get("holdout"):
            continue
        win = _parse_event_window_dates(ev, tolerance_days=tolerance_days)
        if win is not None:
            cal_windows.append(win)

    grouped: dict[str, dict[str, Any]] = {}
    for idx, (st, en) in enumerate(zip(starts, ends, strict=False)):
        if pd.isna(st):
            continue
        st_d = st.normalize()
        en_d = en.normalize() if not pd.isna(en) else st_d
        overlaps_cal = any(
            not (en_d < w_start or st_d > w_end) for w_start, w_end in cal_windows
        )
        if overlaps_cal:
            continue
        key = st_d.strftime("%Y-%m-%d")
        area_val = (
            float(groundsource_gdf_utm.iloc[idx]["area_km2"])
            if "area_km2" in groundsource_gdf_utm.columns
            else float(groundsource_gdf_utm.geometry.iloc[idx].area / 1_000_000.0)
        )
        bucket = grouped.setdefault(
            key,
            {
                "start_date": key,
                "end_date": en_d.strftime("%Y-%m-%d"),
                "n_records": 0,
                "total_area_km2": 0.0,
            },
        )
        bucket["n_records"] += 1
        bucket["total_area_km2"] = round(bucket["total_area_km2"] + area_val, 3)
        bucket["end_date"] = max(bucket["end_date"], en_d.strftime("%Y-%m-%d"))

    candidates = [v for v in grouped.values() if v["n_records"] >= int(min_records)]
    candidates.sort(
        key=lambda item: (
            -item["n_records"],
            -item["total_area_km2"],
            item["start_date"],
        )
    )
    return candidates


def evaluate_groundsource_subbasin_match(
    groundsource_gdf_utm: gpd.GeoDataFrame,
    modeled_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    subbasins_gdf_utm: gpd.GeoDataFrame | None = None,
    domain_bbox_utm: Sequence[float] | None = None,
    depth_threshold_m: float = 0.05,
    nodata_value: float = -9999.0,
    event_windows: Sequence[Mapping[str, Any]] | None = None,
    tolerance_days: int = 2,
) -> dict[str, Any]:
    """Evaluate Tier D Groundsource sub-basin flood presence agreement and holdout dates."""
    if subbasins_gdf_utm is None:
        if domain_bbox_utm is None:
            height, width = valid_land_mask.shape
            xmin = float(transform.c)
            ymax = float(transform.f)
            xmax = xmin + width * float(transform.a)
            ymin = ymax + height * float(transform.e)
            domain_bbox_utm = (xmin, ymin, xmax, ymax)
        subbasins = build_subbasin_grid(domain_bbox_utm=domain_bbox_utm)
    else:
        subbasins = subbasins_gdf_utm.reset_index(drop=True)

    id_col = "subbasin_id" if "subbasin_id" in subbasins.columns else None
    shapes = [
        (geom, idx + 1)
        for idx, geom in enumerate(subbasins.geometry)
        if geom is not None and not geom.is_empty
    ]
    subbasin_raster = (
        rasterize(
            shapes=shapes,
            out_shape=valid_land_mask.shape,
            transform=transform,
            fill=0,
            dtype="int32",
        )
        if shapes
        else np.zeros(valid_land_mask.shape, dtype="int32")
    )

    valid_depth = (
        valid_land_mask
        & np.isfinite(modeled_depth_m)
        & (modeled_depth_m != nodata_value)
        & (modeled_depth_m >= float(depth_threshold_m))
    )

    n_gs_present = 0
    n_model_present = 0
    n_both = 0
    n_gs_only = 0
    n_model_only = 0
    n_neither = 0
    evaluated_subbasins = 0

    for idx, row in subbasins.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        sb_mask = (subbasin_raster == (idx + 1)) & valid_land_mask
        if not np.any(sb_mask):
            continue
        evaluated_subbasins += 1
        model_has_flood = bool(np.any(valid_depth & sb_mask))
        gs_has_flood = (
            bool(groundsource_gdf_utm.geometry.intersects(geom).any())
            if not groundsource_gdf_utm.empty
            else False
        )
        if gs_has_flood:
            n_gs_present += 1
        if model_has_flood:
            n_model_present += 1
        if gs_has_flood and model_has_flood:
            n_both += 1
        elif gs_has_flood:
            n_gs_only += 1
        elif model_has_flood:
            n_model_only += 1
        else:
            n_neither += 1

    hit_rate = round(n_both / n_gs_present, 4) if n_gs_present > 0 else None
    n_gs_absent = n_model_only + n_neither
    specificity = round(n_neither / n_gs_absent, 4) if n_gs_absent > 0 else None
    accuracy = (
        round((n_both + n_neither) / evaluated_subbasins, 4)
        if evaluated_subbasins > 0
        else None
    )

    matched_events: list[str] = []
    if (
        event_windows
        and not groundsource_gdf_utm.empty
        and "start_date" in groundsource_gdf_utm.columns
    ):
        starts = pd.to_datetime(groundsource_gdf_utm["start_date"], errors="coerce")
        if starts.dt.tz is not None:
            starts = starts.dt.tz_localize(None)
        ends = (
            pd.to_datetime(groundsource_gdf_utm["end_date"], errors="coerce").fillna(
                starts
            )
            if "end_date" in groundsource_gdf_utm.columns
            else starts
        )
        if ends.dt.tz is not None:
            ends = ends.dt.tz_localize(None)
        for ev in event_windows:
            win = _parse_event_window_dates(ev, tolerance_days=tolerance_days)
            if win is None:
                continue
            w_start, w_end = win
            overlaps = (
                (ends.dt.normalize() >= w_start) & (starts.dt.normalize() <= w_end)
            ).any()
            if bool(overlaps):
                matched_events.append(str(ev.get("id", "")))

    holdout_candidates = (
        discover_holdout_storm_candidates(
            groundsource_gdf_utm,
            ensemble_events=event_windows,
            tolerance_days=tolerance_days,
        )
        if event_windows is not None
        else []
    )

    return {
        "n_groundsource_records": len(groundsource_gdf_utm),
        "n_subbasins": int(evaluated_subbasins),
        "subbasin_id_col": id_col or "grid",
        "depth_threshold_m": float(depth_threshold_m),
        "n_gs_present": int(n_gs_present),
        "n_model_present": int(n_model_present),
        "n_both_present": int(n_both),
        "n_gs_only": int(n_gs_only),
        "n_model_only": int(n_model_only),
        "n_neither": int(n_neither),
        "presence_hit_rate": hit_rate,
        "specificity": specificity,
        "confusion_matrix": {
            "tp": int(n_both),
            "fp": int(n_model_only),
            "fn": int(n_gs_only),
            "tn": int(n_neither),
        },
        "presence_accuracy": accuracy,
        "matched_ensemble_events": matched_events,
        "holdout_storm_candidates": holdout_candidates,
    }


def aggregate_ensemble_311_metrics(
    event_results: Sequence[Mapping[str, Any]],
    *,
    region: str | None = None,
    drainage_tier: str | None = None,
    rain_source: str | None = None,
    design_benchmark: Mapping[str, Any] | None = None,
    regional_crosschecks: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate per-storm 311 and multi-tier validation metrics across an AR ensemble."""
    if not event_results:
        raise ValueError("event_results must not be empty.")

    radii = sorted(
        {float(b["radius_m"]) for ev in event_results for b in ev["by_buffer"]}
    )
    thresholds = sorted(
        {
            thr
            for ev in event_results
            for b in ev["by_buffer"]
            for thr in b["thresholds"]
        }
    )

    ensemble_mean_by_buffer: list[dict[str, Any]] = []
    for r_m in radii:
        matching_bufs = [
            b
            for ev in event_results
            for b in ev["by_buffer"]
            if math.isclose(float(b["radius_m"]), r_m)
        ]
        aucs = [
            float(b["ar_roc_auc"])
            for b in matching_bufs
            if b.get("ar_roc_auc") is not None
        ]
        thr_means: dict[str, Any] = {}
        for thr in thresholds:
            t_rows = [
                b["thresholds"][thr]
                for b in matching_bufs
                if thr in b.get("thresholds", {})
            ]
            lifts = [float(t["lift"]) for t in t_rows if t.get("lift") is not None]
            thr_means[thr] = {
                "capture_rate": round(
                    float(np.mean([float(t["capture_rate"]) for t in t_rows])), 4
                ),
                "control_rate": round(
                    float(np.mean([float(t["control_rate"]) for t in t_rows])), 4
                ),
                "lift": round(float(np.mean(lifts)), 3) if lifts else None,
            }
        ensemble_mean_by_buffer.append(
            {
                "radius_m": r_m,
                "ar_roc_auc": round(float(np.mean(aucs)), 6) if aucs else None,
                "thresholds": thr_means,
            }
        )

    crosschecks_payload = (
        dict(regional_crosschecks)
        if regional_crosschecks is not None
        else {
            "groundsource_subbasin_match": None,
            "nfip_tract_spearman_rho": None,
        }
    )

    return {
        "region": region,
        "drainage_tier": drainage_tier,
        "rain_source": rain_source,
        "n_events": len(event_results),
        "total_311_points": sum(int(ev["valid_land_points"]) for ev in event_results),
        "design_benchmark": dict(design_benchmark)
        if design_benchmark is not None
        else None,
        "events": [
            {
                "id": ev["id"],
                "label": ev.get("label", ev["id"]),
                "scenario": ev["scenario"],
                "valid_land_points": int(ev["valid_land_points"]),
                "by_buffer": list(ev["by_buffer"]),
                "tiers_evaluated": list(ev.get("tiers_evaluated", ["311"])),
                "road_closures_validation": ev.get("road_closures_validation"),
                "gauge_hwm_validation": ev.get("gauge_hwm_validation"),
            }
            for ev in event_results
        ],
        "ensemble_mean": {"by_buffer": ensemble_mean_by_buffer},
        "regional_crosschecks": crosschecks_payload,
    }


def compute_pooled_ensemble_point_metrics(
    event_results: Sequence[Mapping[str, Any]],
    radii_m: Sequence[float] = (0.0, 25.0, 50.0),
    thresholds_m: Sequence[float] = (0.05, 0.1524),
    weight_key: str = "valid_land_points",
) -> dict[str, Any]:
    """Compute point-weighted pooled ensemble capture, control rate, lift, and AUC."""
    if not event_results:
        raise ValueError("event_results must not be empty.")

    total_pts = sum(int(ev.get(weight_key, 0)) for ev in event_results)
    if total_pts <= 0:
        raise ValueError(f"Total {weight_key} must be positive.")

    by_buffer: list[dict[str, Any]] = []
    for r_m in sorted(float(r) for r in radii_m):
        weighted_auc_num = 0.0
        weighted_auc_den = 0
        thr_out: dict[str, Any] = {}
        for thr in thresholds_m:
            thr_key = f"{thr:g}m"
            tot_hits = 0
            tot_pts_thr = 0
            weighted_ctrl_num = 0.0
            for ev in event_results:
                n_ev = int(ev.get(weight_key, 0))
                if n_ev <= 0:
                    continue
                buf = next(
                    (
                        b
                        for b in ev.get("by_buffer", [])
                        if math.isclose(float(b["radius_m"]), r_m)
                    ),
                    None,
                )
                if buf is None:
                    continue
                t_info = buf.get("thresholds", {}).get(thr_key)
                if t_info is None:
                    continue
                tot_hits += int(t_info["hit_count"])
                tot_pts_thr += n_ev
                weighted_ctrl_num += float(t_info["control_rate"]) * n_ev

            cap = tot_hits / tot_pts_thr if tot_pts_thr > 0 else 0.0
            ctrl = weighted_ctrl_num / tot_pts_thr if tot_pts_thr > 0 else 0.0
            lift = round(cap / ctrl, 3) if ctrl > 0.0 else None
            thr_out[thr_key] = {
                "hit_count": int(tot_hits),
                "total_points": int(tot_pts_thr),
                "capture_rate": round(cap, 4),
                "control_rate": round(ctrl, 4),
                "lift": lift,
            }

        for ev in event_results:
            n_ev = int(ev.get(weight_key, 0))
            if n_ev <= 0:
                continue
            buf = next(
                (
                    b
                    for b in ev.get("by_buffer", [])
                    if math.isclose(float(b["radius_m"]), r_m)
                ),
                None,
            )
            if buf is not None and buf.get("ar_roc_auc") is not None:
                weighted_auc_num += float(buf["ar_roc_auc"]) * n_ev
                weighted_auc_den += n_ev

        by_buffer.append(
            {
                "radius_m": r_m,
                "ar_roc_auc_weighted": (
                    round(weighted_auc_num / weighted_auc_den, 6)
                    if weighted_auc_den > 0
                    else None
                ),
                "thresholds": thr_out,
            }
        )

    return {"total_points": int(total_pts), "by_buffer": by_buffer}


def compute_income_stratified_point_metrics(
    event_samples: Sequence[Mapping[str, Any]],
    tracts_income_gdf_utm: gpd.GeoDataFrame,
    income_col: str = "median_income_usd",
    n_quantiles: int = 3,
    radii_m: Sequence[float] = (0.0, 25.0, 50.0),
    depth_threshold_m: float = 0.05,
) -> dict[str, Any]:
    """Stratify pooled point-vs-control metrics across census-tract median income quantiles."""
    valid_tracts = tracts_income_gdf_utm[
        tracts_income_gdf_utm[income_col].notna()
        & np.isfinite(pd.to_numeric(tracts_income_gdf_utm[income_col], errors="coerce"))
        & (pd.to_numeric(tracts_income_gdf_utm[income_col], errors="coerce") > 0)
    ].copy()
    valid_tracts[income_col] = pd.to_numeric(valid_tracts[income_col], errors="coerce")
    if valid_tracts.empty:
        return {"n_income_tracts": 0, "strata": []}

    labels = (
        ["Low Income", "Middle Income", "High Income"]
        if n_quantiles == 3
        else [f"Q{i + 1}" for i in range(n_quantiles)]
    )
    valid_tracts["income_stratum"] = pd.qcut(
        valid_tracts[income_col],
        q=n_quantiles,
        labels=labels,
        duplicates="drop",
    )

    stratum_bounds: dict[str, dict[str, Any]] = {}
    for lbl in labels:
        sub = valid_tracts[valid_tracts["income_stratum"] == lbl]
        stratum_bounds[lbl] = {
            "n_tracts": len(sub),
            "min_income_usd": round(float(sub[income_col].min()), 0)
            if len(sub)
            else None,
            "max_income_usd": round(float(sub[income_col].max()), 0)
            if len(sub)
            else None,
        }

    # Spatial join helper
    def _assign_stratum(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        pts = gpd.GeoDataFrame(
            {"_idx": np.arange(len(xs))},
            geometry=gpd.points_from_xy(xs, ys),
            crs=valid_tracts.crs,
        )
        joined = gpd.sjoin(
            pts,
            valid_tracts[["income_stratum", "geometry"]],
            how="left",
            predicate="within",
        ).drop_duplicates(subset=["_idx"])
        joined = joined.sort_values("_idx")
        return (
            joined["income_stratum"]
            .astype(object)
            .where(joined["income_stratum"].notna(), None)
            .to_numpy()
        )

    sorted_radii = tuple(sorted(float(r) for r in radii_m))
    pos_scores: dict[str, dict[float, list[np.ndarray]]] = {
        lbl: {r: [] for r in sorted_radii} for lbl in labels
    }
    ctrl_scores: dict[str, dict[float, list[np.ndarray]]] = {
        lbl: {r: [] for r in sorted_radii} for lbl in labels
    }
    n_pos_unmatched = 0
    n_ctrl_unmatched = 0

    for ev in event_samples:
        pos_strata = _assign_stratum(ev["xs_pts"], ev["ys_pts"])
        ctrl_strata = _assign_stratum(ev["xs_ctrl"], ev["ys_ctrl"])
        n_pos_unmatched += int(sum(s is None for s in pos_strata))
        n_ctrl_unmatched += int(sum(s is None for s in ctrl_strata))
        for lbl in labels:
            p_mask = pos_strata == lbl
            c_mask = ctrl_strata == lbl
            for r_m in sorted_radii:
                if np.any(p_mask):
                    pos_scores[lbl][r_m].append(ev["ar_pts"][r_m][p_mask])
                if np.any(c_mask):
                    ctrl_scores[lbl][r_m].append(ev["ar_ctrl"][r_m][c_mask])

    strata_out: list[dict[str, Any]] = []
    for lbl in labels:
        by_buf: list[dict[str, Any]] = []
        n_pos_lbl = sum(len(a) for a in pos_scores[lbl][sorted_radii[0]])
        n_ctrl_lbl = sum(len(a) for a in ctrl_scores[lbl][sorted_radii[0]])
        for r_m in sorted_radii:
            p_arr = (
                np.concatenate(pos_scores[lbl][r_m])
                if pos_scores[lbl][r_m]
                else np.array([], dtype=np.float32)
            )
            c_arr = (
                np.concatenate(ctrl_scores[lbl][r_m])
                if ctrl_scores[lbl][r_m]
                else np.array([], dtype=np.float32)
            )
            cap = float(np.mean(p_arr >= depth_threshold_m)) if p_arr.size else 0.0
            ctrl = float(np.mean(c_arr >= depth_threshold_m)) if c_arr.size else 0.0
            lift = round(cap / ctrl, 3) if ctrl > 0.0 else None
            auc = compute_roc_auc(p_arr, c_arr) if (p_arr.size and c_arr.size) else 0.5
            by_buf.append(
                {
                    "radius_m": r_m,
                    "capture_rate": round(cap, 4),
                    "control_rate": round(ctrl, 4),
                    "lift": lift,
                    "ar_roc_auc": auc,
                }
            )
        strata_out.append(
            {
                "stratum": lbl,
                **stratum_bounds[lbl],
                "n_points": int(n_pos_lbl),
                "n_controls_pooled": int(n_ctrl_lbl),
                "by_buffer": by_buf,
            }
        )

    return {
        "n_income_tracts": len(valid_tracts),
        "n_points_unmatched_income": int(n_pos_unmatched),
        "n_controls_unmatched_income": int(n_ctrl_unmatched),
        "strata": strata_out,
    }


def render_v02_validation_markdown(
    sfpuc_summary: Mapping[str, Any],
    neighborhood_rows: Sequence[Mapping[str, Any]],
    fp_clusters: Sequence[Mapping[str, float]],
    val_311_metrics: Mapping[str, Any],
) -> str:
    """Render the v0.2 citywide dual-validation and 3-way 311 comparison report."""
    m = sfpuc_summary["metrics"]
    lines: list[str] = [
        "# San Francisco Citywide v0.2 Dual Validation Report",
        "",
        "## Model Configuration",
        f"- **Region:** `{sfpuc_summary['region']}`",
        f"- **Uniform Drainage Capacity:** `{sfpuc_summary['drainage_rate_mm_hr']:.1f} mm/hr`",
        f"- **Paved Manning's n:** `{sfpuc_summary['manning_n_paved']:.3f}`",
        f"- **Depth Threshold (SFPUC):** `{sfpuc_summary['depth_threshold_m']:.4f} m` (`6 in`)",
        "",
        "## 1. Citywide SFPUC 100-Year Storm Validation (`sf_citywide_100yr_slr0ft`)",
        "",
        "| Scope | Hit Rate | False Alarm Ratio | CSI | Land Cells | Obs Flood Cells | Mod Flood Cells |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| **Citywide Land (>= 0.5 m)** | {m['hit_rate']:.4f} | {m['false_alarm_ratio']:.4f} | {m['csi']:.4f} | — | — | — |",
    ]
    for row in neighborhood_rows:
        lines.append(
            f"| {row['neighborhood']} | {row['hit_rate']:.4f} | {row['false_alarm_ratio']:.4f} | "
            f"{row['csi']:.4f} | {row['land_cells']:,} | {row['obs_cells']:,} | {row['mod_cells']:,} |"
        )

    lines.extend(
        [
            "",
            "## 2. Dec 31, 2022 Atmospheric River Hindcast vs. SF 311 Reports (3-Way Comparison)",
            "",
            (
                f"- **311 Flood Reports Evaluated:** `{val_311_metrics['valid_land_points']}` of "
                f"`{val_311_metrics['total_raw_points']}` points on valid land "
                f"(`dropped={val_311_metrics['dropped_outside_land']}`)"
            ),
            f"- **Random Dry Control Points:** `{val_311_metrics['n_controls']}`",
            "",
            (
                "| Buffer | AR >= 0.05 m Hit Rate | AR 0.05 m Ctrl Rate | AR 0.05 m Lift | "
                "AR >= 0.1524 m Hit Rate | AR 0.1524 m Lift | SFPUC Hit Rate | SFPUC Lift | AR ROC-AUC |"
            ),
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for b in val_311_metrics["by_buffer"]:
        t05 = b["thresholds"]["0.05m"]
        t15 = b["thresholds"]["0.1524m"]
        sfp = b["sfpuc"]
        lift05 = f"{t05['lift']:.2f}x" if t05["lift"] is not None else "n/a"
        lift15 = f"{t15['lift']:.2f}x" if t15["lift"] is not None else "n/a"
        liftsf = f"{sfp['lift']:.2f}x" if sfp["lift"] is not None else "n/a"
        lines.append(
            f"| {b['radius_m']:.0f} m | {t05['capture_rate']:.1%} ({t05['hit_count']}) | "
            f"{t05['control_rate']:.1%} | {lift05} | "
            f"{t15['capture_rate']:.1%} ({t15['hit_count']}) | {lift15} | "
            f"{sfp['capture_rate']:.1%} ({sfp['hit_count']}) | {liftsf} | {b['ar_roc_auc']:.4f} |"
        )

    eval_r = float(val_311_metrics["eval_buffer_m"])
    lines.extend(
        [
            "",
            f"## 3. 311 Subtype Breakdown (`{eval_r:.0f} m` buffer)",
            "",
            "| Service Subtype | Count | AR >= 0.05 m (25 m) | AR >= 0.05 m (50 m) | AR >= 0.1524 m (50 m) | SFPUC (50 m) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for st in val_311_metrics["subtype_breakdown"]:
        lines.append(
            f"| {st['service_subtype']} | {st['count']} | {st['ar_05_capture_25m']:.1%} | "
            f"{st['ar_05_capture']:.1%} | {st['ar_15_capture']:.1%} | {st['sfpuc_capture']:.1%} |"
        )

    lines.extend(
        [
            "",
            f"## 4. Top SF Neighborhoods by Dec 31, 2022 311 Volume (`{eval_r:.0f} m` buffer)",
            "",
            "| Neighborhood | 311 Calls | AR >= 0.05 m (25 m) | AR >= 0.05 m (50 m) | AR >= 0.1524 m (50 m) | SFPUC (50 m) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for nb in val_311_metrics["neighborhood_breakdown"]:
        lines.append(
            f"| {nb['neighborhood']} | {nb['count']} | {nb['ar_05_capture_25m']:.1%} | "
            f"{nb['ar_05_capture']:.1%} | {nb['ar_15_capture']:.1%} | {nb['sfpuc_capture']:.1%} |"
        )

    lines.extend(
        [
            "",
            "## 5. Diagnostic Clusters",
            "",
            "### Top Land False-Positive Clusters (`sf_citywide_100yr_slr0ft` vs. SFPUC)",
            "",
            "| Area (`m^2`) | Centroid Easting (`m`) | Centroid Northing (`m`) |",
            "| ---: | ---: | ---: |",
        ]
    )
    for cl in fp_clusters:
        lines.append(
            f"| {cl['area_m2']:,.0f} | {cl['centroid_x_m']:.1f} | {cl['centroid_y_m']:.1f} |"
        )

    lines.extend(
        [
            "",
            f"### Top Missed 311 Clusters (`AR < 0.05 m` at `{eval_r:.0f} m`)",
            "",
            "| Neighborhood | Missed / Total | Caught by SFPUC | Sample Addresses |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for mc in val_311_metrics["top_missed_clusters"]:
        addrs = ", ".join(mc["sample_addresses"]) if mc["sample_addresses"] else "—"
        lines.append(
            f"| {mc['neighborhood']} | {mc['missed_count']} / {mc['total_count']} | "
            f"{mc['sfpuc_captured_count']} | {addrs} |"
        )

    lines.append("")
    return "\n".join(lines)


def _write_float32_geotiff(
    output_path: Path,
    data: np.ndarray,
    transform: Affine,
    crs: Any,
    nodata_value: float,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.tif")
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
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


def _generate_sweep_manning_raster(
    cfg: Mapping[str, Any],
    manning_n_paved: float,
    dem_shape: tuple[int, int],
    dem_transform: Affine,
    dem_nodata: float,
    valid_dem: np.ndarray,
    output_path: Path,
) -> Path:
    prep_mod = _load_pipeline_module("01_prep_terrain.py", "phase1_prep_terrain")
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    project_crs = str(cfg["project"]["crs"])

    nlcd_lc = prep_mod._reproject_to_grid(
        src_path=Path(domain["nlcd_landcover_source"]),
        dst_shape=dem_shape,
        dst_transform=dem_transform,
        dst_crs=project_crs,
        resampling=Resampling.nearest,
        dst_dtype=np.dtype(np.uint8),
        dst_nodata=0,
    )
    nlcd_imp = prep_mod._reproject_to_grid(
        src_path=Path(domain["nlcd_impervious_source"]),
        dst_shape=dem_shape,
        dst_transform=dem_transform,
        dst_crs=project_crs,
        resampling=Resampling.nearest,
        dst_dtype=np.dtype(np.uint8),
        dst_nodata=None,
        ignore_src_nodata=True,
    )

    bldgs_gdf = gpd.read_file(Path(domain["buildings_source"]))
    if bldgs_gdf.crs is None:
        raise ValueError("buildings_source is missing CRS")
    bldgs_utm = bldgs_gdf.to_crs(project_crs)
    bldgs_utm = bldgs_utm[bldgs_utm.intersects(box(*domain["bbox_epsg26910"]))]
    bldg_shapes = [
        (geom, 1)
        for geom in bldgs_utm.geometry
        if geom is not None and not geom.is_empty
    ]
    building_mask = (
        rasterize(
            bldg_shapes,
            out_shape=dem_shape,
            transform=dem_transform,
            fill=0,
            dtype=np.uint8,
        ).astype(bool)
        if bldg_shapes
        else np.zeros(dem_shape, dtype=bool)
    )

    manning_grid = prep_mod.compute_manning_n_grid(
        nlcd_landcover=nlcd_lc,
        nlcd_impervious_pct=nlcd_imp,
        valid_mask=valid_dem,
        manning_n_paved=manning_n_paved,
        manning_n_water=float(tprep.get("manning_n_water", 0.020)),
        manning_n_open_space=float(tprep.get("manning_n_open_space", 0.035)),
        manning_n_vegetation=float(tprep.get("manning_n_vegetation", 0.060)),
        nodata_value=dem_nodata,
    )
    dummy_dem = np.where(valid_dem, 10.0, dem_nodata).astype(np.float32)
    _, manning_bldg = prep_mod.apply_building_footprints(
        dem_m=dummy_dem,
        manning_n=manning_grid,
        building_mask=building_mask,
        handling="high_manning",
        building_elevation_bump_m=0.0,
        building_manning_n=float(tprep.get("building_manning_n", 0.30)),
        nodata_value=dem_nodata,
    )
    prep_mod._write_geotiff(
        output_path, manning_bldg, dem_transform, project_crs, dem_nodata
    )
    return output_path


def _ensure_sweep_run(
    cfg: Mapping[str, Any],
    scenario_id: str,
    drainage_rate_mm_hr: float,
    manning_n_paved: float,
    dem_shape: tuple[int, int],
    dem_transform: Affine,
    dem_nodata: float,
    valid_dem: np.ndarray,
    force: bool,
) -> Path:
    build_mod = _load_pipeline_module("02_build_sfincs.py", "phase2_build_sfincs")
    tprep = cfg["terrain_prep"]
    sfincs_cfg = cfg["sfincs_model"]
    runs_dir = Path(cfg["paths"]["runs_dir"])

    base_drainage = float(sfincs_cfg["drainage_rate_mm_hr"])
    base_manning = float(tprep["manning_n_paved"])
    if math.isclose(drainage_rate_mm_hr, base_drainage) and math.isclose(
        manning_n_paved, base_manning
    ):
        baseline_map_nc = runs_dir / scenario_id / "sfincs_map.nc"
        if baseline_map_nc.exists() and not force:
            return baseline_map_nc

    run_dir = runs_dir / f"sweep_d{drainage_rate_mm_hr:g}_n{manning_n_paved:.3f}"
    if math.isclose(drainage_rate_mm_hr, base_drainage) and math.isclose(
        manning_n_paved, base_manning
    ):
        run_dir = runs_dir / scenario_id

    map_nc_path = run_dir / "sfincs_map.nc"
    if map_nc_path.exists() and map_nc_path.stat().st_size > 0 and not force:
        print(f"[03_validate_sf] Reusing cached run {map_nc_path}")
        return map_nc_path

    run_dir.mkdir(parents=True, exist_ok=True)
    if math.isclose(manning_n_paved, base_manning):
        manning_path = Path(tprep["manning_n_file"])
    else:
        manning_path = _generate_sweep_manning_raster(
            cfg=cfg,
            manning_n_paved=manning_n_paved,
            dem_shape=dem_shape,
            dem_transform=dem_transform,
            dem_nodata=dem_nodata,
            valid_dem=valid_dem,
            output_path=run_dir / "manning_n_1m.tif",
        )

    scenario = build_mod.resolve_scenarios(cfg, scenario_id=scenario_id)[0]
    rain_cfg = cfg["rain_events"][scenario["rain"]]
    domain = cfg["domain"]
    project_crs = str(cfg["project"]["crs"])
    hydro_dem_path = Path(tprep["hydro_dem_file"])
    curve_number_path = Path(tprep["curve_number_file"])

    grid_spec = build_mod.compute_sfincs_grid_spec(
        bbox_utm=domain["bbox_epsg26910"],
        grid_resolution_m=float(domain["grid_resolution_m"]),
        subgrid_resolution_m=float(domain["subgrid_resolution_m"]),
        project_crs=project_crs,
    )
    hyetograph_rows = build_mod._read_hyetograph_csv(Path(rain_cfg["hyetograph_file"]))
    duration_hr = float(rain_cfg["duration_hr"])
    tref = datetime.fromisoformat(str(sfincs_cfg["reference_time_utc"])).replace(
        tzinfo=None
    )
    tstop = tref + timedelta(hours=duration_hr)
    precip_series = build_mod.build_precip_series(
        hyetograph_rows=hyetograph_rows,
        reference_time_utc=tref,
        duration_hr=duration_hr,
        drainage_rate_mm_hr=drainage_rate_mm_hr,
    )

    print(
        f"[03_validate_sf] Building & running sweep "
        f"drainage={drainage_rate_mm_hr:.1f} mm/hr, n_paved={manning_n_paved:.3f} -> {run_dir}"
    )
    sf = SfincsModel(root=str(run_dir), mode="w")
    sf.data_catalog._fallback_lib = None
    sf.setup_grid(
        x0=grid_spec["x0"],
        y0=grid_spec["y0"],
        dx=grid_spec["dx"],
        dy=grid_spec["dy"],
        nmax=grid_spec["nmax"],
        mmax=grid_spec["mmax"],
        rotation=grid_spec["rotation"],
        epsg=grid_spec["epsg"],
    )
    sf.setup_dep(datasets_dep=[{"elevtn": hydro_dem_path.resolve()}])
    sf.setup_mask_active(zmin=-50.0, reset_mask=True)
    if bool(sfincs_cfg.get("outflow_perimeter_bounds", True)):
        sf.setup_mask_bounds(btype="outflow", reset_bounds=True)

    sf.setup_subgrid(
        datasets_dep=[{"elevtn": hydro_dem_path.resolve()}],
        datasets_rgh=[{"manning": manning_path.resolve()}],
        nr_subgrid_pixels=grid_spec["nr_subgrid_pixels"],
        nbins=int(sfincs_cfg.get("subgrid_nbins", 10)),
        max_gradient=float(sfincs_cfg.get("subgrid_max_gradient", 5.0)),
    )
    sf.setup_cn_infiltration(
        cn=curve_number_path.resolve(),
        antecedent_moisture=None,
        reproj_method=str(sfincs_cfg.get("cn_reproj_method", "med")),
    )
    sf.setup_config(
        tref=tref,
        tstart=tref,
        tstop=tstop,
        dtout=float(sfincs_cfg.get("dtout_s", 3600.0)),
        dtmaxout=float(sfincs_cfg.get("dtmaxout_s", 86400.0)),
        alpha=float(sfincs_cfg.get("alpha", 0.5)),
        huthresh=float(sfincs_cfg.get("huthresh_m", 0.01)),
        advection=int(sfincs_cfg.get("advection", 1)),
        precipfile="sfincs.prp",
    )
    sf.set_forcing(precip_series.to_xarray(), name="precip")
    sf.write()

    docker_image = str(sfincs_cfg.get("docker_image", "deltares/sfincs-cpu:latest"))
    cmd = build_mod.build_docker_run_command(run_dir, docker_image)
    subprocess.run(cmd, check=True)
    if not map_nc_path.exists() or map_nc_path.stat().st_size == 0:
        raise RuntimeError(f"Sweep SFINCS run failed to produce {map_nc_path}")
    return map_nc_path


def downscale_quadtree_zsmax_to_grid(
    face_x: np.ndarray,
    face_y: np.ndarray,
    face_zsmax: np.ndarray,
    dem_m: np.ndarray,
    dem_transform: Affine,
    dem_nodata: float,
    max_dist_m: float = 80.0,
) -> np.ndarray:
    """Downscale UGRID/quadtree face-centroid zsmax onto a 2 m DEM grid via cKDTree."""
    fx = np.asarray(face_x, dtype=np.float64).ravel()
    fy = np.asarray(face_y, dtype=np.float64).ravel()
    fz = np.asarray(face_zsmax, dtype=np.float32).ravel()
    valid_faces = np.isfinite(fx) & np.isfinite(fy) & np.isfinite(fz)

    height, width = dem_m.shape
    depth_m = np.full((height, width), dem_nodata, dtype=np.float32)
    dem_valid = np.isfinite(dem_m)
    if not np.isnan(dem_nodata):
        dem_valid &= ~np.isclose(dem_m, float(dem_nodata))

    if not np.any(valid_faces) or not np.any(dem_valid):
        return depth_m

    tree = cKDTree(np.column_stack((fx[valid_faces], fy[valid_faces])))
    fz_valid = fz[valid_faces]

    cols = np.arange(width, dtype=np.float64) + 0.5
    rows = np.arange(height, dtype=np.float64) + 0.5
    col_grid, row_grid = np.meshgrid(cols, rows)
    gx = dem_transform.c + col_grid * dem_transform.a + row_grid * dem_transform.b
    gy = dem_transform.f + col_grid * dem_transform.d + row_grid * dem_transform.e

    mask_idx = np.flatnonzero(dem_valid)
    pts = np.column_stack((gx.ravel()[mask_idx], gy.ravel()[mask_idx]))
    dists, nn_idx = tree.query(pts, distance_upper_bound=float(max_dist_m))
    within = np.isfinite(dists) & (nn_idx < len(fz_valid))

    zs_flat = np.full(mask_idx.size, np.nan, dtype=np.float32)
    zs_flat[within] = fz_valid[nn_idx[within]]

    dem_vals = dem_m.ravel()[mask_idx].astype(np.float32)
    out_vals = np.where(within, np.maximum(0.0, zs_flat - dem_vals), 0.0).astype(
        np.float32
    )
    depth_flat = depth_m.ravel()
    depth_flat[mask_idx] = out_vals
    return depth_m


def _evaluate_run(
    map_nc_path: Path,
    src_transform: Affine,
    project_crs: str,
    dem_m: np.ndarray,
    dst_transform: Affine,
    dem_nodata: float,
    obs_mask: np.ndarray,
    land_valid_mask: np.ndarray,
    depth_threshold_m: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    if not map_nc_path.exists():
        raise FileNotFoundError(f"SFINCS output not found: {map_nc_path}")

    with xr.open_dataset(map_nc_path) as ds:
        zsmax_da = ds["zsmax"]
        if "timemax" in zsmax_da.dims:
            zsmax_da = zsmax_da.isel(timemax=-1)
        is_ugrid = "mesh2d_nFaces" in ds.dims or "mesh2d_face_x" in ds
        if is_ugrid:
            face_x = ds["mesh2d_face_x"].values
            face_y = ds["mesh2d_face_y"].values
            face_zsmax = zsmax_da.values.astype(np.float32)
            depth_m = downscale_quadtree_zsmax_to_grid(
                face_x=face_x,
                face_y=face_y,
                face_zsmax=face_zsmax,
                dem_m=dem_m,
                dem_transform=dst_transform,
                dem_nodata=dem_nodata,
            )
        else:
            zsmax_south_up = zsmax_da.values.astype(np.float32)
            depth_m = downscale_zsmax_to_depth_m(
                zsmax_south_up=zsmax_south_up,
                src_transform=src_transform,
                src_crs=project_crs,
                dem_m=dem_m,
                dst_transform=dst_transform,
                dst_crs=project_crs,
                dem_nodata=dem_nodata,
            )
    metrics = compute_flood_metrics(
        depth_m=depth_m,
        obs_mask=obs_mask,
        valid_mask=land_valid_mask,
        depth_threshold_m=depth_threshold_m,
    )
    return depth_m, metrics


def extract_sfincs_gauge_series(
    map_nc_path: Path,
    x_epsg26910: float,
    y_epsg26910: float,
    *,
    max_snap_distance_m: float = 500.0,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]] | None:
    """Extract modeled SFINCS water-surface time series (NAVD88 m) at the nearest active cell."""
    with xr.open_dataset(map_nc_path) as ds:
        if "zs" not in ds or "time" not in ds:
            return None
        x_grid = ds["x"].values.astype(np.float64)
        y_grid = ds["y"].values.astype(np.float64)
        dist = np.hypot(x_grid - float(x_epsg26910), y_grid - float(y_epsg26910))
        if "msk" in ds:
            active = ds["msk"].values > 0
            dist = np.where(active, dist, np.inf)
        min_dist = float(np.min(dist))
        if not np.isfinite(min_dist) or min_dist > float(max_snap_distance_m):
            return None

        r_idx, c_idx = np.unravel_index(int(np.argmin(dist)), dist.shape)
        zs_series = ds["zs"].values[:, r_idx, c_idx].astype(np.float64)
        times = pd.to_datetime(ds["time"].values)
        time_min = ((times - times[0]).total_seconds() / 60.0).to_numpy(
            dtype=np.float64
        )
        cell_info = {
            "row": int(r_idx),
            "col": int(c_idx),
            "snap_distance_m": round(min_dist, 2),
        }
    return time_min, zs_series, cell_info


def evaluate_event_gauges(
    gauges_file: Path,
    map_nc_path: Path,
    *,
    tide_gauges_cfg: Sequence[Mapping[str, Any]] = (),
    zsmax_m: np.ndarray | None = None,
    transform: Affine | None = None,
    nodata_value: float = -9999.0,
    max_snap_distance_m: float = 500.0,
) -> dict[str, Any] | None:
    """Evaluate Tier A coastal/stream gauges and/or HWMs for one hindcast event."""
    if not gauges_file.exists():
        return None

    stations_out: list[dict[str, Any]] = []
    hwm_summary: dict[str, Any] | None = None

    suffix = gauges_file.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(gauges_file)
        if "time_min" in df.columns:
            gauge_meta = {str(g["id"]): g for g in tide_gauges_cfg if "id" in g}
            for col in df.columns:
                if not (col.startswith("wl_") and col.endswith("_m")):
                    continue
                station_id = col[len("wl_") : -len("_m")]
                meta = gauge_meta.get(station_id)
                if meta is None:
                    continue
                extracted = extract_sfincs_gauge_series(
                    map_nc_path=map_nc_path,
                    x_epsg26910=float(meta["x_epsg26910"]),
                    y_epsg26910=float(meta["y_epsg26910"]),
                    max_snap_distance_m=max_snap_distance_m,
                )
                if extracted is None:
                    continue
                mod_t_min, mod_zs_m, cell_info = extracted
                obs_df = df[["time_min", col]].dropna()
                obs_by_t = dict(
                    zip(
                        np.round(obs_df["time_min"].to_numpy(dtype=np.float64), 3),
                        obs_df[col].to_numpy(dtype=np.float64),
                        strict=True,
                    )
                )
                aligned_t: list[float] = []
                aligned_obs: list[float] = []
                aligned_mod: list[float] = []
                for idx, (t_m, m_val) in enumerate(
                    zip(mod_t_min, mod_zs_m, strict=True)
                ):
                    # Skip cold-start t=0 initial condition (zs=0.0 before boundary spinup)
                    if idx == 0 and math.isclose(t_m, 0.0) and math.isclose(m_val, 0.0):
                        continue
                    t_key = round(float(t_m), 3)
                    if t_key in obs_by_t and np.isfinite(m_val):
                        aligned_t.append(float(t_m))
                        aligned_obs.append(float(obs_by_t[t_key]))
                        aligned_mod.append(float(m_val))
                if not aligned_t:
                    continue
                m = compute_gauge_timeseries_metrics(
                    time_min=aligned_t,
                    obs_wse_navd88_m=aligned_obs,
                    mod_wse_navd88_m=aligned_mod,
                )
                stations_out.append(
                    {
                        "station_id": station_id,
                        "name": str(meta.get("name", station_id)),
                        "snap_distance_m": cell_info["snap_distance_m"],
                        **m,
                    }
                )
    elif suffix == ".json":
        payload = json.loads(gauges_file.read_text(encoding="utf-8"))
        if (
            isinstance(payload, Mapping)
            and "hwm_records" in payload
            and zsmax_m is not None
            and transform is not None
        ):
            hwm_gdf = parse_usgs_stn_hwm_records(payload["hwm_records"])
            if len(hwm_gdf) > 0:
                pts_xy = list(
                    zip(
                        hwm_gdf["x_epsg26910"].to_numpy(dtype=np.float64),
                        hwm_gdf["y_epsg26910"].to_numpy(dtype=np.float64),
                        strict=True,
                    )
                )
                mod_vals = sample_raster_at_points(
                    zsmax_m, pts_xy, transform, nodata_value=nodata_value
                )
                hwm_summary = compute_hwm_metrics(
                    obs_wse_navd88_m=hwm_gdf["wse_navd88_m"].to_numpy(dtype=np.float64),
                    mod_wse_navd88_m=mod_vals,
                )

    if not stations_out and hwm_summary is None:
        return None

    rmses = [s["rmse_m"] for s in stations_out if s.get("rmse_m") is not None]
    maes = [s["mae_m"] for s in stations_out if s.get("mae_m") is not None]
    biases = [s["bias_m"] for s in stations_out if s.get("bias_m") is not None]
    timings = [
        s["peak_timing_error_min"]
        for s in stations_out
        if s.get("peak_timing_error_min") is not None
    ]
    summary = {
        "rmse_m": round(float(np.mean(rmses)), 4)
        if rmses
        else (hwm_summary.get("rmse_m") if hwm_summary else None),
        "mae_m": round(float(np.mean(maes)), 4)
        if maes
        else (hwm_summary.get("mae_m") if hwm_summary else None),
        "bias_m": round(float(np.mean(biases)), 4)
        if biases
        else (hwm_summary.get("bias_m") if hwm_summary else None),
        "peak_timing_error_min": round(float(np.mean(timings)), 1) if timings else None,
    }
    return {
        "vertical_datum": "NAVD88",
        "n_stations": len(stations_out),
        "n_hwm": int(hwm_summary["n_hwm"]) if hwm_summary else 0,
        "stations": stations_out,
        "hwm": hwm_summary,
        "summary": summary,
    }


def _load_vector_or_csv_points(file_path: Path, target_crs: str) -> gpd.GeoDataFrame:
    """Load a vector file (.gpkg/.geojson) or CSV with lat/lon into target_crs."""
    if file_path.suffix.lower() == ".csv":
        df = pd.read_csv(file_path)
        lat_col = next((c for c in ("lat", "latitude", "y") if c in df.columns), None)
        lon_col = next(
            (c for c in ("lon", "lng", "longitude", "x") if c in df.columns), None
        )
        if lat_col is None or lon_col is None:
            raise ValueError(f"{file_path} CSV is missing lat/lon columns.")
        src_crs = "EPSG:4326" if lat_col in ("lat", "latitude") else target_crs
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
            crs=src_crs,
        )
    else:
        gdf = gpd.read_file(file_path)
        if gdf.crs is None:
            raise ValueError(f"{file_path} is missing CRS.")
    return (
        gdf.to_crs(target_crs)
        if CRS.from_user_input(gdf.crs) != CRS.from_user_input(target_crs)
        else gdf
    )


def evaluate_event_road_closures(
    road_closures_file: Path,
    roads_file: Path | None,
    ar_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    project_crs: str = "EPSG:26910",
    nodata_value: float = -9999.0,
) -> dict[str, Any] | None:
    """Load Tier C road-closure records and score against modeled AR flood depth."""
    if not road_closures_file.exists():
        return None
    closures_utm = _load_vector_or_csv_points(road_closures_file, project_crs)
    if closures_utm.empty:
        return None

    roads_utm: gpd.GeoDataFrame | None = None
    if roads_file is not None and roads_file.exists():
        roads_raw = gpd.read_file(roads_file)
        if roads_raw.crs is not None:
            roads_utm = (
                roads_raw.to_crs(project_crs)
                if CRS.from_user_input(roads_raw.crs)
                != CRS.from_user_input(project_crs)
                else roads_raw
            )
            if "highway" in roads_utm.columns:
                roads_utm = roads_utm[roads_utm["highway"].notna()].copy()

    try:
        return compute_road_closure_metrics(
            closures_gdf_utm=closures_utm,
            ar_depth_m=ar_depth_m,
            valid_land_mask=valid_land_mask,
            transform=transform,
            roads_gdf_utm=roads_utm,
            nodata_value=nodata_value,
        )
    except ValueError:
        return None


def evaluate_groundsource_crosscheck(
    groundsource_file: Path,
    modeled_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    domain_bbox_utm: Sequence[float] | None = None,
    subbasins_file: Path | None = None,
    project_crs: str = "EPSG:26910",
    depth_threshold_m: float = 0.05,
    nodata_value: float = -9999.0,
    event_windows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Load Google Research Groundsource GeoParquet/GPKG and run Tier D sub-basin crosscheck."""
    if not groundsource_file.exists():
        return None
    suffix = groundsource_file.suffix.lower()
    if suffix in (".parquet", ".geoparquet"):
        gs_raw = gpd.read_parquet(groundsource_file)
    else:
        gs_raw = gpd.read_file(groundsource_file)
    if gs_raw.crs is None:
        gs_raw = gs_raw.set_crs("EPSG:4326")

    gs_utm = filter_groundsource_records(
        gs_raw,
        max_area_km2=25.0,
        domain_bbox_utm=domain_bbox_utm,
        target_crs=project_crs,
    )

    subbasins_utm: gpd.GeoDataFrame | None = None
    if subbasins_file is not None and subbasins_file.exists():
        sb_raw = gpd.read_file(subbasins_file)
        if sb_raw.crs is None:
            raise ValueError(f"{subbasins_file} is missing CRS.")
        subbasins_utm = (
            sb_raw.to_crs(project_crs)
            if CRS.from_user_input(sb_raw.crs) != CRS.from_user_input(project_crs)
            else sb_raw
        )

    return evaluate_groundsource_subbasin_match(
        groundsource_gdf_utm=gs_utm,
        modeled_depth_m=modeled_depth_m,
        valid_land_mask=valid_land_mask,
        transform=transform,
        subbasins_gdf_utm=subbasins_utm,
        domain_bbox_utm=domain_bbox_utm,
        depth_threshold_m=depth_threshold_m,
        nodata_value=nodata_value,
        event_windows=event_windows,
    )


def _safe_spearman(
    x: np.ndarray, y: np.ndarray, *, min_n: int = 3
) -> tuple[float | None, float | None]:
    """Compute Spearman rank correlation safely, returning (None, None) on constant/short inputs."""
    mask = np.isfinite(x) & np.isfinite(y)
    xv = x[mask]
    yv = y[mask]
    if len(xv) < min_n:
        return None, None
    if np.all(xv == xv[0]) or np.all(yv == yv[0]):
        return None, None
    res = stats.spearmanr(xv, yv)
    rho = float(res.statistic)
    pval = float(res.pvalue)
    if not (math.isfinite(rho) and math.isfinite(pval)):
        return None, None
    return round(rho, 6), round(pval, 6)


def _safe_kendall(
    x: np.ndarray, y: np.ndarray, *, min_n: int = 3
) -> tuple[float | None, float | None]:
    """Compute ties-aware Kendall tau-b correlation safely."""
    mask = np.isfinite(x) & np.isfinite(y)
    xv = x[mask]
    yv = y[mask]
    if len(xv) < min_n:
        return None, None
    if np.all(xv == xv[0]) or np.all(yv == yv[0]):
        return None, None
    res = stats.kendalltau(xv, yv, variant="b")
    tau = float(res.statistic)
    pval = float(res.pvalue)
    if not (math.isfinite(tau) and math.isfinite(pval)):
        return None, None
    return round(tau, 6), round(pval, 6)


def compute_tract_building_exposure(
    tracts_gdf_utm: gpd.GeoDataFrame,
    buildings_gdf_utm: gpd.GeoDataFrame | None,
    modeled_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    *,
    depth_threshold_m: float = 0.05,
    nodata_value: float = -9999.0,
    tract_id_col: str = "census_tract",
) -> pd.DataFrame:
    """Aggregate modeled flood depth and building exposure by census tract (Tier E)."""
    if tracts_gdf_utm.crs is None:
        raise ValueError("tracts_gdf_utm must have a CRS.")
    assert_crs_equals(tracts_gdf_utm.crs, "EPSG:26910", "tracts_gdf_utm")

    if tract_id_col not in tracts_gdf_utm.columns:
        for alt in ("census_tract", "GEOID", "geoid", "GEOID20"):
            if alt in tracts_gdf_utm.columns:
                tract_id_col = alt
                break
        else:
            raise ValueError(
                f"Tract ID column {tract_id_col!r} not found in tracts_gdf_utm."
            )

    tracts = tracts_gdf_utm.copy().reset_index(drop=True)
    tracts["census_tract"] = tracts[tract_id_col].astype(str)
    tracts["_tract_idx"] = np.arange(1, len(tracts) + 1, dtype=np.int32)

    height, width = modeled_depth_m.shape
    cell_area_m2 = abs(float(transform.a) * float(transform.e))
    finite_depth = np.isfinite(modeled_depth_m) & (modeled_depth_m != nodata_value)
    flooded_land = (
        valid_land_mask & finite_depth & (modeled_depth_m >= depth_threshold_m)
    )

    if len(tracts) > 0:
        shapes = [
            (geom, int(idx))
            for geom, idx in zip(tracts.geometry, tracts["_tract_idx"], strict=True)
            if geom is not None and not geom.is_empty
        ]
        tract_grid = (
            rasterize(
                shapes,
                out_shape=(height, width),
                transform=transform,
                fill=0,
                dtype="int32",
            )
            if shapes
            else np.zeros((height, width), dtype=np.int32)
        )
        valid_counts = np.bincount(
            tract_grid[valid_land_mask].ravel(), minlength=len(tracts) + 1
        )
        flooded_counts = np.bincount(
            tract_grid[flooded_land].ravel(), minlength=len(tracts) + 1
        )
        tracts["valid_land_area_m2"] = (
            valid_counts[1 : len(tracts) + 1].astype(float) * cell_area_m2
        )
        tracts["flooded_land_area_m2"] = (
            flooded_counts[1 : len(tracts) + 1].astype(float) * cell_area_m2
        )
    else:
        tracts["valid_land_area_m2"] = 0.0
        tracts["flooded_land_area_m2"] = 0.0

    tracts["total_buildings"] = 0
    tracts["exposed_buildings"] = 0
    tracts["exposed_building_fraction"] = 0.0
    tracts["exposed_building_area_m2"] = 0.0
    tracts["mean_building_depth_m"] = 0.0

    if buildings_gdf_utm is not None and len(buildings_gdf_utm) > 0 and len(tracts) > 0:
        assert_crs_equals(buildings_gdf_utm.crs, "EPSG:26910", "buildings_gdf_utm")
        bldgs = buildings_gdf_utm[
            buildings_gdf_utm.geometry.notna() & ~buildings_gdf_utm.geometry.is_empty
        ].copy()
        if len(bldgs) > 0:
            rep_pts = bldgs.geometry.representative_point()
            coords_xy = list(
                zip(
                    rep_pts.x.to_numpy(dtype=float),
                    rep_pts.y.to_numpy(dtype=float),
                    strict=True,
                )
            )
            sampled_depths = sample_raster_at_points(
                modeled_depth_m,
                coords_xy,
                transform,
                nodata_value=nodata_value,
            )
            # Mask out points outside valid_land_mask
            rows_idx, cols_idx = rowcol(
                transform,
                [pt[0] for pt in coords_xy],
                [pt[1] for pt in coords_xy],
            )
            rows_arr = np.asarray(rows_idx, dtype=np.int64)
            cols_arr = np.asarray(cols_idx, dtype=np.int64)
            in_bounds = (
                (rows_arr >= 0)
                & (rows_arr < height)
                & (cols_arr >= 0)
                & (cols_arr < width)
            )
            valid_pt = np.zeros(len(bldgs), dtype=bool)
            valid_pt[in_bounds] = valid_land_mask[
                rows_arr[in_bounds], cols_arr[in_bounds]
            ]
            depths_clean = np.where(
                valid_pt & np.isfinite(sampled_depths), sampled_depths, 0.0
            )
            bldgs["_depth_m"] = depths_clean
            bldgs["_exposed"] = depths_clean >= depth_threshold_m
            bldgs["_area_m2"] = bldgs.geometry.area.astype(float)
            bldgs["_exposed_area_m2"] = np.where(
                bldgs["_exposed"], bldgs["_area_m2"], 0.0
            )

            pts_gdf = gpd.GeoDataFrame(
                bldgs[["_depth_m", "_exposed", "_exposed_area_m2"]],
                geometry=rep_pts,
                crs=bldgs.crs,
            )
            joined = gpd.sjoin(
                pts_gdf,
                tracts[["_tract_idx", "geometry"]],
                how="inner",
                predicate="within",
            )
            if len(joined) > 0:
                grouped = joined.groupby("_tract_idx").agg(
                    total_buildings=("_exposed", "count"),
                    exposed_buildings=("_exposed", "sum"),
                    exposed_building_area_m2=("_exposed_area_m2", "sum"),
                )
                exposed_only = (
                    joined[joined["_exposed"]].groupby("_tract_idx")["_depth_m"].mean()
                )
                for t_idx, row in grouped.iterrows():
                    mask_t = tracts["_tract_idx"] == t_idx
                    tot = int(row["total_buildings"])
                    exp = int(row["exposed_buildings"])
                    tracts.loc[mask_t, "total_buildings"] = tot
                    tracts.loc[mask_t, "exposed_buildings"] = exp
                    tracts.loc[mask_t, "exposed_building_fraction"] = (
                        float(exp) / float(tot) if tot > 0 else 0.0
                    )
                    tracts.loc[mask_t, "exposed_building_area_m2"] = float(
                        row["exposed_building_area_m2"]
                    )
                    if t_idx in exposed_only.index:
                        tracts.loc[mask_t, "mean_building_depth_m"] = float(
                            exposed_only.loc[t_idx]
                        )

    keep_cols = [c for c in tracts.columns if c not in {"geometry", "_tract_idx"}]
    return pd.DataFrame(tracts[keep_cols]).reset_index(drop=True)


def compute_nfip_spearman_correlation(
    tract_df: pd.DataFrame,
    *,
    claim_col: str = "claim_count",
    exposure_col: str = "exposed_buildings",
    min_tracts: int = 3,
) -> dict[str, Any]:
    """Compute tract-level Spearman rank correlation between NFIP claims and modeled exposure."""
    if exposure_col not in tract_df.columns or (
        tract_df[exposure_col].sum() == 0
        and "flooded_land_area_m2" in tract_df.columns
        and tract_df.get("total_buildings", pd.Series([0])).sum() == 0
    ):
        exposure_col = "flooded_land_area_m2"

    rho, p_val = (
        _safe_spearman(
            tract_df[claim_col].to_numpy(dtype=float),
            tract_df[exposure_col].to_numpy(dtype=float),
            min_n=min_tracts,
        )
        if claim_col in tract_df.columns and exposure_col in tract_df.columns
        else (None, None)
    )
    tau_b, tau_p = (
        _safe_kendall(
            tract_df[claim_col].to_numpy(dtype=float),
            tract_df[exposure_col].to_numpy(dtype=float),
            min_n=min_tracts,
        )
        if claim_col in tract_df.columns and exposure_col in tract_df.columns
        else (None, None)
    )

    additional: dict[str, dict[str, float | None]] = {}
    candidate_pairs = (
        ("total_paid_usd", "exposed_buildings"),
        ("claim_count", "exposed_building_area_m2"),
        ("claim_count", "exposed_building_fraction"),
        ("claim_count", "flooded_land_area_m2"),
        ("total_paid_usd", "flooded_land_area_m2"),
    )
    for c_col, e_col in candidate_pairs:
        if c_col in tract_df.columns and e_col in tract_df.columns:
            r_c, p_c = _safe_spearman(
                tract_df[c_col].to_numpy(dtype=float),
                tract_df[e_col].to_numpy(dtype=float),
                min_n=min_tracts,
            )
            t_c, tp_c = _safe_kendall(
                tract_df[c_col].to_numpy(dtype=float),
                tract_df[e_col].to_numpy(dtype=float),
                min_n=min_tracts,
            )
            additional[f"{c_col}_vs_{e_col}"] = {
                "spearman_rho": r_c,
                "p_value": p_c,
                "kendall_tau_b": t_c,
                "kendall_p_value": tp_c,
            }

    total_claims = (
        int(tract_df["claim_count"].sum()) if "claim_count" in tract_df.columns else 0
    )
    n_tracts_with_claims = (
        int((tract_df["claim_count"] > 0).sum())
        if "claim_count" in tract_df.columns
        else 0
    )
    n_tracts = len(tract_df)
    n_zero_claim_tracts = n_tracts - n_tracts_with_claims
    zero_claim_fraction = (
        round(float(n_zero_claim_tracts) / float(n_tracts), 4) if n_tracts > 0 else 0.0
    )
    total_paid = (
        round(float(tract_df["total_paid_usd"].sum()), 2)
        if "total_paid_usd" in tract_df.columns
        else 0.0
    )

    # Binary presence Mann-Whitney U test (tracts with >= 1 claim vs 0 claims) on flooded_land_area_m2
    binary_presence_mwu: dict[str, Any] = {}
    if "claim_count" in tract_df.columns and "flooded_land_area_m2" in tract_df.columns:
        has_claim = tract_df["claim_count"].to_numpy(dtype=float) > 0
        pos_area = tract_df.loc[has_claim, "flooded_land_area_m2"].to_numpy(dtype=float)
        neg_area = tract_df.loc[~has_claim, "flooded_land_area_m2"].to_numpy(
            dtype=float
        )
        if pos_area.size > 0 and neg_area.size > 0:
            u_res = stats.mannwhitneyu(pos_area, neg_area, alternative="greater")
            binary_presence_mwu = {
                "auc": compute_roc_auc(pos_area, neg_area),
                "p_value": round(float(u_res.pvalue), 6),
                "median_flooded_m2_with_claims": round(float(np.median(pos_area)), 1),
                "median_flooded_m2_zero_claims": round(float(np.median(neg_area)), 1),
            }

    return {
        "n_tracts": n_tracts,
        "n_tracts_with_claims": n_tracts_with_claims,
        "n_zero_claim_tracts": n_zero_claim_tracts,
        "zero_claim_tract_fraction": zero_claim_fraction,
        "total_claims": total_claims,
        "total_paid_usd": total_paid,
        "primary_claim_metric": claim_col,
        "primary_exposure_metric": exposure_col,
        "spearman_rho": rho,
        "p_value": p_val,
        "kendall_tau_b": tau_b,
        "kendall_p_value": tau_p,
        "binary_presence_mwu": binary_presence_mwu,
        "additional_correlations": additional,
    }


def evaluate_nfip_crosscheck(
    nfip_claims_file: Path,
    modeled_depth_m: np.ndarray,
    valid_land_mask: np.ndarray,
    transform: Affine,
    *,
    buildings_file: Path | None = None,
    tracts_file: Path | None = None,
    project_crs: str = "EPSG:26910",
    depth_threshold_m: float = 0.05,
    nodata_value: float = -9999.0,
) -> dict[str, Any] | None:
    """Load OpenFEMA NFIP tract claims and score Spearman rho against modeled exposure."""
    if not nfip_claims_file.exists():
        return None
    suffix = nfip_claims_file.suffix.lower()
    if suffix == ".csv":
        if tracts_file is None or not tracts_file.exists():
            raise ValueError("tracts_file is required when nfip_claims_file is a CSV.")
        claims_df = pd.read_csv(nfip_claims_file, dtype={"census_tract": str})
        tracts_raw = gpd.read_file(tracts_file)
        if tracts_raw.crs is None:
            tracts_raw = tracts_raw.set_crs("EPSG:4326")
        tracts_raw["census_tract"] = tracts_raw[
            "census_tract" if "census_tract" in tracts_raw.columns else "GEOID"
        ].astype(str)
        tracts_gdf = tracts_raw.merge(claims_df, on="census_tract", how="left")
    elif suffix in {".parquet", ".geoparquet"}:
        tracts_gdf = gpd.read_parquet(nfip_claims_file)
    else:
        tracts_gdf = gpd.read_file(nfip_claims_file)

    if tracts_gdf.crs is None:
        tracts_gdf = tracts_gdf.set_crs("EPSG:4326")
    tracts_utm = (
        tracts_gdf.to_crs(project_crs)
        if CRS.from_user_input(tracts_gdf.crs) != CRS.from_user_input(project_crs)
        else tracts_gdf
    )

    buildings_utm: gpd.GeoDataFrame | None = None
    if buildings_file is not None and buildings_file.exists():
        bldgs_raw = gpd.read_file(buildings_file)
        if bldgs_raw.crs is None:
            raise ValueError(f"{buildings_file} is missing CRS.")
        buildings_utm = (
            bldgs_raw.to_crs(project_crs)
            if CRS.from_user_input(bldgs_raw.crs) != CRS.from_user_input(project_crs)
            else bldgs_raw
        )

    tract_df = compute_tract_building_exposure(
        tracts_gdf_utm=tracts_utm,
        buildings_gdf_utm=buildings_utm,
        modeled_depth_m=modeled_depth_m,
        valid_land_mask=valid_land_mask,
        transform=transform,
        depth_threshold_m=depth_threshold_m,
        nodata_value=nodata_value,
    )
    return compute_nfip_spearman_correlation(tract_df)


def validate_region(
    config_path: Path,
    scenario_id: str | None = None,
    sweep: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Downscale SFINCS water levels, score against Tier B/D references, and write JSON."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    project = cfg["project"]
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    sfincs_cfg = cfg["sfincs_model"]
    val_cfg = cfg["validation"]

    project_crs = str(project["crs"])
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    target_scenario = scenario_id or str(project["baseline_scenario"])

    hydro_dem_path = Path(tprep["hydro_dem_file"])
    if not hydro_dem_path.exists():
        raise FileNotFoundError(f"Hydro-enforced DEM not found: {hydro_dem_path}")
    with rasterio.open(hydro_dem_path) as dem_ds:
        assert_crs_equals(dem_ds.crs, project_crs, str(hydro_dem_path))
        dem_m = dem_ds.read(1).astype(np.float32)
        dst_transform = dem_ds.transform
        dem_nodata = float(dem_ds.nodata if dem_ds.nodata is not None else -9999.0)

    benchmark_specs = load_benchmark_specs(cfg)
    has_design_benchmark = len(benchmark_specs) > 0
    primary_benchmark: BenchmarkSpec | None = (
        benchmark_specs[0] if has_design_benchmark else None
    )
    if primary_benchmark is not None:
        if not primary_benchmark.vector_path.exists():
            raise FileNotFoundError(
                f"Validation reference vector not found: {primary_benchmark.vector_path}"
            )
        read_kwargs: dict[str, Any] = (
            {"layer": primary_benchmark.layer} if primary_benchmark.layer else {}
        )
        sfpuc_gdf = gpd.read_file(primary_benchmark.vector_path, **read_kwargs)
        if sfpuc_gdf.crs is None:
            raise ValueError(f"{primary_benchmark.vector_path} is missing CRS")
        sfpuc_gdf_utm = filter_benchmark_geodataframe(
            sfpuc_gdf.to_crs(project_crs), primary_benchmark.attribute_filter
        )
        obs_mask = rasterize_benchmark_polygons(
            gdf_utm=sfpuc_gdf_utm,
            out_shape=dem_m.shape,
            transform=dst_transform,
        )
    else:
        obs_mask = np.zeros(dem_m.shape, dtype=bool)

    min_land_elev_m = float(val_cfg.get("min_land_elevation_m", 0.5))
    depth_threshold_m = float(
        primary_benchmark.depth_threshold_m
        if primary_benchmark is not None
        else val_cfg.get("depth_threshold_m", 0.1524)
    )
    valid_dem = (dem_m != dem_nodata) & np.isfinite(dem_m)
    exclude_open_water = bool(val_cfg.get("exclude_open_water", True))
    nlcd_arr: np.ndarray | None = None
    nlcd_src_raw = domain.get("nlcd_landcover_source")
    if exclude_open_water and nlcd_src_raw and Path(nlcd_src_raw).exists():
        nlcd_arr = np.zeros(dem_m.shape, dtype=np.uint8)
        with rasterio.open(Path(nlcd_src_raw)) as nlcd_ds:
            reproject(
                source=nlcd_ds.read(1),
                destination=nlcd_arr,
                src_transform=nlcd_ds.transform,
                src_crs=nlcd_ds.crs,
                dst_transform=dst_transform,
                dst_crs=project_crs,
                resampling=Resampling.nearest,
            )
    land_valid_mask = compute_land_valid_mask(
        dem_m=dem_m,
        dem_nodata=dem_nodata,
        min_land_elevation_m=min_land_elev_m,
        nlcd_landcover=nlcd_arr,
        exclude_open_water=exclude_open_water,
    )

    bbox_utm = domain["bbox_epsg26910"]
    grid_res_m = float(domain["grid_resolution_m"])
    src_transform = from_origin(
        float(bbox_utm[0]), float(bbox_utm[3]), grid_res_m, grid_res_m
    )

    if sweep:
        drainage_rates = [float(x) for x in val_cfg["sweep_drainage_rates_mm_hr"]]
        manning_values = [float(x) for x in val_cfg["sweep_manning_n_paved"]]
        candidates = [(d, n) for d in drainage_rates for n in manning_values]
    else:
        candidates = [
            (float(sfincs_cfg["drainage_rate_mm_hr"]), float(tprep["manning_n_paved"]))
        ]

    best_score: tuple[float, float, float] | None = None
    best_result: tuple[float, float, np.ndarray, dict[str, float | int]] | None = None
    sweep_rows: list[tuple[float, float, dict[str, float | int]]] = []

    for drainage_mm_hr, manning_paved in candidates:
        if sweep:
            map_nc_path = _ensure_sweep_run(
                cfg=cfg,
                scenario_id=target_scenario,
                drainage_rate_mm_hr=drainage_mm_hr,
                manning_n_paved=manning_paved,
                dem_shape=dem_m.shape,
                dem_transform=dst_transform,
                dem_nodata=dem_nodata,
                valid_dem=valid_dem,
                force=force,
            )
        else:
            map_nc_path = (
                Path(cfg["paths"]["runs_dir"]) / target_scenario / "sfincs_map.nc"
            )

        depth_m, metrics = _evaluate_run(
            map_nc_path=map_nc_path,
            src_transform=src_transform,
            project_crs=project_crs,
            dem_m=dem_m,
            dst_transform=dst_transform,
            dem_nodata=dem_nodata,
            obs_mask=obs_mask,
            land_valid_mask=land_valid_mask,
            depth_threshold_m=depth_threshold_m,
        )
        sweep_rows.append((drainage_mm_hr, manning_paved, metrics))
        score_key = (
            float(metrics["csi"]),
            float(metrics["hit_rate"]),
            -float(metrics["false_alarm_ratio"]),
        )
        if best_score is None or score_key > best_score:
            best_score = score_key
            best_result = (drainage_mm_hr, manning_paved, depth_m, metrics)

    assert best_result is not None
    best_drainage, best_manning, best_depth_m, best_metrics = best_result

    print("[03_validate_sf] Validation results:")
    for d_rate, n_val, m in sweep_rows:
        marker = (
            " *"
            if (
                math.isclose(d_rate, best_drainage)
                and math.isclose(n_val, best_manning)
            )
            else ""
        )
        print(
            f"  drainage={d_rate:4.1f} mm/hr, n_paved={n_val:.3f} -> "
            f"HR={m['hit_rate']:.4f}, FAR={m['false_alarm_ratio']:.4f}, CSI={m['csi']:.4f}{marker}"
        )

    top_fp = find_top_false_positive_clusters(
        depth_m=best_depth_m,
        obs_mask=obs_mask,
        valid_mask=land_valid_mask,
        dst_transform=dst_transform,
        depth_threshold_m=depth_threshold_m,
        downsample_factor=10,
        top_n=8,
    )
    print(f"[03_validate_sf] Top {len(top_fp)} land false-positive clusters:")
    for cluster in top_fp:
        print(
            f"  area={cluster['area_m2']:7.0f} m2 @ UTM "
            f"({cluster['centroid_x_m']:.1f}, {cluster['centroid_y_m']:.1f})"
        )

    downscaled_path = Path(val_cfg["downscaled_depth_file"])
    _write_float32_geotiff(
        output_path=downscaled_path,
        data=best_depth_m,
        transform=dst_transform,
        crs=project_crs,
        nodata_value=dem_nodata,
    )

    summary = build_validation_summary(
        region=str(project["region_id"]),
        drainage_rate_mm_hr=best_drainage,
        manning_n_paved=best_manning,
        depth_threshold_m=depth_threshold_m,
        metrics=best_metrics,
    )
    summary_path = Path(val_cfg["summary_file"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"[03_validate_sf] Wrote downscaled depth -> {downscaled_path}")
    print(f"[03_validate_sf] Wrote summary JSON -> {summary_path}")

    ref_311_raw = domain.get("validation_311_reference")
    hindcast_scenario = val_cfg.get("hindcast_scenario")
    if ref_311_raw and hindcast_scenario:
        ref_311_path = Path(ref_311_raw)
        if not ref_311_path.exists():
            raise FileNotFoundError(
                f"Validation 311 point reference not found: {ref_311_path}"
            )
        hindcast_map_nc = (
            Path(cfg["paths"]["runs_dir"]) / str(hindcast_scenario) / "sfincs_map.nc"
        )
        ar_depth_m, _ = _evaluate_run(
            map_nc_path=hindcast_map_nc,
            src_transform=src_transform,
            project_crs=project_crs,
            dem_m=dem_m,
            dst_transform=dst_transform,
            dem_nodata=dem_nodata,
            obs_mask=obs_mask,
            land_valid_mask=land_valid_mask,
            depth_threshold_m=depth_threshold_m,
        )
        hindcast_depth_file = Path(
            val_cfg.get(
                "hindcast_depth_file",
                downscaled_path.parent / f"{hindcast_scenario}_depth_2m.tif",
            )
        )
        _write_float32_geotiff(
            output_path=hindcast_depth_file,
            data=ar_depth_m,
            transform=dst_transform,
            crs=project_crs,
            nodata_value=dem_nodata,
        )

        pts_311_gdf = gpd.read_file(ref_311_path)
        if pts_311_gdf.crs is None:
            raise ValueError(f"{ref_311_path} is missing CRS")
        pts_311_utm = pts_311_gdf.to_crs(project_crs)

        neighborhood_rows = compute_neighborhood_sfpuc_metrics(
            depth_m=best_depth_m,
            obs_mask=obs_mask,
            valid_land_mask=land_valid_mask,
            transform=dst_transform,
            depth_threshold_m=depth_threshold_m,
        )
        val_311_metrics = compute_311_validation_metrics(
            points_gdf_utm=pts_311_utm,
            ar_depth_m=ar_depth_m,
            sfpuc_mask=obs_mask,
            valid_land_mask=land_valid_mask,
            transform=dst_transform,
            nodata_value=dem_nodata,
        )

        metrics_3way_payload = {
            "sfpuc_summary": summary,
            "sfpuc_neighborhoods": neighborhood_rows,
            "top_false_positive_clusters": top_fp,
            "ar_311_validation": val_311_metrics,
        }
        metrics_3way_path = Path(
            val_cfg.get(
                "metrics_3way_file",
                summary_path.parent / "validation_3way_metrics.json",
            )
        )
        metrics_3way_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_3way_path.write_text(
            json.dumps(metrics_3way_payload, indent=2) + "\n", encoding="utf-8"
        )

        report_md = render_v02_validation_markdown(
            sfpuc_summary=summary,
            neighborhood_rows=neighborhood_rows,
            fp_clusters=top_fp,
            val_311_metrics=val_311_metrics,
        )
        report_path = Path(
            val_cfg.get(
                "report_markdown_file",
                f"docs/reports/{project['region_id']}_validation.md",
            )
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")

        b50 = next(
            (
                b
                for b in val_311_metrics["by_buffer"]
                if math.isclose(b["radius_m"], 50.0)
            ),
            val_311_metrics["by_buffer"][-1],
        )
        print(
            "[03_validate_sf] 311 3-way validation (50m): "
            f"AR>=0.05m={b50['thresholds']['0.05m']['capture_rate']:.1%} "
            f"(lift={b50['thresholds']['0.05m']['lift']}x), "
            f"SFPUC={b50['sfpuc']['capture_rate']:.1%} "
            f"(lift={b50['sfpuc']['lift']}x), AUC={b50['ar_roc_auc']:.4f}"
        )
        print(f"[03_validate_sf] Wrote hindcast depth -> {hindcast_depth_file}")
        print(f"[03_validate_sf] Wrote 3-way metrics -> {metrics_3way_path}")
        print(f"[03_validate_sf] Wrote validation report -> {report_path}")

    ensemble_cfg = list(val_cfg.get("hindcast_ensemble") or [])
    if ensemble_cfg:
        event_results: list[dict[str, Any]] = []
        for ev in ensemble_cfg:
            ev_scenario = str(ev["scenario"])
            ev_map_nc = Path(cfg["paths"]["runs_dir"]) / ev_scenario / "sfincs_map.nc"
            ev_311_path = Path(ev["validation_311_reference"])
            if not ev_map_nc.exists() or not ev_311_path.exists():
                print(
                    f"[03_validate_sf] Skipping ensemble event {ev['id']} "
                    f"(missing {ev_map_nc} or {ev_311_path})"
                )
                continue
            ev_depth_m, _ = _evaluate_run(
                map_nc_path=ev_map_nc,
                src_transform=src_transform,
                project_crs=project_crs,
                dem_m=dem_m,
                dst_transform=dst_transform,
                dem_nodata=dem_nodata,
                obs_mask=obs_mask,
                land_valid_mask=land_valid_mask,
                depth_threshold_m=depth_threshold_m,
            )
            ev_depth_file = Path(
                ev.get(
                    "depth_file",
                    summary_path.parent / f"{ev_scenario}_depth_2m.tif",
                )
            )
            _write_float32_geotiff(
                output_path=ev_depth_file,
                data=ev_depth_m,
                transform=dst_transform,
                crs=project_crs,
                nodata_value=dem_nodata,
            )
            ev_pts_gdf = gpd.read_file(ev_311_path)
            if ev_pts_gdf.crs is None:
                raise ValueError(f"{ev_311_path} is missing CRS")
            ev_metrics = compute_point_capture_metrics(
                points_gdf_utm=ev_pts_gdf.to_crs(project_crs),
                ar_depth_m=ev_depth_m,
                valid_land_mask=land_valid_mask,
                transform=dst_transform,
                benchmark_mask=obs_mask if has_design_benchmark else None,
                nodata_value=dem_nodata,
            )
            tiers_evaluated = ["311"]
            gauge_hwm_val: dict[str, Any] | None = None
            gauges_file_raw = ev.get("gauges_file")
            if gauges_file_raw:
                gauge_hwm_val = evaluate_event_gauges(
                    gauges_file=Path(gauges_file_raw),
                    map_nc_path=ev_map_nc,
                    tide_gauges_cfg=project.get("tide_gauges", ()),
                    zsmax_m=ev_depth_m + dem_m,
                    transform=dst_transform,
                    nodata_value=dem_nodata,
                )
                if gauge_hwm_val is not None:
                    tiers_evaluated.append("gauge_hwm")
            road_closures_val: dict[str, Any] | None = None
            road_closures_raw = ev.get("road_closures_file")
            if road_closures_raw:
                roads_raw = domain.get("roads_rail_source")
                road_closures_val = evaluate_event_road_closures(
                    road_closures_file=Path(road_closures_raw),
                    roads_file=Path(roads_raw) if roads_raw else None,
                    ar_depth_m=ev_depth_m,
                    valid_land_mask=land_valid_mask,
                    transform=dst_transform,
                    project_crs=project_crs,
                    nodata_value=dem_nodata,
                )
                if road_closures_val is not None:
                    tiers_evaluated.append("road_closures")
            event_results.append(
                {
                    "id": str(ev["id"]),
                    "label": str(ev.get("label", ev["id"])),
                    "scenario": ev_scenario,
                    "valid_land_points": ev_metrics["valid_land_points"],
                    "by_buffer": ev_metrics["by_buffer"],
                    "tiers_evaluated": tiers_evaluated,
                    "road_closures_validation": road_closures_val,
                    "gauge_hwm_validation": gauge_hwm_val,
                }
            )
        if event_results:
            design_benchmark_payload = (
                build_design_benchmark_summary(
                    source=primary_benchmark.source,
                    depth_threshold_m=depth_threshold_m,
                    metrics=best_metrics,
                )
                if primary_benchmark is not None
                else None
            )
            sources_cfg = val_cfg.get("sources") or {}
            groundsource_match: dict[str, Any] | None = None
            gs_file_raw = sources_cfg.get("groundsource_file")
            if gs_file_raw:
                subbasins_raw = sources_cfg.get("subbasins_file")
                groundsource_match = evaluate_groundsource_crosscheck(
                    groundsource_file=Path(gs_file_raw),
                    modeled_depth_m=best_depth_m,
                    valid_land_mask=land_valid_mask,
                    transform=dst_transform,
                    domain_bbox_utm=domain.get("bbox_epsg26910"),
                    subbasins_file=Path(subbasins_raw) if subbasins_raw else None,
                    project_crs=project_crs,
                    depth_threshold_m=0.05,
                    nodata_value=dem_nodata,
                    event_windows=ensemble_cfg,
                )
            nfip_match: dict[str, Any] | None = None
            nfip_file_raw = sources_cfg.get("nfip_claims_file")
            if nfip_file_raw:
                tracts_raw = sources_cfg.get("census_tracts_file")
                buildings_raw = domain.get("buildings_source")
                nfip_match = evaluate_nfip_crosscheck(
                    nfip_claims_file=Path(nfip_file_raw),
                    modeled_depth_m=best_depth_m,
                    valid_land_mask=land_valid_mask,
                    transform=dst_transform,
                    buildings_file=Path(buildings_raw) if buildings_raw else None,
                    tracts_file=Path(tracts_raw) if tracts_raw else None,
                    project_crs=project_crs,
                    depth_threshold_m=0.05,
                    nodata_value=dem_nodata,
                )
            regional_crosschecks = {
                "groundsource_subbasin_match": groundsource_match,
                "nfip_tract_spearman_rho": nfip_match,
            }
            ensemble_payload = aggregate_ensemble_311_metrics(
                event_results,
                region=str(project["region_id"]),
                drainage_tier=str(sfincs_cfg.get("drainage_tier", "tier0")),
                rain_source=str(sfincs_cfg.get("rain_source", "atlas14")),
                design_benchmark=design_benchmark_payload,
                regional_crosschecks=regional_crosschecks,
            )
            ensemble_path = Path(
                val_cfg.get(
                    "ensemble_metrics_file",
                    summary_path.parent / "validation_ensemble_metrics.json",
                )
            )
            ensemble_path.parent.mkdir(parents=True, exist_ok=True)
            ensemble_path.write_text(
                json.dumps(ensemble_payload, indent=2) + "\n", encoding="utf-8"
            )
            print(
                f"[03_validate_sf] Wrote {ensemble_payload['n_events']}-storm ensemble "
                f"metrics ({ensemble_payload['total_311_points']} 311 pts) -> {ensemble_path}"
            )

    return summary


validate_sf = validate_region


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate SF benchmark flood outputs.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/sf_benchmark.yml"),
        help="Path to scenario configuration YAML.",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Scenario ID to validate (defaults to project.baseline_scenario).",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run parameter sweep across sweep_drainage_rates_mm_hr x sweep_manning_n_paved.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run cached Docker sweep simulations.",
    )
    args = parser.parse_args()
    validate_sf(
        config_path=args.config,
        scenario_id=args.scenario,
        sweep=args.sweep,
        force=args.force,
    )


if __name__ == "__main__":
    main()
