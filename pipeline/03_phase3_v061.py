"""Phase 3 sensitivity analyses for SF Bay Urban Flood Map v0.6.1.

1. Item 19: Tier 0 drainage-rate sensitivity sweep (2.5, 5.0, 10.0, 15.0, 20.0 mm/hr)
   across 100-yr 0 ft SLR and the 3 calibration ARs (Oct 2021, Dec 2022, Jan 2023).
2. Item 21: NOAA Atlas 14 Vol. 6 24-hr temporal distribution sensitivity (10-yr and 100-yr
   0 ft SLR at 5.0 mm/hr) vs. the alternating-block baseline.
3. Saves metrics JSON, markdown report, and multi-panel sweep figure.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import urllib.request
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import rasterio  # must precede geopandas/pyogrio

# isort: split
import geopandas as gpd
import numpy as np
import xarray as xr
import yaml
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from shapely.geometry import Point

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(script_name: str, mod_name: str) -> Any:
    path = REPO_ROOT / "pipeline" / script_name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_atlas14_all_cases_median(csv_text: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse the 'ALL CASES' 50% (median) cumulative curve from NOAA Atlas 14 temporal CSV."""
    lines = csv_text.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if "ALL CASES" in line.upper() and "CUMULATIVE" in line.upper():
            start_idx = idx
            break
    if start_idx is None:
        raise ValueError("Could not find 'ALL CASES' cumulative table in Atlas 14 CSV.")

    col_50_idx: int | None = None
    hours: list[float] = []
    pcts: list[float] = []
    for line in lines[start_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            if hours:
                break
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if col_50_idx is None:
            if parts[0].lower().startswith("hours"):
                for j, hdr in enumerate(parts):
                    if hdr == "50%":
                        col_50_idx = j
                        break
            continue
        try:
            hr = float(parts[0])
            val = float(parts[col_50_idx])
        except (ValueError, IndexError):
            if hours:
                break
            continue
        hours.append(hr)
        pcts.append(val)

    if len(hours) < 2:
        raise ValueError("Parsed fewer than 2 rows from Atlas 14 ALL CASES table.")
    return np.asarray(hours, dtype=np.float64), np.asarray(pcts, dtype=np.float64)


def build_atlas14_hyetograph_rows(
    hours: np.ndarray,
    cum_pct: np.ndarray,
    total_depth_mm: float,
) -> list[dict[str, float]]:
    """Convert cumulative percentage curve into incremental SFINCS hyetograph rows."""
    if len(hours) != len(cum_pct) or len(hours) < 2:
        raise ValueError("hours and cum_pct must have identical length >= 2.")
    rows: list[dict[str, float]] = []
    for i in range(len(hours) - 1):
        dt_hr = float(hours[i + 1] - hours[i])
        if dt_hr <= 0.0:
            raise ValueError(f"Non-positive time step at index {i}: {dt_hr}")
        dpct = max(0.0, float(cum_pct[i + 1] - cum_pct[i])) / 100.0
        inc_depth_mm = dpct * float(total_depth_mm)
        intensity_mm_hr = inc_depth_mm / dt_hr
        rows.append(
            {
                "time_min": round(float(hours[i]) * 60.0, 2),
                "depth_mm": round(inc_depth_mm, 6),
                "intensity_mm_hr": round(intensity_mm_hr, 6),
            }
        )
    return rows


def _write_hyetograph_csv(rows: list[dict[str, float]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["time_min", "depth_mm", "intensity_mm_hr"]
        )
        writer.writeheader()
        writer.writerows(rows)


def _downscale_nc_to_2m(
    nc_path: Path,
    dem_m: np.ndarray,
    dst_transform: Any,
    dst_crs: str,
    dem_nodata: float,
    val_mod: Any,
) -> np.ndarray:
    with xr.open_dataset(nc_path) as ds:
        with np.errstate(all="ignore"):
            zsmax = np.nanmax(ds["zsmax"].to_numpy(), axis=0).astype(np.float32)
        xs = ds["x"].to_numpy()
        ys = ds["y"].to_numpy()
        if xs.ndim == 2:
            dx = abs(float(xs[0, 1] - xs[0, 0]))
            dy = abs(float(ys[1, 0] - ys[0, 0]))
        else:
            dx = abs(float(xs[1] - xs[0]))
            dy = abs(float(ys[1] - ys[0]))
        west = float(np.nanmin(xs)) - dx / 2.0
        north = float(np.nanmax(ys)) + dy / 2.0
        src_transform = from_origin(west, north, dx, dy)
    return val_mod.downscale_zsmax_to_depth_m(
        zsmax_south_up=zsmax,
        src_transform=src_transform,
        src_crs="EPSG:26910",
        dem_m=dem_m,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dem_nodata=dem_nodata,
    )


def _fetch_sf_atlas14_region(
    cache_zip: Path, lon: float = -122.415, lat: float = 37.745
) -> int:
    """Download Vol 6 Temporal Shapefile and identify the California region number for SF."""
    if not cache_zip.exists():
        url = "https://hdsc.nws.noaa.gov/pfds/shapefiles/Vol6_Temporals_Shapefiles.zip"
        req = urllib.request.Request(
            url, headers={"User-Agent": "bay-urban-flood-map/0.6.1"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            cache_zip.write_bytes(resp.read())

    gdf = gpd.read_file(f"zip://{cache_zip}")
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    pt = Point(lon, lat)
    hits = gdf[gdf.geometry.contains(pt)]
    if hits.empty:
        hits = gdf[gdf.geometry.intersects(pt)]
    if hits.empty:
        raise RuntimeError(
            f"Point ({lon}, {lat}) did not fall in any Vol 6 temporal polygon."
        )
    row = hits.iloc[0]
    for col in gdf.columns:
        if col == "geometry":
            continue
        val = str(row[col]).strip()
        digits = "".join(ch for ch in val if ch.isdigit())
        if digits and (
            "reg" in col.lower()
            or "id" in col.lower()
            or "num" in col.lower()
            or "area" in col.lower()
        ):
            return int(digits)
    # Fallback: first numeric column
    for col in gdf.columns:
        if col == "geometry":
            continue
        try:
            return int(float(row[col]))
        except (ValueError, TypeError):
            continue
    raise RuntimeError(f"Could not extract region ID from columns {list(gdf.columns)}")


def _plot_drainage_sweep(sweep_rows: list[dict[str, Any]], out_png: Path) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    rates = [r["rate_mm_hr"] for r in sweep_rows]

    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.4), dpi=160)
    fig.patch.set_facecolor("white")

    # Panel A: Tier B SFPUC CSI (Raw & Filtered) + FAR
    ax = axes[0]
    ax.plot(
        rates,
        [r["tier_b"]["raw"]["csi"] for r in sweep_rows],
        "o--",
        color="#64748b",
        label="Raw CSI (>=0.1524 m)",
    )
    ax.plot(
        rates,
        [r["tier_b"]["filtered"]["csi"] for r in sweep_rows],
        "o-",
        color="#0f766e",
        lw=2.2,
        label="Deep & Contiguous CSI",
    )
    ax.plot(
        rates,
        [r["tier_b"]["filtered"]["false_alarm_ratio"] for r in sweep_rows],
        "s:",
        color="#b45309",
        label="Filtered FAR",
    )
    ax.axvspan(
        12.0, 18.0, color="#e2e8f0", alpha=0.55, label="UK EA 2013-2025 (12-18 mm/hr)"
    )
    ax.axvline(
        10.84, color="#dc2626", ls="--", lw=1.3, label="SFPUC 5-yr 3-hr LOS (10.84)"
    )
    ax.axvline(5.0, color="#1d4ed8", ls=":", lw=1.2, label="v0.6 Frozen (5.0)")
    ax.set_title(
        "A. Tier B SFPUC Benchmark (100-Yr 0 ft)", fontsize=10, fontweight="bold"
    )
    ax.set_xlabel("Tier 0 Drainage Rate (mm/hr)")
    ax.set_ylabel("Score")
    ax.set_xticks(rates)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7.5, loc="best")

    # Panel B: Tier D 311 (3-AR Cal Pooled, 25 m RoW)
    ax = axes[1]
    ax.plot(
        rates,
        [r["tier_d_3ar_pooled_row"]["25m"]["capture_rate"] for r in sweep_rows],
        "o-",
        color="#2563eb",
        lw=2.0,
        label="25 m Capture Rate",
    )
    ax.plot(
        rates,
        [r["tier_d_3ar_pooled_row"]["25m"]["ar_roc_auc_weighted"] for r in sweep_rows],
        "s-",
        color="#7c3aed",
        lw=2.0,
        label="25 m ROC-AUC (RoW)",
    )
    ax.plot(
        rates,
        [r["tier_d_3ar_pooled_row"]["0m"]["ar_roc_auc_weighted"] for r in sweep_rows],
        "^--",
        color="#94a3b8",
        label="0 m ROC-AUC (RoW)",
    )
    ax.axvline(10.84, color="#dc2626", ls="--", lw=1.1)
    ax.axvline(5.0, color="#1d4ed8", ls=":", lw=1.2)
    ax2 = ax.twinx()
    ax2.plot(
        rates,
        [r["tier_d_3ar_pooled_row"]["25m"]["lift"] for r in sweep_rows],
        "d:",
        color="#059669",
        lw=1.6,
        label="25 m Lift (right)",
    )
    ax2.set_ylabel("Lift (x)", color="#059669")
    ax.set_title("B. Tier D 311 (3-AR Cal Pooled, RoW)", fontsize=10, fontweight="bold")
    ax.set_xlabel("Tier 0 Drainage Rate (mm/hr)")
    ax.set_ylabel("Capture / ROC-AUC")
    ax.set_xticks(rates)
    ax.grid(True, alpha=0.3)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=7.5, loc="best")

    # Panel C: Tier C (3-AR Cal Pooled, 25 m RoW) & Tier E NFIP
    ax = axes[2]
    ax.plot(
        rates,
        [r["tier_c_3ar_pooled_row"]["25m"]["capture_rate"] for r in sweep_rows],
        "o-",
        color="#0284c7",
        lw=2.0,
        label="Tier C 25 m Capture",
    )
    ax.plot(
        rates,
        [r["tier_c_3ar_pooled_row"]["25m"]["ar_roc_auc_weighted"] for r in sweep_rows],
        "s-",
        color="#4f46e5",
        lw=2.0,
        label="Tier C 25 m AUC (RoW)",
    )
    ax.plot(
        rates,
        [r["tier_e_nfip"]["area_kendall_tau_b"] for r in sweep_rows],
        "^-",
        color="#d97706",
        lw=1.8,
        label="Tier E NFIP Kendall tau-b",
    )
    ax.axvline(10.84, color="#dc2626", ls="--", lw=1.1)
    ax.axvline(5.0, color="#1d4ed8", ls=":", lw=1.2)
    ax.set_title("C. Tier C Dispatches & Tier E NFIP", fontsize=10, fontweight="bold")
    ax.set_xlabel("Tier 0 Drainage Rate (mm/hr)")
    ax.set_ylabel("Score")
    ax.set_xticks(rates)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7.5, loc="best")

    fig.tight_layout()
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def run_phase3() -> dict[str, Any]:
    build_mod = _load_module("02_build_sfincs.py", "build02")
    val_mod = _load_module("03_validate_sf.py", "val03")

    cfg_path = REPO_ROOT / "configs" / "sf_citywide.yml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    proj_crs = cfg["project"]["crs"]
    dem_nodata = float(cfg["terrain_prep"]["nodata_value"])
    min_land_m = float(cfg["validation"]["min_land_elevation_m"])

    with rasterio.open(REPO_ROOT / cfg["terrain_prep"]["hydro_dem_file"]) as src:
        dem_m = src.read(1).astype(np.float32)
        transform = src.transform
        dem_shape = dem_m.shape
        cell_area_m2 = abs(float(transform.a) * float(transform.e))

    nlcd_reproj = np.zeros(dem_shape, dtype=np.int16)
    with rasterio.open(REPO_ROOT / cfg["domain"]["nlcd_landcover_source"]) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=nlcd_reproj,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=proj_crs,
            resampling=Resampling.nearest,
        )

    valid_land_mask = val_mod.compute_land_valid_mask(
        dem_m=dem_m,
        dem_nodata=dem_nodata,
        min_land_elevation_m=min_land_m,
        nlcd_landcover=nlcd_reproj,
        exclude_open_water=True,
    )

    sfpuc_raw = gpd.read_file(REPO_ROOT / cfg["domain"]["validation_reference"])
    if sfpuc_raw.crs is None:
        sfpuc_raw = sfpuc_raw.set_crs("EPSG:4326")
    sfpuc_mask = val_mod.rasterize_benchmark_polygons(
        sfpuc_raw.to_crs(proj_crs), dem_shape, transform
    )

    roads_raw = gpd.read_file(REPO_ROOT / cfg["domain"]["roads_rail_source"])
    roads_utm = (
        roads_raw.to_crs(proj_crs)
        if CRS.from_user_input(roads_raw.crs) != CRS.from_user_input(proj_crs)
        else roads_raw
    )
    row_utm = val_mod.filter_right_of_way_lines(roads_utm)
    xs_row_ctrl, ys_row_ctrl = val_mod.sample_random_road_controls(
        roads_gdf_utm=row_utm,
        valid_land_mask=valid_land_mask,
        transform=transform,
        n_controls=2000,
        seed=42,
    )

    # ------------------------------------------------------------------
    # Item 19: Drainage-Rate Sweep (2.5, 5.0, 10.0, 15.0, 20.0 mm/hr)
    # ------------------------------------------------------------------
    cal_ids = ("ar_20211024", "ar_20221231", "ar_20230109")
    cal_events = [
        ev for ev in cfg["validation"]["hindcast_ensemble"] if ev["id"] in cal_ids
    ]
    sweep_scenarios = ["sf_citywide_100yr_slr0ft"] + [
        ev["scenario"] for ev in cal_events
    ]
    rates = [2.5, 5.0, 10.0, 15.0, 20.0]
    radii = (0.0, 25.0, 50.0)

    sweep_results: list[dict[str, Any]] = []
    for rate in rates:
        if abs(rate - 5.0) < 1e-6:
            runs_dir = REPO_ROOT / cfg["paths"]["runs_dir"]
        else:
            tag = str(rate).replace(".", "p")
            runs_dir = REPO_ROOT / "data" / "runs_sweep_v061" / f"d{tag}"
            for sc_id in sweep_scenarios:
                build_mod.build_sfincs(
                    config_path=cfg_path,
                    scenario_id=sc_id,
                    run_all=False,
                    drainage_rate_mm_hr=rate,
                    drainage_tier="tier0",
                    amc_mode="amc2",
                    runs_dir=runs_dir,
                )

        # Score Tier B & Tier E on 100-yr 0 ft SLR
        nc_100 = runs_dir / "sf_citywide_100yr_slr0ft" / "sfincs_map.nc"
        d100 = _downscale_nc_to_2m(
            nc_100, dem_m, transform, proj_crs, dem_nodata, val_mod
        )
        tb_raw = val_mod.compute_flood_metrics(
            d100, sfpuc_mask, valid_land_mask, 0.1524
        )
        tb_filt_mask = val_mod.filter_deep_and_contiguous_mask(
            d100, valid_land_mask, cell_area_m2, 0.1524, 5574.0
        )
        tb_filt = val_mod.compute_flood_metrics_from_mask(
            tb_filt_mask, sfpuc_mask, valid_land_mask
        )

        nfip = val_mod.evaluate_nfip_crosscheck(
            nfip_claims_file=REPO_ROOT
            / cfg["validation"]["sources"]["nfip_claims_file"],
            modeled_depth_m=d100,
            valid_land_mask=valid_land_mask,
            transform=transform,
            buildings_file=REPO_ROOT / cfg["domain"]["buildings_source"],
            project_crs=proj_crs,
            depth_threshold_m=0.05,
            nodata_value=dem_nodata,
        )
        nfip_area = nfip["additional_correlations"][
            "claim_count_vs_flooded_land_area_m2"
        ]

        # Score Tier D & Tier C on 3 calibration storms
        d_events: list[dict[str, Any]] = []
        c_events: list[dict[str, Any]] = []
        for ev in cal_events:
            nc_ev = runs_dir / ev["scenario"] / "sfincs_map.nc"
            ar_depth = _downscale_nc_to_2m(
                nc_ev, dem_m, transform, proj_crs, dem_nodata, val_mod
            )
            pts_311 = val_mod._load_vector_or_csv_points(
                REPO_ROOT / ev["validation_311_reference"], proj_crs
            )
            m_d = val_mod.compute_point_capture_metrics(
                pts_311,
                ar_depth,
                valid_land_mask,
                transform,
                benchmark_mask=sfpuc_mask,
                nodata_value=dem_nodata,
                radii_m=radii,
                thresholds_m=(0.05, 0.1524),
                control_coords=(xs_row_ctrl, ys_row_ctrl),
            )
            d_events.append(
                {
                    "id": ev["id"],
                    "label": ev["label"],
                    "scenario": ev["scenario"],
                    **m_d,
                }
            )

            pts_c = val_mod._load_vector_or_csv_points(
                REPO_ROOT / ev["road_closures_file"], proj_crs
            )
            snapped_c, _ = val_mod.snap_points_to_roads(
                pts_c, roads_utm, max_snap_distance_m=50.0
            )
            m_c = val_mod.compute_point_capture_metrics(
                snapped_c,
                ar_depth,
                valid_land_mask,
                transform,
                benchmark_mask=None,
                nodata_value=dem_nodata,
                radii_m=radii,
                thresholds_m=(0.05, 0.1524),
                subtype_col="category",
                group_col="corridor",
                control_coords=(xs_row_ctrl, ys_row_ctrl),
            )
            c_events.append(
                {
                    "id": ev["id"],
                    "label": ev["label"],
                    "scenario": ev["scenario"],
                    **m_c,
                }
            )

        pooled_d = val_mod.compute_pooled_ensemble_point_metrics(
            d_events, radii_m=radii
        )
        pooled_c = val_mod.compute_pooled_ensemble_point_metrics(
            c_events, radii_m=radii
        )
        unw_d = val_mod.aggregate_ensemble_311_metrics(d_events)["ensemble_mean"]

        def _extract_buf(pooled_dict: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for b in pooled_dict["by_buffer"]:
                key = f"{int(b['radius_m'])}m"
                t = b["thresholds"]["0.05m"]
                out[key] = {
                    "capture_rate": t["capture_rate"],
                    "control_rate": t["control_rate"],
                    "lift": t["lift"],
                    "ar_roc_auc_weighted": b["ar_roc_auc_weighted"],
                }
            return out

        sweep_results.append(
            {
                "rate_mm_hr": rate,
                "tier_b": {"raw": tb_raw, "filtered": tb_filt},
                "tier_d_3ar_pooled_row": _extract_buf(pooled_d),
                "tier_d_3ar_unweighted_row_25m": next(
                    b
                    for b in unw_d["by_buffer"]
                    if abs(float(b["radius_m"]) - 25.0) < 1e-6
                ),
                "tier_c_3ar_pooled_row": _extract_buf(pooled_c),
                "tier_e_nfip": {
                    "bldg_spearman_rho": nfip["spearman_rho"],
                    "area_spearman_rho": nfip_area["spearman_rho"],
                    "area_kendall_tau_b": nfip_area["kendall_tau_b"],
                    "area_kendall_p": nfip_area["kendall_p_value"],
                    "binary_auc": nfip["binary_presence_mwu"]["auc"],
                },
            }
        )

    # ------------------------------------------------------------------
    # Item 21: Atlas 14 Vol. 6 Temporal Distribution Sensitivity
    # ------------------------------------------------------------------
    atlas_dir = REPO_ROOT / "data" / "runs_sweep_v061" / "atlas14_td"
    atlas_dir.mkdir(parents=True, exist_ok=True)
    zip_path = REPO_ROOT / "data" / "raw" / "sf_citywide" / "atlas14_vol6_temporals.zip"
    region_num = _fetch_sf_atlas14_region(zip_path, lon=-122.415, lat=37.745)
    td_csv_path = (
        REPO_ROOT / "data" / "raw" / "sf_citywide" / f"ca_{region_num}_24h_temporal.csv"
    )
    if not td_csv_path.exists():
        td_url = f"https://hdsc.nws.noaa.gov/pub/hdsc/data/sw/ca_{region_num}_24h_temporal.csv"
        req = urllib.request.Request(
            td_url, headers={"User-Agent": "bay-urban-flood-map/0.6.1"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            td_csv_path.write_bytes(resp.read())

    hours, cum_pct = parse_atlas14_all_cases_median(
        td_csv_path.read_text(encoding="utf-8")
    )
    td_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    td_peak_intensities: dict[str, float] = {}
    for storm_key, total_mm in [("rain_10yr", 90.4), ("rain_100yr", 144.0)]:
        rows = build_atlas14_hyetograph_rows(hours, cum_pct, total_mm)
        h_path = atlas_dir / f"sf_hyetograph_{storm_key}_atlas14_td.csv"
        _write_hyetograph_csv(rows, h_path)
        td_cfg["rain_events"][storm_key]["hyetograph_file"] = str(h_path)
        td_cfg["rain_events"][storm_key]["hyetograph_method"] = (
            f"atlas14_vol6_ca{region_num}_all_cases_50pct"
        )
        td_peak_intensities[storm_key] = max(r["intensity_mm_hr"] for r in rows)

    td_cfg_path = atlas_dir / "config_atlas14_td.yml"
    td_cfg_path.write_text(yaml.safe_dump(td_cfg, sort_keys=False), encoding="utf-8")

    ab_runs_dir = REPO_ROOT / cfg["paths"]["runs_dir"]
    atlas14_comparison: dict[str, Any] = {
        "california_region": region_num,
        "temporal_distribution": f"CA Region {region_num} 24-hr ALL CASES 50% (median)",
        "scenarios": {},
    }

    for sc_id, storm_key in [
        ("sf_citywide_10yr_slr0ft", "rain_10yr"),
        ("sf_citywide_100yr_slr0ft", "rain_100yr"),
    ]:
        build_mod.build_sfincs(
            config_path=td_cfg_path,
            scenario_id=sc_id,
            run_all=False,
            drainage_rate_mm_hr=5.0,
            drainage_tier="tier0",
            amc_mode="amc2",
            runs_dir=atlas_dir,
        )
        d_ab = _downscale_nc_to_2m(
            ab_runs_dir / sc_id / "sfincs_map.nc",
            dem_m,
            transform,
            proj_crs,
            dem_nodata,
            val_mod,
        )
        d_td = _downscale_nc_to_2m(
            atlas_dir / sc_id / "sfincs_map.nc",
            dem_m,
            transform,
            proj_crs,
            dem_nodata,
            val_mod,
        )

        def _summary_stats(depth: np.ndarray) -> dict[str, Any]:
            arr = depth[valid_land_mask & np.isfinite(depth)]
            km2_05 = float(np.sum(arr >= 0.05)) * cell_area_m2 / 1e6
            km2_15 = float(np.sum(arr >= 0.1524)) * cell_area_m2 / 1e6
            wet = arr[arr >= 0.05]
            return {
                "flooded_km2_ge_05m": round(km2_05, 3),
                "flooded_km2_ge_1524m": round(km2_15, 3),
                "mean_wet_depth_m": round(float(np.mean(wet)), 4) if wet.size else 0.0,
                "p90_wet_depth_m": round(float(np.percentile(wet, 90)), 4)
                if wet.size
                else 0.0,
            }

        ab_hyet_path = REPO_ROOT / cfg["rain_events"][storm_key]["hyetograph_file"]
        with ab_hyet_path.open(encoding="utf-8") as f:
            ab_peak = max(float(r["intensity_mm_hr"]) for r in csv.DictReader(f))

        sc_entry: dict[str, Any] = {
            "alternating_block": {
                "peak_intensity_mm_hr": round(ab_peak, 2),
                **_summary_stats(d_ab),
            },
            "atlas14_td": {
                "peak_intensity_mm_hr": round(td_peak_intensities[storm_key], 2),
                **_summary_stats(d_td),
            },
        }
        if sc_id == "sf_citywide_100yr_slr0ft":
            sc_entry["alternating_block"]["tier_b_raw"] = val_mod.compute_flood_metrics(
                d_ab, sfpuc_mask, valid_land_mask, 0.1524
            )
            ab_f = val_mod.filter_deep_and_contiguous_mask(
                d_ab, valid_land_mask, cell_area_m2, 0.1524, 5574.0
            )
            sc_entry["alternating_block"]["tier_b_filtered"] = (
                val_mod.compute_flood_metrics_from_mask(
                    ab_f, sfpuc_mask, valid_land_mask
                )
            )

            sc_entry["atlas14_td"]["tier_b_raw"] = val_mod.compute_flood_metrics(
                d_td, sfpuc_mask, valid_land_mask, 0.1524
            )
            td_f = val_mod.filter_deep_and_contiguous_mask(
                d_td, valid_land_mask, cell_area_m2, 0.1524, 5574.0
            )
            sc_entry["atlas14_td"]["tier_b_filtered"] = (
                val_mod.compute_flood_metrics_from_mask(
                    td_f, sfpuc_mask, valid_land_mask
                )
            )

        atlas14_comparison["scenarios"][sc_id] = sc_entry

    # ------------------------------------------------------------------
    # Save outputs & plot
    # ------------------------------------------------------------------
    fig_path = REPO_ROOT / "docs" / "reports" / "figures" / "sf_v061_drainage_sweep.png"
    _plot_drainage_sweep(sweep_results, fig_path)

    payload = {
        "version": "0.6.1",
        "sfpuc_los_design_storm": {
            "return_period_yr": 5,
            "duration_hr": 3,
            "total_depth_in": 1.28,
            "total_depth_mm": 32.51,
            "peak_1hr_depth_in": 0.72,
            "peak_1hr_depth_mm": 18.29,
            "implied_mean_drainage_mm_hr": round(1.28 * 25.4 / 3.0, 2),
            "source": "San Francisco Public Works (2017), FASIP Report Sec. 3.1 & Table 3 (1941 City IDF)",
        },
        "drainage_sweep": sweep_results,
        "atlas14_temporal_sensitivity": atlas14_comparison,
        "tier_a_geometry_note": {
            "in_domain_gauges": ["9414290 (Presidio)"],
            "out_of_domain_gauges": [
                "9414750 (Alameda)",
                "9414863 (Richmond)",
                "9414523 (Redwood City)",
            ],
            "status": "Retained as boundary-condition QA for SF-only domain; leave-one-gauge-out deferred to multi-gauge v0.7+/v1.0 domain.",
        },
    }
    out_json = (
        REPO_ROOT / "data" / "processed" / "sf_citywide" / "sf_v061_phase3_metrics.json"
    )
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_phase3_report(
        payload, REPO_ROOT / "docs" / "reports" / "sf_v061_phase3_report.md"
    )
    return payload


def _write_phase3_report(payload: dict[str, Any], out_md: Path) -> None:
    los = payload["sfpuc_los_design_storm"]
    lines = [
        "# SF Bay Urban Flood Map v0.6.1 — Phase 3 Sensitivity Report",
        "",
        "## 1. Drainage-Rate Sensitivity Sweep (Item 19)",
        "",
        f"SFPUC's Sewer System Improvement Program (SSIP) Level of Service (LOS) design storm is the **{los['return_period_yr']}-year, {los['duration_hr']}-hour storm ({los['total_depth_in']} in / {los['total_depth_mm']} mm)**, implying a mean drainage rate of **{los['implied_mean_drainage_mm_hr']} mm/hr** (comparable to England's Environment Agency 12–18 mm/hr urban default).",
        "",
        "![Drainage-rate sensitivity sweep](figures/sf_v061_drainage_sweep.png)",
        "",
        "| Rate (mm/hr) | Tier B Raw CSI | Tier B Filtered CSI (FAR) | Tier D 3-AR Pooled 25 m RoW (Cap / Lift / AUC) | Tier C 3-AR Pooled 25 m RoW (Cap / AUC) | Tier E NFIP Area $\\tau_b$ |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in payload["drainage_sweep"]:
        tb_r = r["tier_b"]["raw"]
        tb_f = r["tier_b"]["filtered"]
        td = r["tier_d_3ar_pooled_row"]["25m"]
        tc = r["tier_c_3ar_pooled_row"]["25m"]
        te = r["tier_e_nfip"]
        lines.append(
            f"| {r['rate_mm_hr']:.1f} | {tb_r['csi']:.4f} | {tb_f['csi']:.4f} ({tb_f['false_alarm_ratio']:.4f}) | "
            f"{td['capture_rate'] * 100:.2f}% / {td['lift']:.3f}x / {td['ar_roc_auc_weighted']:.4f} | "
            f"{tc['capture_rate'] * 100:.2f}% / {tc['ar_roc_auc_weighted']:.4f} | {te['area_kendall_tau_b']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## 2. Atlas 14 Vol. 6 Temporal Distribution Sensitivity (Item 21)",
            "",
            f"Temporal distribution: **{payload['atlas14_temporal_sensitivity']['temporal_distribution']}** vs. alternating block (at 5.0 mm/hr Tier 0).",
            "",
            "| Scenario | Method | Peak Intensity (mm/hr) | Flooded $\\ge 0.05$ m (km²) | Flooded $\\ge 0.1524$ m (km²) | Tier B Raw / Filtered CSI |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for sc_id, sc in payload["atlas14_temporal_sensitivity"]["scenarios"].items():
        for m_key, m_lbl in [
            ("alternating_block", "Alternating Block"),
            ("atlas14_td", "Atlas 14 TD (50%)"),
        ]:
            d = sc[m_key]
            csi_str = (
                f"{d['tier_b_raw']['csi']:.4f} / {d['tier_b_filtered']['csi']:.4f}"
                if "tier_b_raw" in d
                else "n/a"
            )
            lines.append(
                f"| {sc_id} | {m_lbl} | {d['peak_intensity_mm_hr']:.2f} | "
                f"{d['flooded_km2_ge_05m']:.3f} | {d['flooded_km2_ge_1524m']:.3f} | {csi_str} |"
            )

    lines.extend(
        [
            "",
            "## 3. Tier A Leave-One-Gauge-Out Geometry Check (Item 20)",
            "",
            f"- **In-domain gauges:** {', '.join(payload['tier_a_geometry_note']['in_domain_gauges'])}",
            f"- **Out-of-domain gauges:** {', '.join(payload['tier_a_geometry_note']['out_of_domain_gauges'])}",
            f"- **Status:** {payload['tier_a_geometry_note']['status']}",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run_phase3()
