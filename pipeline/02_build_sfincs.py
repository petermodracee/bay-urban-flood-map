"""Build and run HydroMT-SFINCS model instances for storm x SLR scenarios."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import rasterio  # must precede geopandas/pyogrio to avoid GDAL 3.10 double-free

# isort: split
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
import yaml
from hydromt_sfincs import SfincsModel
from pyproj import CRS
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from shapely.geometry import Point, shape


def assert_crs_equals(actual_crs: Any, expected_crs: str, label: str) -> None:
    """Assert that a dataset or config CRS matches the expected CRS."""
    if actual_crs is None:
        raise ValueError(f"{label} is missing CRS; expected {expected_crs}")
    if CRS.from_user_input(actual_crs) != CRS.from_user_input(expected_crs):
        raise ValueError(
            f"{label} CRS mismatch: got {actual_crs}, expected {expected_crs}"
        )


def resolve_scenarios(
    cfg: Mapping[str, Any],
    scenario_id: str | None = None,
    run_all: bool = False,
) -> list[dict[str, Any]]:
    """Resolve target scenario definitions from config, defaulting to baseline_scenario."""
    scenarios = list(cfg.get("scenarios") or [])
    if not scenarios:
        raise ValueError("Configuration contains no scenarios.")
    if run_all:
        return scenarios

    target_id = scenario_id or str(
        cfg.get("project", {}).get("baseline_scenario") or ""
    )
    if not target_id:
        raise ValueError(
            "No scenario_id specified and project.baseline_scenario is missing."
        )

    matched = [dict(sc) for sc in scenarios if sc.get("id") == target_id]
    if not matched:
        available = [sc.get("id") for sc in scenarios]
        raise ValueError(
            f"Unknown scenario '{target_id}'. Available scenarios: {available}"
        )
    return matched


def compute_sfincs_grid_spec(
    bbox_utm: Sequence[float],
    grid_resolution_m: float,
    subgrid_resolution_m: float,
    project_crs: str,
) -> dict[str, Any]:
    """Compute SFINCS regular grid dimensions and subgrid refinement ratio in EPSG:26910."""
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    if len(bbox_utm) != 4:
        raise ValueError(f"Expected 4 coordinates in bbox, got {len(bbox_utm)}")
    if grid_resolution_m <= 0 or subgrid_resolution_m <= 0:
        raise ValueError("grid_resolution_m and subgrid_resolution_m must be positive.")

    xmin, ymin, xmax, ymax = (float(v) for v in bbox_utm)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError(f"Invalid bbox ordering: {bbox_utm}")

    mmax_f = (xmax - xmin) / grid_resolution_m
    nmax_f = (ymax - ymin) / grid_resolution_m
    mmax = round(mmax_f)
    nmax = round(nmax_f)
    if not (
        math.isclose(mmax_f, mmax, abs_tol=1e-6)
        and math.isclose(nmax_f, nmax, abs_tol=1e-6)
    ):
        raise ValueError(
            f"bbox {(xmin, ymin, xmax, ymax)} is not divisible by grid_resolution_m={grid_resolution_m}"
        )

    ratio_f = grid_resolution_m / subgrid_resolution_m
    nr_subgrid_pixels = round(ratio_f)
    if (
        not math.isclose(ratio_f, nr_subgrid_pixels, abs_tol=1e-6)
        or nr_subgrid_pixels < 1
    ):
        raise ValueError(
            f"grid_resolution_m ({grid_resolution_m}) must be an integer multiple of "
            f"subgrid_resolution_m ({subgrid_resolution_m})"
        )

    return {
        "x0": xmin,
        "y0": ymin,
        "dx": float(grid_resolution_m),
        "dy": float(grid_resolution_m),
        "mmax": mmax,
        "nmax": nmax,
        "rotation": 0.0,
        "epsg": 26910,
        "nr_subgrid_pixels": nr_subgrid_pixels,
    }


def _extract_time_and_intensity(
    row: Mapping[str, Any] | Sequence[float],
) -> tuple[float, float]:
    if isinstance(row, Mapping):
        return float(row["time_min"]), float(row["intensity_mm_hr"])
    if len(row) == 2:
        return float(row[0]), float(row[1])
    if len(row) == 3:
        return float(row[0]), float(row[2])
    raise ValueError(f"Expected 2 or 3 values in hyetograph row, got {len(row)}")


def apply_urban_drainage_subtraction(
    hyetograph_rows: Sequence[Mapping[str, Any] | Sequence[float]],
    drainage_rate_mm_hr: float,
) -> list[tuple[float, float]]:
    """Subtract a uniform urban drainage capacity (mm/hr) from rainfall intensities."""
    if drainage_rate_mm_hr < 0:
        raise ValueError(
            f"drainage_rate_mm_hr must be non-negative, got {drainage_rate_mm_hr}"
        )

    net_rows: list[tuple[float, float]] = []
    for row in hyetograph_rows:
        time_min, intensity_mm_hr = _extract_time_and_intensity(row)
        net_intensity_mm_hr = max(0.0, intensity_mm_hr - drainage_rate_mm_hr)
        net_rows.append((time_min, net_intensity_mm_hr))
    return net_rows


def build_precip_series(
    hyetograph_rows: Sequence[Mapping[str, Any] | Sequence[float]],
    reference_time_utc: str | datetime,
    duration_hr: float,
    drainage_rate_mm_hr: float,
) -> pd.Series:
    """Build a timestamped net precipitation Series (mm/hr) covering [tstart, tstop]."""
    if duration_hr <= 0:
        raise ValueError(f"duration_hr must be positive, got {duration_hr}")

    tref = (
        datetime.fromisoformat(reference_time_utc)
        if isinstance(reference_time_utc, str)
        else reference_time_utc
    )
    # Strip tzinfo if present so SFINCS timestamps stay naive UTC matching tref.
    tref = tref.replace(tzinfo=None)
    net_rows = apply_urban_drainage_subtraction(hyetograph_rows, drainage_rate_mm_hr)
    if not net_rows:
        raise ValueError("hyetograph_rows must not be empty.")

    times: list[datetime] = [tref + timedelta(minutes=t_min) for t_min, _ in net_rows]
    values: list[float] = [rate for _, rate in net_rows]

    tstop = tref + timedelta(hours=duration_hr)
    if times[-1] < tstop:
        times.append(tstop)
        values.append(0.0)

    series = pd.Series(
        values, index=pd.DatetimeIndex(times, name="time"), name="precip"
    )
    return series


def resolve_reference_time_utc(
    sfincs_cfg: Mapping[str, Any],
    rain_cfg: Mapping[str, Any],
    slr_cfg: Mapping[str, Any],
) -> datetime:
    """Resolve naive UTC reference timestamp giving priority to event-specific overrides."""
    raw_ref = (
        slr_cfg.get("reference_time_utc")
        or rain_cfg.get("reference_time_utc")
        or sfincs_cfg["reference_time_utc"]
    )
    tref = datetime.fromisoformat(str(raw_ref)) if isinstance(raw_ref, str) else raw_ref
    return tref.replace(tzinfo=None)


def build_waterlevel_forcing_inputs(
    tidal_rows: Sequence[Mapping[str, Any]],
    reference_time_utc: str | datetime,
    duration_hr: float,
    location_xy: tuple[float, float],
    project_crs: str,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Build water-level forcing timeseries (bzs DataFrame) and gauge point GeoDataFrame."""
    if duration_hr <= 0:
        raise ValueError(f"duration_hr must be positive, got {duration_hr}")
    if not tidal_rows:
        raise ValueError("tidal_rows must not be empty.")
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")

    tref = (
        datetime.fromisoformat(reference_time_utc)
        if isinstance(reference_time_utc, str)
        else reference_time_utc
    ).replace(tzinfo=None)

    times: list[datetime] = []
    values: list[float] = []
    for row in tidal_rows:
        t_min = float(row["time_min"])
        wl_m = float(row["water_level_m"])
        times.append(tref + timedelta(minutes=t_min))
        values.append(wl_m)

    tstop = tref + timedelta(hours=duration_hr)
    if times[-1] < tstop:
        times.append(tstop)
        values.append(values[-1])

    bzs_df = pd.DataFrame({1: values}, index=pd.DatetimeIndex(times, name="time"))
    bnd_gdf = gpd.GeoDataFrame(
        {"index": [1]},
        geometry=[Point(float(location_xy[0]), float(location_xy[1]))],
        crs=project_crs,
    )
    return bzs_df, bnd_gdf


