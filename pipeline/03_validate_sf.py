"""Validate SFINCS max flood depth against the SFPUC 100-Year Storm Flood Risk Map."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
from collections.abc import Mapping
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
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject
from scipy import ndimage
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parent.parent


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
