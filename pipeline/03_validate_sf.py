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
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr
import yaml
from hydromt_sfincs import SfincsModel
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine, from_origin, rowcol
from rasterio.warp import reproject
from scipy import ndimage, stats
from shapely.geometry import box

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
    height, width = depth_m.shape
    rows: list[dict[str, Any]] = []
    for name, (xmin, ymin, xmax, ymax) in neighborhoods_utm.items():
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
                "neighborhood": name,
                "bbox_epsg26910": [xmin, ymin, xmax, ymax],
                "land_cells": int(np.sum(sub_valid)),
                "obs_cells": int(np.sum(sub_obs & sub_valid)),
                "mod_cells": int(np.sum((sub_depth >= depth_threshold_m) & sub_valid)),
                **m,
            }
        )
    return rows


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
    xs_311 = np.asarray(xs_list, dtype=np.float64)
    ys_311 = np.asarray(ys_list, dtype=np.float64)
    n_valid_pts = xs_311.size

    land_rows, land_cols = np.where(valid_land_mask)
    if land_rows.size == 0 or n_valid_pts == 0:
        raise ValueError(
            "Cannot compute 311 validation without valid land cells and points."
        )

    rng = np.random.default_rng(seed)
    n_ctrl_eff = min(int(n_controls), int(land_rows.size))
    ctrl_picks = rng.choice(land_rows.size, size=n_ctrl_eff, replace=False)
    xs_ctrl = (
        transform.c + (land_cols[ctrl_picks].astype(np.float64) + 0.5) * transform.a
    )
    ys_ctrl = (
        transform.f + (land_rows[ctrl_picks].astype(np.float64) + 0.5) * transform.e
    )

    sorted_radii = tuple(sorted(float(r) for r in radii_m))
    ar_311 = sample_max_depth_within_buffers(
        ar_depth_m, transform, nodata_value, xs_311, ys_311, sorted_radii
    )
    ar_ctrl = sample_max_depth_within_buffers(
        ar_depth_m, transform, nodata_value, xs_ctrl, ys_ctrl, sorted_radii
    )
    sfpuc_311 = sample_mask_within_buffers(
        sfpuc_mask, transform, xs_311, ys_311, sorted_radii
    )
    sfpuc_ctrl = sample_mask_within_buffers(
        sfpuc_mask, transform, xs_ctrl, ys_ctrl, sorted_radii
    )

    by_buffer: list[dict[str, Any]] = []
    for r_m in sorted_radii:
        entry: dict[str, Any] = {
            "radius_m": r_m,
            "ar_roc_auc": compute_roc_auc(ar_311[r_m], ar_ctrl[r_m]),
            "thresholds": {},
        }
        for thr in thresholds_m:
            thr_key = f"{thr:g}m"
            hits_311 = ar_311[r_m] >= float(thr)
            hits_ctrl = ar_ctrl[r_m] >= float(thr)
            rate_311 = float(np.mean(hits_311))
            rate_ctrl = float(np.mean(hits_ctrl))
            lift = round(rate_311 / rate_ctrl, 3) if rate_ctrl > 0.0 else None
            entry["thresholds"][thr_key] = {
                "hit_count": int(np.sum(hits_311)),
                "capture_rate": round(rate_311, 4),
                "control_rate": round(rate_ctrl, 4),
                "lift": lift,
            }

        s_hits_311 = sfpuc_311[r_m]
        s_hits_ctrl = sfpuc_ctrl[r_m]
        s_rate_311 = float(np.mean(s_hits_311))
        s_rate_ctrl = float(np.mean(s_hits_ctrl))
        entry["sfpuc"] = {
            "hit_count": int(np.sum(s_hits_311)),
            "capture_rate": round(s_rate_311, 4),
            "control_rate": round(s_rate_ctrl, 4),
            "lift": round(s_rate_311 / s_rate_ctrl, 3) if s_rate_ctrl > 0.0 else None,
        }
        ar05_hits = ar_311[r_m] >= 0.05
        entry["combined_ar05_or_sfpuc_rate"] = round(
            float(np.mean(ar05_hits | s_hits_311)), 4
        )
        entry["ar05_only_not_sfpuc_count"] = int(np.sum(ar05_hits & (~s_hits_311)))
        entry["sfpuc_only_not_ar05_count"] = int(np.sum(s_hits_311 & (~ar05_hits)))
        by_buffer.append(entry)

    eval_r = 50.0 if 50.0 in sorted_radii else max(sorted_radii)
    r25 = 25.0 if 25.0 in sorted_radii else eval_r
    ar_eval = ar_311[eval_r]
    ar_25 = ar_311[r25]
    s_eval = sfpuc_311[eval_r]

    subtype_rows: list[dict[str, Any]] = []
    if "service_subtype" in filtered.columns:
        subtypes = filtered["service_subtype"].fillna("unknown").astype(str)
        for st_val, idxs in subtypes.groupby(subtypes).groups.items():
            idx_arr = np.asarray(list(idxs), dtype=int)
            row_dict = {"service_subtype": st_val}
            row_dict.update(_summarize_point_group(idx_arr, ar_eval, s_eval, ar_25))
            subtype_rows.append(row_dict)
        subtype_rows.sort(key=lambda r: r["count"], reverse=True)

    neighborhood_rows: list[dict[str, Any]] = []
    if "neighborhood" in filtered.columns:
        nbhds = filtered["neighborhood"].fillna("Unknown").astype(str)
        for nb_val, idxs in nbhds.groupby(nbhds).groups.items():
            idx_arr = np.asarray(list(idxs), dtype=int)
            row_dict = {"neighborhood": nb_val}
            row_dict.update(_summarize_point_group(idx_arr, ar_eval, s_eval, ar_25))
            neighborhood_rows.append(row_dict)
        neighborhood_rows.sort(key=lambda r: r["count"], reverse=True)
        neighborhood_rows = neighborhood_rows[:12]

    missed_mask = ar_eval < 0.05
    missed_clusters: list[dict[str, Any]] = []
    if np.any(missed_mask):
        missed_df = filtered.loc[missed_mask].copy()
        nb_series = (
            missed_df["neighborhood"].fillna("Unknown").astype(str)
            if "neighborhood" in missed_df.columns
            else np.repeat("Unknown", len(missed_df))
        )
        missed_df["_nb"] = nb_series
        total_by_nb = (
            filtered["neighborhood"]
            .fillna("Unknown")
            .astype(str)
            .value_counts()
            .to_dict()
            if "neighborhood" in filtered.columns
            else {}
        )
        missed_sfpuc = s_eval[missed_mask]
        missed_df["_sfpuc_hit"] = missed_sfpuc
        for nb_name, grp in missed_df.groupby("_nb"):
            addrs = (
                [
                    str(a)
                    for a in grp["address"].dropna().astype(str).unique()[:3]
                    if str(a).strip()
                ]
                if "address" in grp.columns
                else []
            )
            missed_clusters.append(
                {
                    "neighborhood": str(nb_name),
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


def aggregate_ensemble_311_metrics(
    event_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-storm 311 validation metrics across a multi-storm AR ensemble."""
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

    return {
        "n_events": len(event_results),
        "total_311_points": sum(int(ev["valid_land_points"]) for ev in event_results),
        "events": [
            {
                "id": ev["id"],
                "label": ev.get("label", ev["id"]),
                "scenario": ev["scenario"],
                "valid_land_points": int(ev["valid_land_points"]),
                "by_buffer": list(ev["by_buffer"]),
            }
            for ev in event_results
        ],
        "ensemble_mean": {"by_buffer": ensemble_mean_by_buffer},
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


def validate_sf(
    config_path: Path,
    scenario_id: str | None = None,
    sweep: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Downscale SFINCS water levels, score against SFPUC polygons, and write summary JSON."""
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

    sfpuc_path = Path(domain["validation_reference"])
    if not sfpuc_path.exists():
        raise FileNotFoundError(f"Validation reference vector not found: {sfpuc_path}")
    sfpuc_gdf = gpd.read_file(sfpuc_path)
    if sfpuc_gdf.crs is None:
        raise ValueError(f"{sfpuc_path} is missing CRS")
    sfpuc_utm = sfpuc_gdf.to_crs(project_crs)
    obs_shapes = [
        (geom, 1)
        for geom in sfpuc_utm.geometry
        if geom is not None and not geom.is_empty
    ]
    obs_mask = (
        rasterize(
            obs_shapes,
            out_shape=dem_m.shape,
            transform=dst_transform,
            fill=0,
            dtype=np.uint8,
        ).astype(bool)
        if obs_shapes
        else np.zeros(dem_m.shape, dtype=bool)
    )

    min_land_elev_m = float(val_cfg.get("min_land_elevation_m", 0.5))
    depth_threshold_m = float(val_cfg.get("depth_threshold_m", 0.1524))
    valid_dem = (dem_m != dem_nodata) & np.isfinite(dem_m)
    land_valid_mask = valid_dem & (dem_m >= min_land_elev_m)
    nlcd_src_raw = domain.get("nlcd_landcover_source")
    if (
        bool(val_cfg.get("exclude_open_water", True))
        and nlcd_src_raw
        and Path(nlcd_src_raw).exists()
    ):
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
        land_valid_mask = land_valid_mask & (nlcd_arr != 11)

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
            ev_metrics = compute_311_validation_metrics(
                points_gdf_utm=ev_pts_gdf.to_crs(project_crs),
                ar_depth_m=ev_depth_m,
                sfpuc_mask=obs_mask,
                valid_land_mask=land_valid_mask,
                transform=dst_transform,
                nodata_value=dem_nodata,
            )
            event_results.append(
                {
                    "id": str(ev["id"]),
                    "label": str(ev.get("label", ev["id"])),
                    "scenario": ev_scenario,
                    "valid_land_points": ev_metrics["valid_land_points"],
                    "by_buffer": ev_metrics["by_buffer"],
                }
            )
        if event_results:
            ensemble_payload = aggregate_ensemble_311_metrics(event_results)
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