def build_multi_station_waterlevel_forcing_inputs(
    tidal_rows: Sequence[Mapping[str, Any]],
    stations: Sequence[Mapping[str, Any]],
    reference_time_utc: str | datetime,
    duration_hr: float,
    project_crs: str,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Build multi-station water-level forcing timeseries (bzs DataFrame) and gauge GeoDataFrame."""
    if duration_hr <= 0:
        raise ValueError(f"duration_hr must be positive, got {duration_hr}")
    if not tidal_rows:
        raise ValueError("tidal_rows must not be empty.")
    if not stations:
        raise ValueError("stations must not be empty.")
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")

    station_cols = [f"wl_{st['id']}_m" for st in stations]
    missing_cols = [col for col in station_cols if col not in tidal_rows[0]]
    if missing_cols:
        raise ValueError(
            f"Multi-station tidal rows missing required columns: {missing_cols}"
        )

    tref = (
        datetime.fromisoformat(reference_time_utc)
        if isinstance(reference_time_utc, str)
        else reference_time_utc
    ).replace(tzinfo=None)

    times: list[datetime] = []
    series_by_idx: dict[int, list[float]] = {
        idx: [] for idx in range(1, len(stations) + 1)
    }
    for row in tidal_rows:
        times.append(tref + timedelta(minutes=float(row["time_min"])))
        for idx, col in enumerate(station_cols, start=1):
            series_by_idx[idx].append(float(row[col]))

    tstop = tref + timedelta(hours=duration_hr)
    if times[-1] < tstop:
        times.append(tstop)
        for values in series_by_idx.values():
            values.append(values[-1])

    bzs_df = pd.DataFrame(series_by_idx, index=pd.DatetimeIndex(times, name="time"))
    bnd_gdf = gpd.GeoDataFrame(
        {"index": list(range(1, len(stations) + 1))},
        geometry=[
            Point(float(st["x_epsg26910"]), float(st["y_epsg26910"])) for st in stations
        ],
        crs=project_crs,
    )
    return bzs_df, bnd_gdf


def _read_tidal_hydrograph_csv(csv_path: Path) -> list[dict[str, float]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Tidal hydrograph CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [{k: float(v) for k, v in row.items()} for row in reader]


def write_sfincs_waterlevel_files(
    run_dir: Path,
    bzs_df: pd.DataFrame,
    bnd_gdf: gpd.GeoDataFrame,
    tref: datetime,
) -> tuple[Path, Path]:
    """Write SFINCS ASCII boundary locations (sfincs.bnd) and waterlevels (sfincs.bzs)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    bnd_path = run_dir / "sfincs.bnd"
    bzs_path = run_dir / "sfincs.bzs"

    with bnd_path.open("w", encoding="utf-8") as f:
        for geom in bnd_gdf.geometry:
            f.write(f"{geom.x:.1f} {geom.y:.1f}\n")

    with bzs_path.open("w", encoding="utf-8") as f:
        for ts, row in bzs_df.iterrows():
            time_s = (pd.Timestamp(ts).to_pydatetime() - tref).total_seconds()
            wl_vals = " ".join(f"{float(v):8.3f}" for v in row.to_numpy())
            f.write(f"{time_s:10.1f} {wl_vals}\n")

    return bnd_path, bzs_path


def compute_tier1a_drainage_rate_grid(
    impervious_pct: np.ndarray,
    landcover: np.ndarray,
    base_rate_mm_hr: float = 2.0,
    max_rate_mm_hr: float = 10.0,
) -> np.ndarray:
    """Compute Tier 1a 2D urban drainage abstraction (mm/hr) from NLCD imperviousness."""
    if base_rate_mm_hr < 0 or max_rate_mm_hr < base_rate_mm_hr:
        raise ValueError(
            f"Invalid Tier 1a rates: base={base_rate_mm_hr}, max={max_rate_mm_hr}"
        )
    valid_imp = (impervious_pct >= 0.0) & (impervious_pct <= 100.0)
    f_imp = np.clip(np.where(valid_imp, impervious_pct / 100.0, 0.0), 0.0, 1.0)
    is_water = landcover == 11
    is_urban = (f_imp > 0.0) | np.isin(landcover, [21, 22, 23, 24])
    rate_mm_hr = np.where(
        (~is_water) & is_urban,
        base_rate_mm_hr + (max_rate_mm_hr - base_rate_mm_hr) * f_imp,
        0.0,
    )
    return rate_mm_hr.astype(np.float32)


def normalize_tier1a_mean_rate(
    drainage_1a_mm_hr: np.ndarray,
    landcover: np.ndarray,
    impervious_pct: np.ndarray,
    target_mean_mm_hr: float = 5.0,
) -> np.ndarray:
    """Normalize Tier 1a 2D drainage so the domain-mean urban land rate equals target_mean_mm_hr."""
    if target_mean_mm_hr < 0.0:
        raise ValueError(
            f"target_mean_mm_hr must be non-negative, got {target_mean_mm_hr}"
        )
    valid_imp = (impervious_pct >= 0.0) & (impervious_pct <= 100.0)
    f_imp = np.clip(np.where(valid_imp, impervious_pct / 100.0, 0.0), 0.0, 1.0)
    is_water = landcover == 11
    urban_mask = (~is_water) & ((f_imp > 0.0) | np.isin(landcover, [21, 22, 23, 24]))
    if not np.any(urban_mask):
        return drainage_1a_mm_hr.astype(np.float32)
    current_mean = float(np.mean(drainage_1a_mm_hr[urban_mask]))
    if current_mean <= 0.0:
        return drainage_1a_mm_hr.astype(np.float32)
    scale = float(target_mean_mm_hr) / current_mean
    matched = np.where(urban_mask, drainage_1a_mm_hr * scale, 0.0)
    return matched.astype(np.float32)


def apply_amc_iii_curve_number(
    cn_ii: np.ndarray,
    nodata_value: float = -9999.0,
) -> np.ndarray:
    """Convert AMC-II Curve Number to AMC-III using USDA NRCS NEH-630."""
    cn_arr = np.asarray(cn_ii, dtype=np.float32)
    valid = np.isfinite(cn_arr) & (cn_arr != nodata_value) & (cn_arr > 0.0)
    cn_iii = np.where(
        valid,
        (23.0 * cn_arr) / (10.0 + 0.13 * cn_arr),
        nodata_value,
    )
    cn_iii = np.where(valid, np.clip(cn_iii, 0.0, 100.0), nodata_value)
    return cn_iii.astype(np.float32)


def select_amc_condition(
    antecedent_5day_mm: float | None,
    threshold_mm: float = 28.0,
) -> str:
    """Select USDA NRCS antecedent moisture condition ('AMC-II' or 'AMC-III')."""
    if antecedent_5day_mm is not None and float(antecedent_5day_mm) > float(
        threshold_mm
    ):
        return "AMC-III"
    return "AMC-II"


def compute_tier1b_effective_drainage(
    drainage_1a_mm_hr: np.ndarray,
    dem_m: np.ndarray,
    tide_m: float | np.ndarray,
    head_full_m: float = 1.5,
    polder_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Attenuate Tier 1a gravity drainage smoothly as (DEM - tide(t)) -> 0."""
    if head_full_m <= 0:
        raise ValueError(f"head_full_m must be positive, got {head_full_m}")
    tide_arr = np.asarray(tide_m, dtype=np.float32)
    if tide_arr.ndim == 0:
        head_m = dem_m.astype(np.float32) - float(tide_arr)
        factor = np.clip(head_m / float(head_full_m), 0.0, 1.0)
        if polder_mask is not None:
            factor = np.where(polder_mask, 1.0, factor)
        return (drainage_1a_mm_hr * factor).astype(np.float32)
    if tide_arr.ndim == 1:
        head_m = dem_m[None, :, :].astype(np.float32) - tide_arr[:, None, None]
        factor = np.clip(head_m / float(head_full_m), 0.0, 1.0)
        if polder_mask is not None:
            factor = np.where(polder_mask[None, :, :], 1.0, factor)
        return (drainage_1a_mm_hr[None, :, :] * factor).astype(np.float32)
    raise ValueError(f"tide_m must be scalar or 1D array, got shape {tide_arr.shape}")


def build_gridded_net_precip_da(
    gross_precip_series: pd.Series,
    drainage_mm_hr: np.ndarray,
    x_coords: np.ndarray,
    y_coords: np.ndarray,
    project_crs: str,
) -> xr.DataArray:
    """Build SFINCS gridded net precipitation DataArray (time, y, x) in mm/hr."""
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    gross = gross_precip_series.to_numpy(dtype=np.float32)
    if drainage_mm_hr.ndim == 2:
        net_rain = np.maximum(0.0, gross[:, None, None] - drainage_mm_hr[None, :, :])
    elif drainage_mm_hr.ndim == 3:
        if drainage_mm_hr.shape[0] != len(gross):
            raise ValueError(
                f"3D drainage time length {drainage_mm_hr.shape[0]} != precip {len(gross)}"
            )
        net_rain = np.maximum(0.0, gross[:, None, None] - drainage_mm_hr)
    else:
        raise ValueError(f"drainage_mm_hr must be 2D or 3D, got {drainage_mm_hr.ndim}D")

    da = xr.DataArray(
        net_rain.astype(np.float32),
        dims=("time", "y", "x"),
        coords={
            "time": pd.DatetimeIndex(gross_precip_series.index, name="time"),
            "y": np.asarray(y_coords, dtype=np.float64),
            "x": np.asarray(x_coords, dtype=np.float64),
        },
        name="precip_2d",
    )
    return da.rio.write_crs(project_crs)


def build_mrms_net_precip_da(
    mrms_da: xr.DataArray,
    drainage_mm_hr: float | np.ndarray,
    tref: datetime,
    duration_hr: float,
    project_crs: str,
) -> xr.DataArray:
    """Apply drainage abstraction to gridded MRMS QPE (time, y, x) and return SFINCS precip_2d."""
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    if duration_hr <= 0:
        raise ValueError(f"duration_hr must be positive, got {duration_hr}")

    times = pd.DatetimeIndex(mrms_da.coords["time"].values).tz_localize(None)
    vals = mrms_da.values.astype(np.float32)

    if np.ndim(drainage_mm_hr) == 0:
        net = np.maximum(0.0, vals - float(drainage_mm_hr)).astype(np.float32)
    else:
        drain_arr = np.asarray(drainage_mm_hr, dtype=np.float32)
        if drain_arr.ndim == 2:
            net = np.maximum(0.0, vals - drain_arr[None, :, :]).astype(np.float32)
        elif drain_arr.ndim == 3:
            net = np.maximum(0.0, vals - drain_arr).astype(np.float32)
        else:
            raise ValueError(f"Unsupported drainage array shape: {drain_arr.shape}")

    tstop = tref + timedelta(hours=duration_hr)
    if times[-1].to_pydatetime() < tstop:
        times = times.append(pd.DatetimeIndex([tstop]))
        net = np.concatenate([net, net[-1:, :, :]], axis=0)

    da = xr.DataArray(
        net,
        dims=("time", "y", "x"),
        coords={
            "time": pd.DatetimeIndex(times, name="time"),
            "y": np.asarray(mrms_da.coords["y"].values, dtype=np.float64),
            "x": np.asarray(mrms_da.coords["x"].values, dtype=np.float64),
        },
        name="precip_2d",
    )
    return da.rio.write_crs(project_crs)


def _reproject_raster_to_grid(
    src_path: Path,
    dst_shape: tuple[int, int],
    dst_transform: Any,
    dst_crs: str,
    resampling: Resampling,
    dst_dtype: np.dtype[Any],
) -> np.ndarray:
    if not src_path.exists():
        raise FileNotFoundError(f"Required drainage input raster not found: {src_path}")
    dst = np.zeros(dst_shape, dtype=dst_dtype)
    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError(f"{src_path} is missing CRS")
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=resampling,
        )
    return dst


def _load_resampled_drainage_inputs(
    cfg: Mapping[str, Any],
    bbox_epsg26910: Sequence[float],
    grid_res_m: float,
    project_crs: str,
    drainage_tier: str,
) -> tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray | None, np.ndarray, np.ndarray
]:
    """Load and resample NLCD impervious/landcover, hydro-DEM, and optional polder mask."""
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    domain = cfg["domain"]
    if drainage_tier == "tier2":
        storm_drain_src = domain.get("storm_drain_source")
        if not storm_drain_src:
            raise ValueError(
                "drainage_tier='tier2' requires domain.storm_drain_source to be configured."
            )
        if not Path(storm_drain_src).exists():
            raise FileNotFoundError(
                f"Tier 2 storm_drain_source not found: {storm_drain_src}"
            )

    xmin, ymin, xmax, ymax = (float(v) for v in bbox_epsg26910)
    ncols = max(1, math.ceil((xmax - xmin) / grid_res_m))
    nrows = max(1, math.ceil((ymax - ymin) / grid_res_m))
    # North-up transform for rasterio.warp.reproject, then flip to y-ascending for SFINCS NetCDF
    north_up_transform = from_origin(xmin, ymax, grid_res_m, grid_res_m)
    dst_shape = (nrows, ncols)

    imp = _reproject_raster_to_grid(
        Path(domain["nlcd_impervious_source"]),
        dst_shape,
        north_up_transform,
        project_crs,
        Resampling.bilinear,
        np.dtype(np.float32),
    )
    lc = _reproject_raster_to_grid(
        Path(domain["nlcd_landcover_source"]),
        dst_shape,
        north_up_transform,
        project_crs,
        Resampling.nearest,
        np.dtype(np.int32),
    )
    dem = _reproject_raster_to_grid(
        Path(cfg["terrain_prep"]["hydro_dem_file"]),
        dst_shape,
        north_up_transform,
        project_crs,
        Resampling.bilinear,
        np.dtype(np.float32),
    )

    polder_mask: np.ndarray | None = None
    polder_src = domain.get("polder_mask_source")
    if polder_src:
        polder_path = Path(polder_src)
        if not polder_path.exists():
            raise FileNotFoundError(f"polder_mask_source not found: {polder_path}")
        gdf = gpd.read_file(polder_path)
        if gdf.crs is not None and CRS.from_user_input(gdf.crs) != CRS.from_user_input(
            project_crs
        ):
            gdf = gdf.to_crs(project_crs)
        if len(gdf) > 0:
            polder_mask = (
                rasterize(
                    [(geom, 1) for geom in gdf.geometry if geom is not None],
                    out_shape=dst_shape,
                    transform=north_up_transform,
                    fill=0,
                    dtype=np.uint8,
                )
                == 1
            )

    # Flip vertically so y increases monotonically (SFINCS NetCDF convention)
    imp = np.flipud(imp)
    lc = np.flipud(lc)
    dem = np.flipud(dem)
    if polder_mask is not None:
        polder_mask = np.flipud(polder_mask)

    x_coords = xmin + (np.arange(ncols, dtype=np.float64) + 0.5) * grid_res_m
    y_coords = ymax - (np.arange(nrows, dtype=np.float64) + 0.5) * grid_res_m
    y_coords = y_coords[::-1]
    return imp, lc, dem, polder_mask, x_coords, y_coords


def _interpolate_mean_tide_series(
    tidal_rows: Sequence[Mapping[str, Any]],
    stations: Sequence[Mapping[str, Any]],
    tref: datetime,
    target_times: pd.DatetimeIndex,
) -> np.ndarray:
    """Interpolate mean Bay water level (m NAVD88) onto precipitation timestamps."""
    t_min = np.array([float(r["time_min"]) for r in tidal_rows], dtype=np.float64)
    if stations and all(f"wl_{st['id']}_m" in tidal_rows[0] for st in stations):
        cols = [f"wl_{st['id']}_m" for st in stations]
        wl = np.mean([[float(r[c]) for c in cols] for r in tidal_rows], axis=1)
    else:
        wl = np.array([float(r["water_level_m"]) for r in tidal_rows], dtype=np.float64)
    target_min = np.array(
        [(ts.to_pydatetime() - tref).total_seconds() / 60.0 for ts in target_times],
        dtype=np.float64,
    )
    return np.interp(target_min, t_min, wl).astype(np.float32)


def compute_valid_active_mask(
    dem_2m: np.ndarray,
    nodata_value: float,
    nr_subgrid_pixels: int,
    y_ascending: bool,
) -> np.ndarray:
    """Downsample 2 m DEM validity onto the SFINCS grid matching SFINCS y orientation."""
    if nr_subgrid_pixels < 1:
        raise ValueError(f"nr_subgrid_pixels must be >= 1, got {nr_subgrid_pixels}")
    height, width = dem_2m.shape
    if height % nr_subgrid_pixels != 0 or width % nr_subgrid_pixels != 0:
        raise ValueError(
            f"DEM shape {dem_2m.shape} is not divisible by nr_subgrid_pixels={nr_subgrid_pixels}"
        )
    nmax = height // nr_subgrid_pixels
    mmax = width // nr_subgrid_pixels
    valid_fine = (dem_2m != nodata_value) & np.isfinite(dem_2m)
    valid_coarse = valid_fine.reshape(
        nmax, nr_subgrid_pixels, mmax, nr_subgrid_pixels
    ).any(axis=(1, 3))
    if y_ascending:
        return np.flipud(valid_coarse)
    return valid_coarse


def build_docker_run_command(run_dir: Path, docker_image: str) -> list[str]:
    """Construct the docker run command mounting run_dir at /data."""
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{run_dir.resolve()}:/data",
        docker_image,
    ]


def _read_hyetograph_csv(csv_path: Path) -> list[dict[str, float]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Hyetograph CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [
            {
                "time_min": float(row["time_min"]),
                "depth_mm": float(row["depth_mm"]),
                "intensity_mm_hr": float(row["intensity_mm_hr"]),
            }
            for row in reader
        ]


def _verify_processed_raster(path: Path, expected_crs: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Processed Phase 1 raster not found: {path}")
    with rasterio.open(path) as ds:
        assert_crs_equals(ds.crs, expected_crs, str(path))


def build_quadtree_refinement_polygons(
    dem_m: np.ndarray,
    transform: Any,
    project_crs: str,
    zmax_m: float = 15.0,
    nodata_value: float = -9999.0,
    min_area_m2: float = 10000.0,
    refinement_level: int = 2,
) -> gpd.GeoDataFrame:
    """Build low-lying urban/coastal corridor polygons for SFINCS quadtree refinement."""
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    if refinement_level < 1:
        raise ValueError(f"refinement_level must be >= 1, got {refinement_level}")

    valid = np.isfinite(dem_m)
    if not np.isnan(nodata_value):
        valid &= ~np.isclose(dem_m, float(nodata_value))
    refine_mask = (valid & (dem_m <= float(zmax_m))).astype(np.uint8)

    geoms: list[Any] = []
    for geom_dict, val in shapes(
        refine_mask, mask=refine_mask == 1, transform=transform
    ):
        if int(val) != 1:
            continue
        poly = shape(geom_dict)
        if poly.is_valid and poly.area >= float(min_area_m2):
            geoms.append(poly)

    if not geoms:
        return gpd.GeoDataFrame(
            {"refinement_level": pd.Series(dtype="int64")},
            geometry=gpd.GeoSeries([], crs=project_crs),
            crs=project_crs,
        )
    return gpd.GeoDataFrame(
        {"refinement_level": [int(refinement_level)] * len(geoms)},
        geometry=geoms,
        crs=project_crs,
    )


def _apply_dem_valid_mask_to_quadtree(
    sf: SfincsModel,
    dem_arr: np.ndarray,
    dem_transform: Any,
    dem_nodata: float,
) -> None:
    """Mask quadtree cells whose centers fall on DEM NoData as inactive (msk = 0)."""
    fx, fy = sf.quadtree_grid.face_coordinates
    rows, cols = rasterio.transform.rowcol(dem_transform, fx, fy)
    rows_arr = np.asarray(rows, dtype=np.int64)
    cols_arr = np.asarray(cols, dtype=np.int64)
    in_bounds = (
        (rows_arr >= 0)
        & (rows_arr < dem_arr.shape[0])
        & (cols_arr >= 0)
        & (cols_arr < dem_arr.shape[1])
    )
    valid = np.zeros(len(fx), dtype=bool)
    sampled = dem_arr[
        np.clip(rows_arr, 0, dem_arr.shape[0] - 1),
        np.clip(cols_arr, 0, dem_arr.shape[1] - 1),
    ]
    valid[in_bounds] = np.isfinite(sampled[in_bounds])
    if not np.isnan(dem_nodata):
        valid[in_bounds] &= ~np.isclose(sampled[in_bounds], float(dem_nodata))

    mask_var = "mask" if "mask" in sf.quadtree_grid.data else "msk"
    da_msk = sf.quadtree_grid.data[mask_var]
    da_msk = da_msk.where(xr.DataArray(valid, dims=da_msk.dims), np.uint8(0)).astype(
        np.uint8
    )
    sf.quadtree_grid.data[mask_var] = da_msk


def _build_and_run_single_scenario(
    cfg: Mapping[str, Any],
    scenario: Mapping[str, Any],
    drainage_rate_mm_hr: float,
    skip_run: bool,
    force: bool,
    drainage_tier_override: str | None = None,
    quadtree_override: bool | None = None,
    rain_source_override: str | None = None,
    amc_mode_override: str | None = None,
    mean_rate_matched_override: bool | None = None,
    runs_dir_override: Path | None = None,
) -> None:
    scenario_id = str(scenario["id"])
    rain_key = str(scenario["rain"])
    slr_key = str(scenario["slr"])

    project = cfg["project"]
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    sfincs_cfg = cfg["sfincs_model"]
    runs_dir = (
        Path(runs_dir_override)
        if runs_dir_override is not None
        else Path(cfg["paths"]["runs_dir"])
    )
    run_dir = runs_dir / scenario_id
    map_nc_path = run_dir / "sfincs_map.nc"

    if (
        not force
        and not skip_run
        and map_nc_path.exists()
        and map_nc_path.stat().st_size > 0
    ):
        print(
            f"[02_build_sfincs] Skipping existing run {map_nc_path} (use --force to rebuild)."
        )
        return

    project_crs = str(project["crs"])
    hydro_dem_path = Path(tprep["hydro_dem_file"])
    manning_n_path = Path(tprep["manning_n_file"])
    curve_number_path = Path(tprep["curve_number_file"])
    for raster_path in (hydro_dem_path, manning_n_path, curve_number_path):
        _verify_processed_raster(raster_path, project_crs)

    rain_cfg = cfg["rain_events"][rain_key]
    slr_cfg = cfg["slr_increments"][slr_key]
    tidal_file = slr_cfg.get("tidal_hydrograph_file")
    if float(slr_cfg.get("slr_m", 0.0)) > 0.0 and not tidal_file:
        print(
            f"[02_build_sfincs] Note: {scenario_id} has slr_m={slr_cfg['slr_m']} "
            "without tidal_hydrograph_file; building pluvial baseline boundary setup."
        )

    grid_spec = compute_sfincs_grid_spec(
        bbox_utm=domain["bbox_epsg26910"],
        grid_resolution_m=float(domain["grid_resolution_m"]),
        subgrid_resolution_m=float(domain["subgrid_resolution_m"]),
        project_crs=project_crs,
    )

    drainage_tier = str(
        drainage_tier_override or sfincs_cfg.get("drainage_tier", "tier0")
    ).lower()
    if drainage_tier not in {"tier0", "tier1a", "tier1b", "tier2"}:
        raise ValueError(f"Unsupported drainage_tier: {drainage_tier}")

    hyetograph_rows = _read_hyetograph_csv(Path(rain_cfg["hyetograph_file"]))
    duration_hr = float(rain_cfg["duration_hr"])
    tref = resolve_reference_time_utc(sfincs_cfg, rain_cfg, slr_cfg)
    tstop = tref + timedelta(hours=duration_hr)
    dtmaxout_s = max(float(sfincs_cfg.get("dtmaxout_s", 86400.0)), duration_hr * 3600.0)

    precip_drainage = drainage_rate_mm_hr if drainage_tier == "tier0" else 0.0
    precip_series = build_precip_series(
        hyetograph_rows=hyetograph_rows,
        reference_time_utc=tref,
        duration_hr=duration_hr,
        drainage_rate_mm_hr=precip_drainage,
    )

    quadtree_cfg = dict(sfincs_cfg.get("quadtree") or {})
    quadtree_enabled = (
        bool(quadtree_override)
        if quadtree_override is not None
        else bool(quadtree_cfg.get("enabled", False))
    )

    run_dir.mkdir(parents=True, exist_ok=True)

    amc_mode = str(
        amc_mode_override or sfincs_cfg.get("antecedent_moisture_mode", "amc2")
    ).lower()
    if amc_mode not in {"amc2", "auto", "amc3"}:
        raise ValueError(f"Unsupported antecedent_moisture_mode: {amc_mode}")
    effective_amc = "AMC-II"
    if amc_mode == "amc3":
        effective_amc = "AMC-III"
    elif amc_mode == "auto":
        antecedent_file = sfincs_cfg.get("antecedent_5day_file")
        threshold_mm = float(sfincs_cfg.get("antecedent_amc3_threshold_mm", 28.0))
        antecedent_mm: float | None = None
        if antecedent_file and Path(str(antecedent_file)).exists():
            antecedent_data = json.loads(
                Path(str(antecedent_file)).read_text(encoding="utf-8")
            )
            if rain_key in antecedent_data:
                antecedent_mm = float(antecedent_data[rain_key]["mean_5day_mm"])
        effective_amc = select_amc_condition(antecedent_mm, threshold_mm=threshold_mm)

    effective_cn_path = curve_number_path
    if effective_amc == "AMC-III":
        amc3_cn_path = run_dir / "curve_number_amc3.tif"
        with rasterio.open(curve_number_path) as cn_ds:
            cn_profile = cn_ds.profile.copy()
            cn_nodata = float(cn_ds.nodata) if cn_ds.nodata is not None else -9999.0
            cn_ii = cn_ds.read(1)
        cn_iii = apply_amc_iii_curve_number(cn_ii, nodata_value=cn_nodata)
        cn_profile.update(dtype="float32", nodata=cn_nodata)
        with rasterio.open(amc3_cn_path, "w", **cn_profile) as out_cn:
            out_cn.write(cn_iii, 1)
        effective_cn_path = amc3_cn_path

    mean_rate_matched = (
        bool(mean_rate_matched_override)
        if mean_rate_matched_override is not None
        else bool(sfincs_cfg.get("tier1_mean_rate_matched", False))
    )
    drainage_label = (
        f"tier0={drainage_rate_mm_hr:.1f} mm/hr"
        if drainage_tier == "tier0"
        else f"{drainage_tier} ({'rate-matched ' if mean_rate_matched else ''}netamprfile)"
    )
    if quadtree_enabled:
        base_res_m = float(quadtree_cfg.get("base_resolution_m", 40.0))
        refinement_levels = int(quadtree_cfg.get("refinement_levels", 2))
        print(
            f"[02_build_sfincs] Building {scenario_id}: quadtree "
            f"{base_res_m:.0f}m -> {base_res_m / (2**refinement_levels):.0f}m, "
            f"drainage={drainage_label} -> {run_dir}"
        )
    else:
        print(
            f"[02_build_sfincs] Building {scenario_id}: {grid_spec['mmax']}x{grid_spec['nmax']} @ "
            f"{grid_spec['dx']} m (subgrid {grid_spec['nr_subgrid_pixels']}x), "
            f"drainage={drainage_label} -> {run_dir}"
        )

    with rasterio.open(hydro_dem_path) as dem_ds:
        dem_arr = dem_ds.read(1)
        dem_transform = dem_ds.transform
        dem_nodata = float(dem_ds.nodata) if dem_ds.nodata is not None else float("nan")

    sf = SfincsModel(root=str(run_dir), mode="w+")
    outflow_bounds = bool(sfincs_cfg.get("outflow_perimeter_bounds", True))
    coastal_zmax_m = float(sfincs_cfg.get("coastal_bound_zmax_m", 0.5))

    if quadtree_enabled:
        base_res_m = float(quadtree_cfg.get("base_resolution_m", 40.0))
        refinement_levels = int(quadtree_cfg.get("refinement_levels", 2))
        refine_zmax_m = float(quadtree_cfg.get("refine_zmax_m", 15.0))
        min_poly_area_m2 = float(quadtree_cfg.get("min_polygon_area_m2", 10000.0))
        qt_grid_spec = compute_sfincs_grid_spec(
            bbox_utm=domain["bbox_epsg26910"],
            grid_resolution_m=base_res_m,
            subgrid_resolution_m=float(domain["subgrid_resolution_m"]),
            project_crs=project_crs,
        )
        refine_gdf = build_quadtree_refinement_polygons(
            dem_m=dem_arr,
            transform=dem_transform,
            project_crs=project_crs,
            zmax_m=refine_zmax_m,
            nodata_value=dem_nodata,
            min_area_m2=min_poly_area_m2,
            refinement_level=refinement_levels,
        )
        sf.quadtree_grid.create(
            x0=qt_grid_spec["x0"],
            y0=qt_grid_spec["y0"],
            nmax=qt_grid_spec["nmax"],
            mmax=qt_grid_spec["mmax"],
            dx=qt_grid_spec["dx"],
            dy=qt_grid_spec["dy"],
            rotation=qt_grid_spec["rotation"],
            epsg=qt_grid_spec["epsg"],
            refinement_polygons=refine_gdf if len(refine_gdf) > 0 else None,
        )
        sf.quadtree_elevation.create(
            elevation_list=[{"elevation": str(hydro_dem_path.resolve())}]
        )
        sf.quadtree_mask.create_active(zmin=-50.0, reset_mask=True)
        _apply_dem_valid_mask_to_quadtree(
            sf=sf,
            dem_arr=dem_arr,
            dem_transform=dem_transform,
            dem_nodata=dem_nodata,
        )
        if tidal_file:
            sf.quadtree_mask.create_boundary(
                btype="waterlevel",
                zmax=coastal_zmax_m,
                reset_bounds=True,
            )
            if outflow_bounds:
                sf.quadtree_mask.create_boundary(
                    btype="outflow",
                    zmin=coastal_zmax_m,
                    reset_bounds=False,
                )
        elif outflow_bounds:
            sf.quadtree_mask.create_boundary(
                btype="outflow",
                zmin=-9999.0,
                reset_bounds=False,
            )
        sf.quadtree_subgrid.create(
            elevation_list=[{"elevation": str(hydro_dem_path.resolve())}],
            roughness_list=[{"manning": str(manning_n_path.resolve())}],
            nr_levels=int(sfincs_cfg.get("subgrid_nbins", 10)),
            nr_subgrid_pixels=qt_grid_spec["nr_subgrid_pixels"],
            max_gradient=float(sfincs_cfg.get("subgrid_max_gradient", 5.0)),
            write_dep_tif=False,
        )
        sf.quadtree_infiltration.create_cn(
            cn=effective_cn_path.resolve(),
            antecedent_moisture=None,
            reproj_method=str(sfincs_cfg.get("cn_reproj_method", "med")),
        )
    else:
        sf.grid.create(
            x0=grid_spec["x0"],
            y0=grid_spec["y0"],
            dx=grid_spec["dx"],
            dy=grid_spec["dy"],
            nmax=grid_spec["nmax"],
            mmax=grid_spec["mmax"],
            rotation=grid_spec["rotation"],
            epsg=grid_spec["epsg"],
        )
        sf.elevation.create(
            elevation_list=[{"elevation": str(hydro_dem_path.resolve())}]
        )
        sf.mask.create_active(zmin=-50.0, reset_mask=True)
        if dem_arr.shape == (
            grid_spec["nmax"] * grid_spec["nr_subgrid_pixels"],
            grid_spec["mmax"] * grid_spec["nr_subgrid_pixels"],
        ):
            reg_mask_var = "mask" if "mask" in sf.grid.data else "msk"
            y_coords = sf.grid.data[reg_mask_var].coords["y"].values.ravel()
            y_ascending = bool(y_coords[0] < y_coords[-1])
            valid_mask = compute_valid_active_mask(
                dem_2m=dem_arr,
                nodata_value=dem_nodata,
                nr_subgrid_pixels=grid_spec["nr_subgrid_pixels"],
                y_ascending=y_ascending,
            )
            da_msk = (
                sf.grid.data[reg_mask_var]
                .where(valid_mask, np.uint8(0))
                .astype(np.uint8)
            )
            sf.grid.data[reg_mask_var] = da_msk

        if tidal_file:
            sf.mask.create_boundary(
                btype="waterlevel",
                zmax=coastal_zmax_m,
                reset_bounds=True,
            )
            if outflow_bounds:
                sf.mask.create_boundary(
                    btype="outflow",
                    zmin=coastal_zmax_m,
                    reset_bounds=False,
                )
        elif outflow_bounds:
            sf.mask.create_boundary(
                btype="outflow",
                zmin=-9999.0,
                reset_bounds=False,
            )

        sf.subgrid.create(
            elevation_list=[{"elevation": str(hydro_dem_path.resolve())}],
            roughness_list=[{"manning": str(manning_n_path.resolve())}],
            nbins=int(sfincs_cfg.get("subgrid_nbins", 10)),
            nr_subgrid_pixels=grid_spec["nr_subgrid_pixels"],
            max_gradient=float(sfincs_cfg.get("subgrid_max_gradient", 5.0)),
        )
        sf.infiltration.create_cn(
            cn=effective_cn_path.resolve(),
            antecedent_moisture=None,
            reproj_method=str(sfincs_cfg.get("cn_reproj_method", "med")),
        )

    config_kwargs: dict[str, Any] = {
        "tref": tref,
        "tstart": tref,
        "tstop": tstop,
        "dtout": float(sfincs_cfg.get("dtout_s", 3600.0)),
        "dtmaxout": dtmaxout_s,
        "alpha": float(sfincs_cfg.get("alpha", 0.5)),
        "huthresh": float(sfincs_cfg.get("huthresh_m", 0.01)),
        "advection": int(sfincs_cfg.get("advection", 1)),
    }
    if quadtree_enabled:
        config_kwargs["qtrfile"] = "sfincs.nc"
        config_kwargs["sbgfile"] = "sfincs.sbg"
    rain_source = str(
        rain_source_override or sfincs_cfg.get("rain_source", "default")
    ).lower()
    mrms_file = rain_cfg.get("mrms_qpe_file")
    use_mrms = rain_source == "mrms" and bool(mrms_file)
    if rain_source_override == "mrms" and not mrms_file:
        raise ValueError(
            f"rain_source='mrms' requested for {scenario_id}, but mrms_qpe_file is not configured."
        )

    if drainage_tier == "tier0" and not use_mrms:
        config_kwargs["precipfile"] = "sfincs.prp"
    else:
        config_kwargs["netamprfile"] = "sfincs_netampr.nc"
    if tidal_file:
        config_kwargs["bndfile"] = "sfincs.bnd"
        config_kwargs["bzsfile"] = "sfincs.bzs"

    sf.config.update(config_kwargs)
    if use_mrms:
        mrms_path = Path(str(mrms_file))
        if not mrms_path.exists():
            raise FileNotFoundError(f"MRMS QPE NetCDF not found: {mrms_path}")
        with xr.open_dataset(mrms_path) as mrms_ds:
            mrms_da = mrms_ds["precip_mm_hr"].load()
        mrms_drainage: float | np.ndarray = float(drainage_rate_mm_hr)
        if drainage_tier != "tier0":
            drainage_res_m = float(sfincs_cfg.get("drainage_grid_res_m", 40.0))
            imp, lc, dem_drain, polder_mask, x_coords, y_coords = (
                _load_resampled_drainage_inputs(
                    cfg=cfg,
                    bbox_epsg26910=domain["bbox_epsg26910"],
                    grid_res_m=drainage_res_m,
                    project_crs=project_crs,
                    drainage_tier=drainage_tier,
                )
            )
            drain_1a = compute_tier1a_drainage_rate_grid(
                impervious_pct=imp,
                landcover=lc,
                base_rate_mm_hr=float(sfincs_cfg.get("tier1a_base_mm_hr", 2.0)),
                max_rate_mm_hr=float(sfincs_cfg.get("tier1a_max_mm_hr", 10.0)),
            )
            if mean_rate_matched:
                drain_1a = normalize_tier1a_mean_rate(
                    drainage_1a_mm_hr=drain_1a,
                    landcover=lc,
                    impervious_pct=imp,
                    target_mean_mm_hr=float(drainage_rate_mm_hr),
                )
            if drainage_tier == "tier1b":
                mrms_times = pd.DatetimeIndex(
                    mrms_da.coords["time"].values
                ).tz_localize(None)
                if tidal_file:
                    tidal_rows_for_drain = _read_tidal_hydrograph_csv(Path(tidal_file))
                    tide_series = _interpolate_mean_tide_series(
                        tidal_rows=tidal_rows_for_drain,
                        stations=list(cfg.get("project", {}).get("tide_gauges") or []),
                        tref=tref,
                        target_times=mrms_times,
                    )
                else:
                    tide_series = np.full(len(mrms_times), -10.0, dtype=np.float32)
                mrms_drainage = compute_tier1b_effective_drainage(
                    drainage_1a_mm_hr=drain_1a,
                    dem_m=dem_drain,
                    tide_m=tide_series,
                    head_full_m=float(sfincs_cfg.get("tier1b_head_full_m", 1.5)),
                    polder_mask=polder_mask,
                )
            else:
                mrms_drainage = drain_1a
            mrms_da = mrms_da.interp(
                y=y_coords,
                x=x_coords,
                method="nearest",
                kwargs={"fill_value": "extrapolate"},
            )
        precip_2d_da = build_mrms_net_precip_da(
            mrms_da=mrms_da,
            drainage_mm_hr=mrms_drainage,
            tref=tref,
            duration_hr=duration_hr,
            project_crs=project_crs,
        )
        sf.precipitation.set(precip_2d_da, name="precip_2d")
    elif drainage_tier == "tier0":
        sf.precipitation.set(precip_series.to_xarray(), name="precip")
    else:
        drainage_res_m = float(sfincs_cfg.get("drainage_grid_res_m", 40.0))
        imp, lc, dem_drain, polder_mask, x_coords, y_coords = (
            _load_resampled_drainage_inputs(
                cfg=cfg,
                bbox_epsg26910=domain["bbox_epsg26910"],
                grid_res_m=drainage_res_m,
                project_crs=project_crs,
                drainage_tier=drainage_tier,
            )
        )
        drainage_1a = compute_tier1a_drainage_rate_grid(
            impervious_pct=imp,
            landcover=lc,
            base_rate_mm_hr=float(sfincs_cfg.get("tier1a_base_mm_hr", 2.0)),
            max_rate_mm_hr=float(sfincs_cfg.get("tier1a_max_mm_hr", 10.0)),
        )
        if mean_rate_matched:
            drainage_1a = normalize_tier1a_mean_rate(
                drainage_1a_mm_hr=drainage_1a,
                landcover=lc,
                impervious_pct=imp,
                target_mean_mm_hr=float(drainage_rate_mm_hr),
            )
        if drainage_tier == "tier1b":
            if tidal_file:
                tidal_rows_for_drain = _read_tidal_hydrograph_csv(Path(tidal_file))
                tide_series = _interpolate_mean_tide_series(
                    tidal_rows=tidal_rows_for_drain,
                    stations=list(cfg.get("project", {}).get("tide_gauges") or []),
                    tref=tref,
                    target_times=precip_series.index,
                )
            else:
                tide_series = np.full(len(precip_series), -10.0, dtype=np.float32)
            effective_drainage = compute_tier1b_effective_drainage(
                drainage_1a_mm_hr=drainage_1a,
                dem_m=dem_drain,
                tide_m=tide_series,
                head_full_m=float(sfincs_cfg.get("tier1b_head_full_m", 1.5)),
                polder_mask=polder_mask,
            )
        else:
            effective_drainage = drainage_1a

        precip_2d_da = build_gridded_net_precip_da(
            gross_precip_series=precip_series,
            drainage_mm_hr=effective_drainage,
            x_coords=x_coords,
            y_coords=y_coords,
            project_crs=project_crs,
        )
        sf.precipitation.set(precip_2d_da, name="precip_2d")

    sf.write()

    if tidal_file:
        tidal_rows = _read_tidal_hydrograph_csv(Path(tidal_file))
        tide_gauges = list(cfg.get("project", {}).get("tide_gauges") or [])
        if (
            tide_gauges
            and tidal_rows
            and all(f"wl_{st['id']}_m" in tidal_rows[0] for st in tide_gauges)
        ):
            bzs_df, bnd_gdf = build_multi_station_waterlevel_forcing_inputs(
                tidal_rows=tidal_rows,
                stations=tide_gauges,
                reference_time_utc=tref,
                duration_hr=duration_hr,
                project_crs=project_crs,
            )
        else:
            xmin, ymin, xmax, ymax = (float(v) for v in domain["bbox_epsg26910"])
            bzs_df, bnd_gdf = build_waterlevel_forcing_inputs(
                tidal_rows=tidal_rows,
                reference_time_utc=tref,
                duration_hr=duration_hr,
                location_xy=(0.5 * (xmin + xmax), 0.5 * (ymin + ymax)),
                project_crs=project_crs,
            )
        write_sfincs_waterlevel_files(
            run_dir=run_dir,
            bzs_df=bzs_df,
            bnd_gdf=bnd_gdf,
            tref=tref,
        )

    inp_path = run_dir / "sfincs.inp"
    expected_precip = (
        run_dir / "sfincs.prp"
        if (drainage_tier == "tier0" and not use_mrms)
        else run_dir / "sfincs_netampr.nc"
    )
    if not inp_path.exists() or not expected_precip.exists():
        raise RuntimeError(f"Expected SFINCS build files missing in {run_dir}")
    if tidal_file and (
        not (run_dir / "sfincs.bnd").exists() or not (run_dir / "sfincs.bzs").exists()
    ):
        raise RuntimeError(f"Expected SFINCS tidal boundary files missing in {run_dir}")

    if skip_run:
        print(f"[02_build_sfincs] Built model files in {run_dir} (--skip-run enabled).")
        return

    docker_image = str(sfincs_cfg.get("docker_image", "deltares/sfincs-cpu:latest"))
    cmd = build_docker_run_command(run_dir, docker_image)
    print(f"[02_build_sfincs] Executing SFINCS solver: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    if not map_nc_path.exists() or map_nc_path.stat().st_size == 0:
        raise RuntimeError(f"SFINCS run completed without producing {map_nc_path}")
    print(f"[02_build_sfincs] Completed {scenario_id} -> {map_nc_path}")


def build_sfincs(
    config_path: Path,
    scenario_id: str | None = None,
    run_all: bool = False,
    drainage_rate_mm_hr: float | None = None,
    drainage_tier: str | None = None,
    quadtree: bool | None = None,
    rain_source: str | None = None,
    amc_mode: str | None = None,
    mean_rate_matched: bool | None = None,
    runs_dir: Path | None = None,
    skip_run: bool = False,
    force: bool = False,
) -> None:
    """Generate and optionally run SFINCS models from the scenario matrix."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    scenarios = resolve_scenarios(cfg, scenario_id=scenario_id, run_all=run_all)
    effective_drainage_mm_hr = (
        float(drainage_rate_mm_hr)
        if drainage_rate_mm_hr is not None
        else float(cfg["sfincs_model"]["drainage_rate_mm_hr"])
    )

    for scenario in scenarios:
        _build_and_run_single_scenario(
            cfg=cfg,
            scenario=scenario,
            drainage_rate_mm_hr=effective_drainage_mm_hr,
            skip_run=skip_run,
            force=force,
            drainage_tier_override=drainage_tier,
            quadtree_override=quadtree,
            rain_source_override=rain_source,
            amc_mode_override=amc_mode,
            mean_rate_matched_override=mean_rate_matched,
            runs_dir_override=runs_dir,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and run HydroMT-SFINCS models.")
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
        help="Scenario ID to build/run (defaults to project.baseline_scenario).",
    )
    parser.add_argument(
        "--all-scenarios",
        action="store_true",
        help="Build and run all scenarios defined in configs.",
    )
    parser.add_argument(
        "--drainage-rate-mm-hr",
        type=float,
        default=None,
        help="Optional override for uniform urban drainage subtraction rate (mm/hr).",
    )
    parser.add_argument(
        "--drainage-tier",
        type=str,
        choices=["tier0", "tier1a", "tier1b", "tier2"],
        default=None,
        help="Optional override for storm-drain abstraction tier.",
    )
    parser.add_argument(
        "--quadtree",
        action="store_true",
        default=None,
        help="Force-enable SFINCS quadtree mesh refinement.",
    )
    parser.add_argument(
        "--rain-source",
        type=str,
        choices=["default", "open_meteo", "mrms"],
        default=None,
        help="Optional override for rainfall forcing source ('default', 'open_meteo', or 'mrms').",
    )
    parser.add_argument(
        "--amc-mode",
        type=str,
        choices=["amc2", "auto", "amc3"],
        default=None,
        help="Antecedent soil moisture mode ('amc2', 'auto' via 5-day MRMS > 28 mm, or 'amc3').",
    )
    parser.add_argument(
        "--mean-rate-matched",
        action="store_true",
        default=None,
        help="Normalize Tier 1a/1b urban land drainage to match drainage_rate_mm_hr.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Optional override for SFINCS runs directory.",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Write SFINCS model files without executing the Docker solver.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild and re-run even if sfincs_map.nc already exists.",
    )
    args = parser.parse_args()
    build_sfincs(
        config_path=args.config,
        scenario_id=args.scenario,
        run_all=args.all_scenarios,
        drainage_rate_mm_hr=args.drainage_rate_mm_hr,
        drainage_tier=args.drainage_tier,
        quadtree=args.quadtree,
        rain_source=args.rain_source,
        amc_mode=args.amc_mode,
        mean_rate_matched=args.mean_rate_matched,
        runs_dir=args.runs_dir,
        skip_run=args.skip_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
