"""Reproducible Phase 2 rescoring of frozen v0.6 SF outputs for v0.6.1 revision.

No new SFINCS simulations are run. Reads existing 2 m depth rasters and
sfincs_map.nc outputs, computes all Phase 2 diagnostic and rescoring metrics,
and writes:
  - data/processed/sf_citywide/sf_v061_rescore_metrics.json
  - docs/reports/sf_v061_rescore_report.md
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import rasterio  # must precede geopandas/pyogrio

# isort: split
import geopandas as gpd
import numpy as np
import xarray as xr
import yaml
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT / "pipeline"))
import importlib.util


def _load_val_mod():
    spec = importlib.util.spec_from_file_location(
        "val03", REPO_ROOT / "pipeline" / "03_validate_sf.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fetch_acs_tract_income(cache_path: Path) -> dict[str, float]:
    """Fetch ACS 5-yr B19013 median household income for SF tracts (06075)."""
    if not cache_path.exists():
        url = "https://api.censusreporter.org/1.0/data/show/latest?table_ids=B19013&geo_ids=140|05000US06075"
        req = urllib.request.Request(
            url, headers={"User-Agent": "bay-urban-flood-map/0.6.1"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            cache_path.write_bytes(resp.read())
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for geoid, tables in payload.get("data", {}).items():
        # geoid format: 14000US06075010101 -> 06075010101
        tract_id = geoid.replace("14000US", "")
        est = tables.get("B19013", {}).get("estimate", {}).get("B19013001")
        if est is not None and float(est) > 0:
            out[tract_id] = float(est)
    return out


def _downscale_nc_to_2m(
    nc_path: Path,
    dem_m: np.ndarray,
    dst_transform: Any,
    dst_crs: str,
    dem_nodata: float,
    val_mod: Any,
) -> np.ndarray:
    """Downscale existing sfincs_map.nc zsmax onto the 2 m hydro DEM in memory."""
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


def _load_or_download_sfpuc_elevations(
    cache_path: Path, project_crs: str
) -> gpd.GeoDataFrame:
    """Load or download SFPUC Dec 2024 Flood Elevations (wppz-u2hi) in project_crs."""
    if not cache_path.exists():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        urls = [
            "https://data.sf.gov/api/geospatial/wppz-u2hi?method=export&format=GeoJSON",
            "https://data.sf.gov/resource/wppz-u2hi.geojson?$limit=5000",
        ]
        last_exc: Exception | None = None
        for url in urls:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "bay-urban-flood-map/0.6.1"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = resp.read()
                cache_path.write_bytes(payload)
                last_exc = None
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
        if last_exc is not None:
            raise RuntimeError(
                f"Failed to download SFPUC Flood Elevations (wppz-u2hi): {last_exc}"
            )

    gdf = gpd.read_file(cache_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf.to_crs(project_crs)


def run_rescore() -> dict[str, Any]:
    val_mod = _load_val_mod()
    cfg = yaml.safe_load(
        (REPO_ROOT / "configs" / "sf_citywide.yml").read_text(encoding="utf-8")
    )
    proj_crs = cfg["project"]["crs"]
    dem_nodata = float(cfg["terrain_prep"]["nodata_value"])
    min_land_m = float(cfg["validation"]["min_land_elevation_m"])

    dem_path = REPO_ROOT / cfg["terrain_prep"]["hydro_dem_file"]
    with rasterio.open(dem_path) as src:
        dem_m = src.read(1).astype(np.float32)
        transform = src.transform
        dem_shape = dem_m.shape
        cell_area_m2 = abs(float(transform.a) * float(transform.e))

    nlcd_path = REPO_ROOT / cfg["domain"]["nlcd_landcover_source"]
    nlcd_reproj = np.zeros(dem_shape, dtype=np.int16)
    with rasterio.open(nlcd_path) as src:
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

    # ------------------------------------------------------------------
    # 1. Tier B: SFPUC Benchmark (Raw vs Deep & Contiguous + SLR 0/1/3/5 ft)
    # ------------------------------------------------------------------
    sfpuc_raw = gpd.read_file(REPO_ROOT / cfg["domain"]["validation_reference"])
    if sfpuc_raw.crs is None:
        sfpuc_raw = sfpuc_raw.set_crs("EPSG:4326")
    sfpuc_utm = sfpuc_raw.to_crs(proj_crs)
    sfpuc_mask = val_mod.rasterize_benchmark_polygons(sfpuc_utm, dem_shape, transform)

    # Half an average SF city block: ~275 ft x 436 ft = 120,000 sq ft -> half = 60,000 sq ft = 5,574.18 m2
    half_block_m2 = 5574.0
    sensitivity_areas = [4500.0, 5574.0, 6500.0]

    tier_b_slr: dict[str, Any] = {}
    depth_0ft_m: np.ndarray | None = None
    for slr_ft, nc_rel in [
        ("0ft", "data/processed/sf_citywide/sf_citywide_100yr_slr0ft_depth_2m.tif"),
        ("1ft", "data/runs/sf_citywide_100yr_slr1ft/sfincs_map.nc"),
        ("3ft", "data/runs/sf_citywide_100yr_slr3ft/sfincs_map.nc"),
        ("5ft", "data/runs/sf_citywide_100yr_slr5ft/sfincs_map.nc"),
    ]:
        p = REPO_ROOT / nc_rel
        if p.suffix == ".tif":
            with rasterio.open(p) as src:
                depth_m = src.read(1).astype(np.float32)
        else:
            depth_m = _downscale_nc_to_2m(
                p, dem_m, transform, proj_crs, dem_nodata, val_mod
            )
        if slr_ft == "0ft":
            depth_0ft_m = depth_m

        raw_m = val_mod.compute_flood_metrics(
            depth_m, sfpuc_mask, valid_land_mask, 0.1524
        )
        filt_mask = val_mod.filter_deep_and_contiguous_mask(
            depth_m, valid_land_mask, cell_area_m2, 0.1524, half_block_m2
        )
        filt_m = val_mod.compute_flood_metrics_from_mask(
            filt_mask, sfpuc_mask, valid_land_mask
        )
        diag = val_mod.compute_benchmark_depth_diagnostics(
            depth_m, sfpuc_mask, valid_land_mask, dem_nodata
        )

        entry: dict[str, Any] = {
            "raw": raw_m,
            "deep_and_contiguous_5574m2": filt_m,
            "depth_diagnostics": diag,
        }
        if slr_ft == "0ft":
            sens = {}
            for area_m2 in sensitivity_areas:
                sm = val_mod.filter_deep_and_contiguous_mask(
                    depth_m, valid_land_mask, cell_area_m2, 0.1524, area_m2
                )
                sens[f"{int(area_m2)}m2"] = val_mod.compute_flood_metrics_from_mask(
                    sm, sfpuc_mask, valid_land_mask
                )
            entry["area_sensitivity"] = sens
        tier_b_slr[slr_ft] = entry

    assert depth_0ft_m is not None
    elev_cache = (
        REPO_ROOT
        / "data"
        / "raw"
        / "sf_citywide"
        / "sfpuc_flood_elevations_2024.geojson"
    )
    elev_utm = _load_or_download_sfpuc_elevations(elev_cache, proj_crs)
    cmin = elev_utm["contourmin"].astype(float)
    cmax = elev_utm["contourmax"].astype(float)
    # wppz-u2hi polygons are 1-ft contour bands where contourmax is the flood
    # elevation rounded UP to the nearest foot (SFCD). Subtracting 0.5 ft
    # (0.1524 m, equal to the band midpoint) applies an approximate mean
    # rounding correction.
    elev_utm["wse_navd88_upper_m"] = (
        cmax + val_mod.SFCD_TO_NAVD88_FT
    ) * val_mod.FT_TO_M
    elev_utm["wse_navd88_mid_m"] = (
        0.5 * (cmin + cmax) + val_mod.SFCD_TO_NAVD88_FT
    ) * val_mod.FT_TO_M

    def _rasterize_wse(col_name: str) -> np.ndarray:
        shapes = [
            (geom, float(val))
            for geom, val in zip(elev_utm.geometry, elev_utm[col_name], strict=True)
            if geom is not None and not geom.is_empty and np.isfinite(val)
        ]
        return rasterize(
            shapes=shapes,
            out_shape=dem_shape,
            transform=transform,
            fill=np.nan,
            dtype="float32",
        )

    sfpuc_wse_upper = _rasterize_wse("wse_navd88_upper_m")
    sfpuc_wse_mid = _rasterize_wse("wse_navd88_mid_m")
    elev_metrics_upper = val_mod.compute_sfpuc_elevation_metrics(
        model_depth_m=depth_0ft_m,
        dem_m=dem_m,
        sfpuc_wse_navd88_m=sfpuc_wse_upper,
        valid_land_mask=valid_land_mask,
        min_model_depth_m=0.05,
        nodata_value=dem_nodata,
    )
    elev_metrics_mid = val_mod.compute_sfpuc_elevation_metrics(
        model_depth_m=depth_0ft_m,
        dem_m=dem_m,
        sfpuc_wse_navd88_m=sfpuc_wse_mid,
        valid_land_mask=valid_land_mask,
        min_model_depth_m=0.05,
        nodata_value=dem_nodata,
    )
    sfpuc_elev_check = {
        "status": "available",
        "dataset_id": "wppz-u2hi",
        "n_polygons": len(elev_utm),
        "bias_sign_convention": "model minus SFPUC benchmark",
        "datum_conversion": "NAVD88 (ft) = SFCD (ft) + 11.35 (SF Public Works CCSF-VD13 nominal)",
        "raw_rounded_up_contourmax": elev_metrics_upper,
        "approx_rounding_corrected_midpoint": elev_metrics_mid,
        **elev_metrics_mid,
    }

    # ------------------------------------------------------------------
    # 2. Groundsource (Item 14): Specificity & Confusion Matrix
    # ------------------------------------------------------------------
    with rasterio.open(REPO_ROOT / cfg["validation"]["downscaled_depth_file"]) as src:
        d100 = src.read(1).astype(np.float32)
    gs_res = val_mod.evaluate_groundsource_crosscheck(
        groundsource_file=REPO_ROOT / cfg["validation"]["sources"]["groundsource_file"],
        modeled_depth_m=d100,
        valid_land_mask=valid_land_mask,
        transform=transform,
        domain_bbox_utm=cfg["domain"]["bbox_epsg26910"],
        project_crs=proj_crs,
        depth_threshold_m=0.05,
        nodata_value=dem_nodata,
        event_windows=cfg["validation"]["hindcast_ensemble"],
    )

    # ------------------------------------------------------------------
    # 3. Tiers C & D: Right-of-Way vs Valid-Land Controls, Pooled & Income
    # ------------------------------------------------------------------
    roads_raw = gpd.read_file(REPO_ROOT / cfg["domain"]["roads_rail_source"])
    roads_utm = (
        roads_raw.to_crs(proj_crs)
        if CRS.from_user_input(roads_raw.crs) != CRS.from_user_input(proj_crs)
        else roads_raw
    )
    row_utm = val_mod.filter_right_of_way_lines(roads_utm)

    xs_land_ctrl, ys_land_ctrl = val_mod.sample_random_land_controls(
        valid_land_mask=valid_land_mask, transform=transform, n_controls=2000, seed=42
    )
    xs_row_ctrl, ys_row_ctrl = val_mod.sample_random_road_controls(
        roads_gdf_utm=row_utm,
        valid_land_mask=valid_land_mask,
        transform=transform,
        n_controls=2000,
        seed=42,
    )

    # Load census tracts + ACS median income
    nfip_gdf = gpd.read_file(
        REPO_ROOT / cfg["validation"]["sources"]["nfip_claims_file"]
    )
    tracts_utm = (
        nfip_gdf.to_crs(proj_crs)
        if CRS.from_user_input(nfip_gdf.crs) != CRS.from_user_input(proj_crs)
        else nfip_gdf
    )
    income_map = _fetch_acs_tract_income(
        REPO_ROOT / "data" / "raw" / "sf_citywide" / "sf_acs_tract_income.json"
    )
    tracts_utm["median_income_usd"] = (
        tracts_utm["census_tract"].astype(str).map(income_map)
    )

    radii = (0.0, 25.0, 50.0)
    tier_d_land_events: list[dict[str, Any]] = []
    tier_d_row_events: list[dict[str, Any]] = []
    tier_c_land_events: list[dict[str, Any]] = []
    tier_c_row_events: list[dict[str, Any]] = []
    tier_d_row_samples: list[dict[str, Any]] = []

    for ev in cfg["validation"]["hindcast_ensemble"]:
        with rasterio.open(REPO_ROOT / ev["depth_file"]) as src:
            ar_depth = src.read(1).astype(np.float32)

        # Tier D (311)
        pts_311 = val_mod._load_vector_or_csv_points(
            REPO_ROOT / ev["validation_311_reference"], proj_crs
        )
        m_d_land = val_mod.compute_point_capture_metrics(
            pts_311,
            ar_depth,
            valid_land_mask,
            transform,
            benchmark_mask=sfpuc_mask,
            nodata_value=dem_nodata,
            radii_m=radii,
            thresholds_m=(0.05, 0.1524),
            control_coords=(xs_land_ctrl, ys_land_ctrl),
        )
        m_d_row = val_mod.compute_point_capture_metrics(
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
        tier_d_land_events.append(
            {
                "id": ev["id"],
                "label": ev["label"],
                "scenario": ev["scenario"],
                **m_d_land,
            }
        )
        tier_d_row_events.append(
            {
                "id": ev["id"],
                "label": ev["label"],
                "scenario": ev["scenario"],
                **m_d_row,
            }
        )

        # Save raw point/control samples for income stratification
        _, xs_p, ys_p = val_mod.extract_valid_land_points(
            pts_311, valid_land_mask, transform
        )
        ar_p = val_mod.sample_max_depth_within_buffers(
            ar_depth, transform, dem_nodata, xs_p, ys_p, radii
        )
        ar_c = val_mod.sample_max_depth_within_buffers(
            ar_depth, transform, dem_nodata, xs_row_ctrl, ys_row_ctrl, radii
        )
        tier_d_row_samples.append(
            {
                "id": ev["id"],
                "xs_pts": xs_p,
                "ys_pts": ys_p,
                "xs_ctrl": xs_row_ctrl,
                "ys_ctrl": ys_row_ctrl,
                "ar_pts": ar_p,
                "ar_ctrl": ar_c,
            }
        )

        # Tier C (road closures / dispatches, snapped to roads)
        pts_c = val_mod._load_vector_or_csv_points(
            REPO_ROOT / ev["road_closures_file"], proj_crs
        )
        snapped_c, _ = val_mod.snap_points_to_roads(
            pts_c, roads_utm, max_snap_distance_m=50.0
        )
        m_c_land = val_mod.compute_point_capture_metrics(
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
            control_coords=(xs_land_ctrl, ys_land_ctrl),
        )
        m_c_row = val_mod.compute_point_capture_metrics(
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
        tier_c_land_events.append(
            {
                "id": ev["id"],
                "label": ev["label"],
                "scenario": ev["scenario"],
                **m_c_land,
            }
        )
        tier_c_row_events.append(
            {
                "id": ev["id"],
                "label": ev["label"],
                "scenario": ev["scenario"],
                **m_c_row,
            }
        )

    agg_d_land = val_mod.aggregate_ensemble_311_metrics(tier_d_land_events)
    agg_d_row = val_mod.aggregate_ensemble_311_metrics(tier_d_row_events)
    pooled_d_land = val_mod.compute_pooled_ensemble_point_metrics(
        tier_d_land_events, radii_m=radii
    )
    pooled_d_row = val_mod.compute_pooled_ensemble_point_metrics(
        tier_d_row_events, radii_m=radii
    )

    agg_c_land = val_mod.aggregate_ensemble_311_metrics(tier_c_land_events)
    agg_c_row = val_mod.aggregate_ensemble_311_metrics(tier_c_row_events)
    pooled_c_land = val_mod.compute_pooled_ensemble_point_metrics(
        tier_c_land_events, radii_m=radii
    )
    pooled_c_row = val_mod.compute_pooled_ensemble_point_metrics(
        tier_c_row_events, radii_m=radii
    )

    income_strat = val_mod.compute_income_stratified_point_metrics(
        tier_d_row_samples,
        tracts_utm,
        income_col="median_income_usd",
        n_quantiles=3,
        radii_m=radii,
    )

    # ------------------------------------------------------------------
    # 4. Tier E: NFIP Zero-Claim Share & Kendall tau-b
    # ------------------------------------------------------------------
    nfip_res = val_mod.evaluate_nfip_crosscheck(
        nfip_claims_file=REPO_ROOT / cfg["validation"]["sources"]["nfip_claims_file"],
        modeled_depth_m=d100,
        valid_land_mask=valid_land_mask,
        transform=transform,
        buildings_file=REPO_ROOT / cfg["domain"]["buildings_source"],
        project_crs=proj_crs,
        depth_threshold_m=0.05,
        nodata_value=dem_nodata,
    )

    results = {
        "version": "0.6.1",
        "tier_b_sfpuc": {
            "half_block_area_m2": half_block_m2,
            "half_block_source": "Half of ~120,000 sq ft (~275 x 436 ft) average SF city block = 60,000 sq ft (5,574 m2)",
            "sfpuc_disclaimer_summary": "Updated December 2024: models stormwater runoff only (deep and contiguous >= 6 in over >= half an average City block); excludes Bay/Pacific coastal inundation.",
            "slr_comparison": tier_b_slr,
            "flood_elevations_2024": sfpuc_elev_check,
        },
        "groundsource": gs_res,
        "tier_d_311": {
            "n_events": len(tier_d_row_events),
            "valid_land_controls": {
                "events": tier_d_land_events,
                "unweighted_mean": agg_d_land["ensemble_mean"],
                "pooled": pooled_d_land,
            },
            "right_of_way_controls": {
                "n_row_features": len(row_utm),
                "events": tier_d_row_events,
                "unweighted_mean": agg_d_row["ensemble_mean"],
                "pooled": pooled_d_row,
            },
            "income_terciles_row_controls": income_strat,
        },
        "tier_c_dispatches": {
            "n_events": len(tier_c_row_events),
            "valid_land_controls": {
                "events": tier_c_land_events,
                "unweighted_mean": agg_c_land["ensemble_mean"],
                "pooled": pooled_c_land,
            },
            "right_of_way_controls": {
                "events": tier_c_row_events,
                "unweighted_mean": agg_c_row["ensemble_mean"],
                "pooled": pooled_c_row,
            },
        },
        "tier_e_nfip": nfip_res,
    }

    out_json = (
        REPO_ROOT
        / "data"
        / "processed"
        / "sf_citywide"
        / "sf_v061_rescore_metrics.json"
    )
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    _write_markdown_report(
        results, REPO_ROOT / "docs" / "reports" / "sf_v061_rescore_report.md"
    )
    return results


def _fmt_buf(
    buf_list: list[dict[str, Any]], r_m: float, auc_key: str = "ar_roc_auc"
) -> str:
    b = next(x for x in buf_list if abs(float(x["radius_m"]) - r_m) < 1e-6)
    t = b["thresholds"]["0.05m"] if "thresholds" in b else b
    cap = f"{t['capture_rate'] * 100:.2f}%"
    ctrl = f"{t['control_rate'] * 100:.2f}%"
    lift = f"{t['lift']:.3f}x" if t["lift"] is not None else "n/a"
    auc = f"{b[auc_key]:.4f}" if b.get(auc_key) is not None else "n/a"
    return f"{cap} / {ctrl} / {lift} / {auc}"


def _write_markdown_report(res: dict[str, Any], out_md: Path) -> None:
    lines: list[str] = [
        "# SF Bay Urban Flood Map v0.6.1 — Phase 2 Rescore Report",
        "",
        "Generated from frozen v0.6 outputs (`pipeline/03_rescore_v061.py`, no new SFINCS runs).",
        "",
        "## 1. Groundsource (Item 14)",
        "",
    ]
    gs = res["groundsource"]
    cm = gs["confusion_matrix"]
    lines.extend(
        [
            f"- **Sub-basins:** {gs['n_subbasins']} | **Observed flooded:** {gs['n_gs_present']} | **Modeled flooded:** {gs['n_model_present']}",
            f"- **Confusion Matrix:** TP={cm['tp']}, FP={cm['fp']}, FN={cm['fn']}, TN={cm['tn']}",
            f"- **Presence Hit Rate:** {gs['presence_hit_rate']:.4f} | **Specificity:** {gs['specificity']:.4f} | **Accuracy:** {gs['presence_accuracy']:.4f} (base rate = 37/41 = 0.9024)",
            "- **Conclusion:** The model flags all 41 sub-basins as flooded, showing zero discrimination on negative sub-basins.",
            "",
            "## 2. Tier B — SFPUC 100-Yr Benchmark (Item 15)",
            "",
            f"Deep-and-contiguous filter: `>= 0.1524 m` (6 in) over connected area `>= {res['tier_b_sfpuc']['half_block_area_m2']:.0f} m^2` ({res['tier_b_sfpuc']['half_block_source']}).",
            "",
            "| SLR Scenario | Raw HR / FAR / CSI | Filtered (5,574 m²) HR / FAR / CSI | Median Depth Inside vs Outside |",
            "| --- | --- | --- | --- |",
        ]
    )
    for slr in ["0ft", "1ft", "3ft", "5ft"]:
        s = res["tier_b_sfpuc"]["slr_comparison"][slr]
        r, f, d = s["raw"], s["deep_and_contiguous_5574m2"], s["depth_diagnostics"]
        lines.append(
            f"| +{slr} | {r['hit_rate']:.4f} / {r['false_alarm_ratio']:.4f} / {r['csi']:.4f} | "
            f"{f['hit_rate']:.4f} / {f['false_alarm_ratio']:.4f} / {f['csi']:.4f} | "
            f"{d['inside_benchmark']['median_m']:.3f} m vs {d['outside_benchmark']['median_m']:.3f} m |"
        )

    fe = res["tier_b_sfpuc"]["flood_elevations_2024"]
    az_raw = fe["raw_rounded_up_contourmax"]["all_zone_cells"]
    mw_raw = fe["raw_rounded_up_contourmax"]["mutually_wet_cells"]
    az_mid = fe["approx_rounding_corrected_midpoint"]["all_zone_cells"]
    mw_mid = fe["approx_rounding_corrected_midpoint"]["mutually_wet_cells"]
    lines.extend(
        [
            "",
            f"### SFPUC 100-Year Storm Flood Elevations (`{fe['dataset_id']}`, {fe['n_polygons']} polygons)",
            "",
            f"- Datum conversion: {fe['datum_conversion']} | Bias sign convention: {fe['bias_sign_convention']}",
            f"- **Raw rounded-up (`contourmax`, biased high by 0–0.3048 m) — all zone cells ({az_raw['n_cells']:,}):** SFPUC implied depth mean {az_raw['sfpuc_depth_mean_m']:.4f} m vs. modeled mean {az_raw['model_depth_mean_m']:.4f} m; depth RMSE = {az_raw['depth_rmse_m']:.4f} m, MAE = {az_raw['depth_mae_m']:.4f} m, bias = {az_raw['depth_bias_m']:+.4f} m, Spearman rho = {az_raw['depth_spearman_rho']:.4f}",
            f"- **Raw rounded-up (`contourmax`) — mutually wet cells >= 0.05 m ({mw_raw['n_cells']:,}):** depth Pearson r = {mw_raw['depth_pearson_r']:.4f}, Spearman rho = {mw_raw['depth_spearman_rho']:.4f}, RMSE = {mw_raw['depth_rmse_m']:.4f} m, MAE = {mw_raw['depth_mae_m']:.4f} m, bias = {mw_raw['depth_bias_m']:+.4f} m",
            f"- **Approx. rounding-corrected (`contourmax - 0.1524 m` / band midpoint) — all zone cells ({az_mid['n_cells']:,}):** SFPUC implied depth mean {az_mid['sfpuc_depth_mean_m']:.4f} m vs. modeled mean {az_mid['model_depth_mean_m']:.4f} m; depth RMSE = {az_mid['depth_rmse_m']:.4f} m, MAE = {az_mid['depth_mae_m']:.4f} m, bias = {az_mid['depth_bias_m']:+.4f} m, Spearman rho = {az_mid['depth_spearman_rho']:.4f}",
            f"- **Approx. rounding-corrected — mutually wet cells >= 0.05 m ({mw_mid['n_cells']:,}):** depth Pearson r = {mw_mid['depth_pearson_r']:.4f}, Spearman rho = {mw_mid['depth_spearman_rho']:.4f}, RMSE = {mw_mid['depth_rmse_m']:.4f} m, MAE = {mw_mid['depth_mae_m']:.4f} m, bias = {mw_mid['depth_bias_m']:+.4f} m",
        ]
    )

    lines.extend(
        [
            "",
            "## 3. Tier D (311 Reports) — Valid-Land vs Right-of-Way Controls (Item 16)",
            "",
            "Format: `Cap / Ctrl Rate / Lift / AUC`",
            "",
            "| Event (Pts) | 0 m (Valid Land) | 0 m (Right-of-Way) | 25 m (Valid Land) | 25 m (Right-of-Way) | 50 m (Right-of-Way) |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    d_land = res["tier_d_311"]["valid_land_controls"]
    d_row = res["tier_d_311"]["right_of_way_controls"]
    for ev_l, ev_r in zip(d_land["events"], d_row["events"], strict=True):
        lines.append(
            f"| {ev_l['label']} ({ev_l['valid_land_points']}) | "
            f"{_fmt_buf(ev_l['by_buffer'], 0.0)} | {_fmt_buf(ev_r['by_buffer'], 0.0)} | "
            f"{_fmt_buf(ev_l['by_buffer'], 25.0)} | {_fmt_buf(ev_r['by_buffer'], 25.0)} | "
            f"{_fmt_buf(ev_r['by_buffer'], 50.0)} |"
        )
    lines.append(
        f"| **5-AR Unweighted Mean** | "
        f"{_fmt_buf(d_land['unweighted_mean']['by_buffer'], 0.0)} | {_fmt_buf(d_row['unweighted_mean']['by_buffer'], 0.0)} | "
        f"{_fmt_buf(d_land['unweighted_mean']['by_buffer'], 25.0)} | {_fmt_buf(d_row['unweighted_mean']['by_buffer'], 25.0)} | "
        f"{_fmt_buf(d_row['unweighted_mean']['by_buffer'], 50.0)} |"
    )
    lines.append(
        f"| **5-AR Pooled (2,071 pts)** | "
        f"{_fmt_buf(d_land['pooled']['by_buffer'], 0.0, 'ar_roc_auc_weighted')} | {_fmt_buf(d_row['pooled']['by_buffer'], 0.0, 'ar_roc_auc_weighted')} | "
        f"{_fmt_buf(d_land['pooled']['by_buffer'], 25.0, 'ar_roc_auc_weighted')} | {_fmt_buf(d_row['pooled']['by_buffer'], 25.0, 'ar_roc_auc_weighted')} | "
        f"{_fmt_buf(d_row['pooled']['by_buffer'], 50.0, 'ar_roc_auc_weighted')} |"
    )

    lines.extend(
        [
            "",
            "### Tier D Income Terciles (Right-of-Way Controls, Pooled 5-AR)",
            "",
            "| Income Tercile (Tracts) | Income Range (USD) | 311 Pts | 0 m Cap / Ctrl / Lift / AUC | 25 m Cap / Ctrl / Lift / AUC |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for st in res["tier_d_311"]["income_terciles_row_controls"]["strata"]:
        b0 = _fmt_buf(st["by_buffer"], 0.0)
        b25 = _fmt_buf(st["by_buffer"], 25.0)
        lines.append(
            rf"| {st['stratum']} ({st['n_tracts']}) | \${st['min_income_usd']:,.0f}–\${st['max_income_usd']:,.0f} | "
            f"{st['n_points']} | {b0} | {b25} |"
        )

    lines.extend(
        [
            "",
            "## 4. Tier C (Emergency Dispatches) — Valid-Land vs Right-of-Way Controls",
            "",
            "| Summary (310 pts) | 0 m | 25 m | 50 m |",
            "| --- | --- | --- | --- |",
        ]
    )
    c_land = res["tier_c_dispatches"]["valid_land_controls"]
    c_row = res["tier_c_dispatches"]["right_of_way_controls"]
    lines.append(
        f"| Unweighted Mean (Valid Land) | {_fmt_buf(c_land['unweighted_mean']['by_buffer'], 0.0)} | "
        f"{_fmt_buf(c_land['unweighted_mean']['by_buffer'], 25.0)} | {_fmt_buf(c_land['unweighted_mean']['by_buffer'], 50.0)} |"
    )
    lines.append(
        f"| Unweighted Mean (Right-of-Way) | {_fmt_buf(c_row['unweighted_mean']['by_buffer'], 0.0)} | "
        f"{_fmt_buf(c_row['unweighted_mean']['by_buffer'], 25.0)} | {_fmt_buf(c_row['unweighted_mean']['by_buffer'], 50.0)} |"
    )
    lines.append(
        f"| Pooled (Right-of-Way) | {_fmt_buf(c_row['pooled']['by_buffer'], 0.0, 'ar_roc_auc_weighted')} | "
        f"{_fmt_buf(c_row['pooled']['by_buffer'], 25.0, 'ar_roc_auc_weighted')} | {_fmt_buf(c_row['pooled']['by_buffer'], 50.0, 'ar_roc_auc_weighted')} |"
    )

    nfip = res["tier_e_nfip"]
    area_corr = nfip["additional_correlations"]["claim_count_vs_flooded_land_area_m2"]
    lines.extend(
        [
            "",
            "## 5. Tier E — NFIP Claims (Item 17)",
            "",
            f"- **Tracts:** {nfip['n_tracts']} | **Zero-claim tracts:** {nfip['n_zero_claim_tracts']} ({nfip['zero_claim_tract_fraction'] * 100:.2f}%)",
            f"- **Exposed buildings vs claims:** Spearman rho = {nfip['spearman_rho']:.4f} (p = {nfip['p_value']:.4f}); Kendall tau-b = {nfip['kendall_tau_b']:.4f} (p = {nfip['kendall_p_value']:.4f})",
            f"- **Flooded land area vs claims:** Spearman rho = {area_corr['spearman_rho']:.4f} (p = {area_corr['p_value']:.4f}); Kendall tau-b = {area_corr['kendall_tau_b']:.4f} (p = {area_corr['kendall_p_value']:.4f})",
            f"- **Binary presence (>=1 claim vs 0 claims) on flooded land area:** AUC = {nfip['binary_presence_mwu']['auc']:.4f} (p = {nfip['binary_presence_mwu']['p_value']:.4f})",
        ]
    )

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run_rescore()
