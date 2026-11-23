"""Export SFINCS flood depth grids to EPSG:3857 Cloud Optimized GeoTIFFs (COGs)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import rasterio  # must precede geopandas/pyogrio to avoid GDAL 3.10 double-free

# isort: split
import geopandas as gpd
import numpy as np
import xarray as xr
import yaml
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject
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


def mask_dry_cells(
    depth_m: np.ndarray,
    min_depth_m: float = 0.05,
    nodata_value: float = -9999.0,
    land_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Mask dry, trace (< min_depth_m), invalid, or open-water cells to nodata_value."""
    if min_depth_m < 0:
        raise ValueError(f"min_depth_m must be non-negative, got {min_depth_m}")

    depth_f32 = depth_m.astype(np.float32, copy=True)
    valid = np.isfinite(depth_f32) & (depth_f32 != np.float32(nodata_value))
    wet = valid & (depth_f32 >= np.float32(min_depth_m))
    if land_mask is not None:
        wet &= land_mask.astype(bool)

    return np.where(wet, depth_f32, np.float32(nodata_value)).astype(np.float32)


def compute_slr_delta_m(
    depth_slr_m: np.ndarray,
    depth_baseline_m: np.ndarray,
    nodata_value: float = -9999.0,
) -> np.ndarray:
    """Compute non-negative added flood depth (SLR - 0 ft baseline) preserving nodata."""
    if depth_slr_m.shape != depth_baseline_m.shape:
        raise ValueError(
            f"Shape mismatch between SLR {depth_slr_m.shape} and baseline {depth_baseline_m.shape}"
        )
    nd = np.float32(nodata_value)
    slr_f32 = depth_slr_m.astype(np.float32, copy=False)
    base_f32 = depth_baseline_m.astype(np.float32, copy=False)
    slr_valid = np.isfinite(slr_f32) & (slr_f32 != nd)
    base_valid = np.isfinite(base_f32) & (base_f32 != nd)
    base_clean = np.where(
        base_valid, np.maximum(np.float32(0.0), base_f32), np.float32(0.0)
    )
    return np.where(
        slr_valid,
        np.maximum(np.float32(0.0), slr_f32 - base_clean),
        nd,
    ).astype(np.float32)


def build_manifest_entry(
    scenario_cfg: Mapping[str, Any],
    rain_cfg: Mapping[str, Any],
    slr_cfg: Mapping[str, Any],
    cog_filename: str,
    metric: str = "depth",
    url_prefix: str = "/data",
) -> dict[str, Any]:
    """Build a scenario manifest entry matching docs/DATA_CONTRACTS.md Section 5."""
    scenario_id = str(scenario_cfg["id"])
    raw_rp = rain_cfg.get("return_period_yr")
    if raw_rp is not None:
        return_period_yr = int(raw_rp)
        storm = f"{return_period_yr}yr"
        storm_label = f"{return_period_yr}-Yr Storm"
    else:
        storm = str(scenario_cfg["rain"]).removeprefix("rain_")
        storm_label = str(rain_cfg.get("label", storm))

    slr_ft_raw = float(slr_cfg["slr_ft"])
    slr_ft: int | float = (
        round(slr_ft_raw) if math.isclose(slr_ft_raw, round(slr_ft_raw)) else slr_ft_raw
    )

    if metric == "delta":
        label = f"{storm_label} — +{slr_ft} ft SLR (Added Depth)"
    elif raw_rp is None:
        label = storm_label
    elif slr_ft == 0:
        label = f"{storm_label} — Today (0 ft SLR)"
    else:
        label = f"{storm_label} — +{slr_ft} ft SLR"

    entry: dict[str, Any] = {
        "id": f"{scenario_id}_{metric}",
        "label": label,
        "storm": storm,
        "slr_ft": slr_ft,
        "metric": metric,
        "url": f"{url_prefix.rstrip('/')}/{cog_filename}",
    }
    if slr_cfg.get("opc_2024_pathway") is not None:
        entry["opc_2024_pathway"] = str(slr_cfg["opc_2024_pathway"])
    if slr_cfg.get("opc_2024_horizon") is not None:
        entry["opc_2024_horizon"] = str(slr_cfg["opc_2024_horizon"])
    return entry


def build_manifest(
    entries: Sequence[Mapping[str, Any]],
    version: str = "0.1",
    drainage_tier: str | None = None,
) -> dict[str, Any]:
    """Build the top-level manifest.json payload."""
    payload: dict[str, Any] = {"version": str(version)}
    if drainage_tier is not None:
        payload["drainage_tier"] = str(drainage_tier)
    payload["scenarios"] = [dict(entry) for entry in entries]
    return payload


def export_benchmark_geojson(
    validation_ref_path: Path,
    bbox_utm: Sequence[float],
    project_crs: str,
    output_path: Path,
    simplify_tolerance_m: float = 1.0,
    max_size_bytes: int = 2 * 1024 * 1024,
) -> Path:
    """Clip, simplify, reproject to EPSG:4326, and write the SFPUC benchmark GeoJSON."""
    if not validation_ref_path.exists():
        raise FileNotFoundError(
            f"Validation reference not found: {validation_ref_path}"
        )
    assert_crs_equals(project_crs, "EPSG:26910", "project_crs")

    gdf = gpd.read_file(validation_ref_path)
    if gdf.crs is None:
        raise ValueError(f"{validation_ref_path} is missing CRS")

    gdf_utm = gdf.to_crs(project_crs)
    clipped = gpd.clip(gdf_utm, box(*bbox_utm)).copy()
    if clipped.empty:
        raise RuntimeError(f"No benchmark geometries intersect bbox {bbox_utm}")

    if simplify_tolerance_m > 0:
        clipped["geometry"] = clipped.geometry.simplify(
            simplify_tolerance_m, preserve_topology=True
        )
    clipped = clipped[~clipped.geometry.is_empty].copy()

    gdf_wgs84 = clipped.to_crs("EPSG:4326")
    assert_crs_equals(gdf_wgs84.crs, "EPSG:4326", str(output_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.geojson")
    gdf_wgs84.to_file(tmp_path, driver="GeoJSON")
    tmp_path.replace(output_path)

    size_bytes = output_path.stat().st_size
    if size_bytes >= max_size_bytes:
        raise RuntimeError(
            f"Exported GeoJSON {output_path} is {size_bytes} bytes (>= {max_size_bytes} limit)"
        )
    return output_path


def export_311_geojson(
    source_path: Path,
    bbox_utm: Sequence[float],
    project_crs: str,
    output_path: Path,
) -> Path:
    """Clip 311 flood reports to bbox_utm, reproject to EPSG:4326, and write GeoJSON."""
    if not source_path.exists():
        raise FileNotFoundError(f"311 flood reports not found: {source_path}")
    assert_crs_equals(project_crs, "EPSG:26910", "project_crs")

    gdf = gpd.read_file(source_path)
    if gdf.crs is None:
        raise ValueError(f"{source_path} is missing CRS")

    gdf_utm = gdf.to_crs(project_crs)
    clipped = gpd.clip(gdf_utm, box(*bbox_utm)).copy()
    keep_cols = [
        c
        for c in (
            "service_request_id",
            "requested_datetime",
            "service_subtype",
            "service_details",
            "address",
            "neighborhood",
        )
        if c in clipped.columns
    ]
    clipped = clipped[[*keep_cols, "geometry"]].copy()
    gdf_wgs84 = clipped.to_crs("EPSG:4326")
    assert_crs_equals(gdf_wgs84.crs, "EPSG:4326", str(output_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.geojson")
    gdf_wgs84.to_file(tmp_path, driver="GeoJSON")
    tmp_path.replace(output_path)
    return output_path


def write_epsg3857_cog(
    masked_depth_m: np.ndarray,
    src_transform: Affine,
    src_crs: Any,
    output_cog_path: Path,
    output_crs: str = "EPSG:3857",
    nodata_value: float = -9999.0,
    blocksize: int = 256,
) -> Path:
    """Reproject masked depth grid to EPSG:3857 and write a compliant Cloud Optimized GeoTIFF."""
    assert_crs_equals(src_crs, "EPSG:26910", "input depth grid")
    assert_crs_equals(output_crs, "EPSG:3857", "output COG CRS")

    output_cog_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        src_tif = tmpdir / "src_26910.tif"
        warped_tif = tmpdir / "warped_3857.tif"
        cog_tmp = tmpdir / "cog_3857.tif"

        profile = {
            "driver": "GTiff",
            "height": masked_depth_m.shape[0],
            "width": masked_depth_m.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": src_crs,
            "transform": src_transform,
            "nodata": nodata_value,
            "compress": "DEFLATE",
        }
        with rasterio.open(src_tif, "w", **profile) as dst:
            dst.write(masked_depth_m.astype(np.float32, copy=False), 1)

        subprocess.run(
            [
                "gdalwarp",
                "-t_srs",
                output_crs,
                "-r",
                "bilinear",
                "-srcnodata",
                str(nodata_value),
                "-dstnodata",
                str(nodata_value),
                "-ot",
                "Float32",
                "-overwrite",
                str(src_tif),
                str(warped_tif),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        subprocess.run(
            [
                "gdal_translate",
                "-of",
                "COG",
                "-co",
                "COMPRESS=DEFLATE",
                "-co",
                "PREDICTOR=3",
                "-co",
                f"BLOCKSIZE={int(blocksize)}",
                "-co",
                "OVERVIEWS=IGNORE_EXISTING",
                str(warped_tif),
                str(cog_tmp),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        atomic_tmp = output_cog_path.with_suffix(".tmp.tif")
        shutil.move(str(cog_tmp), str(atomic_tmp))
        atomic_tmp.replace(output_cog_path)

    with rasterio.open(output_cog_path) as verify_ds:
        assert_crs_equals(verify_ds.crs, output_crs, str(output_cog_path))
        if verify_ds.dtypes[0] != "float32":
            raise ValueError(f"Expected float32 COG, got {verify_ds.dtypes[0]}")
        if verify_ds.nodata != nodata_value:
            raise ValueError(f"Expected nodata={nodata_value}, got {verify_ds.nodata}")
        if min(verify_ds.height, verify_ds.width) > int(
            blocksize
        ) and not verify_ds.overviews(1):
            raise RuntimeError(f"COG {output_cog_path} is missing internal overviews")

    return output_cog_path


def _load_or_downscale_depth_m(
    cfg: Mapping[str, Any],
    scenario_id: str,
    project_crs: str,
) -> tuple[np.ndarray, Affine]:
    project = cfg["project"]
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    val_cfg = cfg["validation"]

    downscaled_path = Path(val_cfg["downscaled_depth_file"])
    if scenario_id == str(project["baseline_scenario"]) and downscaled_path.exists():
        with rasterio.open(downscaled_path) as ds:
            assert_crs_equals(ds.crs, project_crs, str(downscaled_path))
            return ds.read(1).astype(np.float32), ds.transform

    map_nc_path = Path(cfg["paths"]["runs_dir"]) / scenario_id / "sfincs_map.nc"
    if not map_nc_path.exists():
        raise FileNotFoundError(
            f"Neither {downscaled_path} nor {map_nc_path} exists for scenario '{scenario_id}'."
        )

    hydro_dem_path = Path(tprep["hydro_dem_file"])
    with rasterio.open(hydro_dem_path) as dem_ds:
        assert_crs_equals(dem_ds.crs, project_crs, str(hydro_dem_path))
        dem_m = dem_ds.read(1).astype(np.float32)
        dst_transform = dem_ds.transform
        dem_nodata = float(dem_ds.nodata if dem_ds.nodata is not None else -9999.0)

    with xr.open_dataset(map_nc_path) as ds:
        zsmax_da = ds["zsmax"]
        if "timemax" in zsmax_da.dims:
            zsmax_da = zsmax_da.isel(timemax=-1)
        zsmax_south_up = zsmax_da.values.astype(np.float32)

    bbox_utm = domain["bbox_epsg26910"]
    grid_res_m = float(domain["grid_resolution_m"])
    src_transform = from_origin(
        float(bbox_utm[0]), float(bbox_utm[3]), grid_res_m, grid_res_m
    )

    val_mod = _load_pipeline_module("03_validate_sf.py", "phase3_validate_sf")
    depth_m = val_mod.downscale_zsmax_to_depth_m(
        zsmax_south_up=zsmax_south_up,
        src_transform=src_transform,
        src_crs=project_crs,
        dem_m=dem_m,
        dst_transform=dst_transform,
        dst_crs=project_crs,
        dem_nodata=dem_nodata,
    )
    return depth_m, dst_transform


def _build_land_mask(
    cfg: Mapping[str, Any],
    project_crs: str,
) -> np.ndarray | None:
    export_cfg = cfg["export"]
    if not bool(export_cfg.get("mask_open_water", True)):
        return None

    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    val_cfg = cfg["validation"]
    hydro_dem_path = Path(tprep["hydro_dem_file"])
    with rasterio.open(hydro_dem_path) as dem_ds:
        assert_crs_equals(dem_ds.crs, project_crs, str(hydro_dem_path))
        dem_m = dem_ds.read(1).astype(np.float32)
        dst_transform = dem_ds.transform
        dem_nodata = float(dem_ds.nodata if dem_ds.nodata is not None else -9999.0)

    min_land_elev_m = float(val_cfg.get("min_land_elevation_m", 0.5))
    land_mask = (dem_m != dem_nodata) & np.isfinite(dem_m) & (dem_m >= min_land_elev_m)

    nlcd_src_raw = domain.get("nlcd_landcover_source")
    if nlcd_src_raw and Path(nlcd_src_raw).exists():
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
        land_mask = land_mask & (nlcd_arr != 11)

    return land_mask


def _export_masked_cog(
    depth_grid: np.ndarray,
    src_transform: Affine,
    project_crs: str,
    output_crs: str,
    cog_path: Path,
    land_mask: np.ndarray | None,
    min_depth_m: float,
    nodata_value: float,
    blocksize: int,
) -> int:
    masked = mask_dry_cells(
        depth_m=depth_grid,
        min_depth_m=min_depth_m,
        nodata_value=nodata_value,
        land_mask=land_mask,
    )
    write_epsg3857_cog(
        masked_depth_m=masked,
        src_transform=src_transform,
        src_crs=project_crs,
        output_cog_path=cog_path,
        output_crs=output_crs,
        nodata_value=nodata_value,
        blocksize=blocksize,
    )
    wet_cells = int(np.sum(masked != np.float32(nodata_value)))
    print(
        f"[04_export_cog] Wrote COG ({wet_cells} wet cells >= {min_depth_m} m) -> {cog_path}"
    )
    return wet_cells


def export_cog(
    config_path: Path,
    scenario_id: str | None = None,
    export_all: bool = False,
) -> Path:
    """Mask dry cells, reproject to EPSG:3857 COGs, and write GeoJSON overlays + manifest."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    project = cfg["project"]
    domain = cfg["domain"]
    export_cfg = cfg["export"]

    project_crs = str(project["crs"])
    output_crs = str(project["output_crs"])
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    assert_crs_equals(output_crs, "EPSG:3857", "config.project.output_crs")

    build_mod = _load_pipeline_module("02_build_sfincs.py", "phase2_build_sfincs")
    target_scenarios = build_mod.resolve_scenarios(
        cfg, scenario_id=scenario_id, run_all=export_all
    )
    target_ids = {str(sc["id"]) for sc in target_scenarios}

    land_mask = _build_land_mask(cfg, project_crs)
    min_depth_m = float(export_cfg.get("min_depth_m", 0.05))
    nodata_value = float(export_cfg.get("nodata_value", -9999.0))
    blocksize = int(export_cfg.get("cog_blocksize", 256))
    web_out_dir = Path(cfg["paths"]["web_output_dir"])

    depth_cache: dict[str, tuple[np.ndarray, Affine]] = {}
    baseline_by_rain: dict[str, np.ndarray] = {}
    first_cog_path: Path | None = None

    for sc in target_scenarios:
        sc_id = str(sc["id"])
        depth_m, src_transform = _load_or_downscale_depth_m(cfg, sc_id, project_crs)
        depth_cache[sc_id] = (depth_m, src_transform)

        slr_cfg = cfg["slr_increments"][sc["slr"]]
        if math.isclose(float(slr_cfg["slr_ft"]), 0.0):
            baseline_by_rain[str(sc["rain"])] = depth_m

        cog_path = web_out_dir / f"{sc_id}_depth_3857.tif"
        _export_masked_cog(
            depth_grid=depth_m,
            src_transform=src_transform,
            project_crs=project_crs,
            output_crs=output_crs,
            cog_path=cog_path,
            land_mask=land_mask,
            min_depth_m=min_depth_m,
            nodata_value=nodata_value,
            blocksize=blocksize,
        )
        if first_cog_path is None:
            first_cog_path = cog_path

    exported_deltas: set[str] = set()
    if export_all:
        for sc in target_scenarios:
            sc_id = str(sc["id"])
            rain_key = str(sc["rain"])
            slr_cfg = cfg["slr_increments"][sc["slr"]]
            if float(slr_cfg["slr_ft"]) <= 0.0 or rain_key not in baseline_by_rain:
                continue
            depth_m, src_transform = depth_cache[sc_id]
            delta_m = compute_slr_delta_m(
                depth_slr_m=depth_m,
                depth_baseline_m=baseline_by_rain[rain_key],
                nodata_value=nodata_value,
            )
            delta_cog_path = web_out_dir / f"{sc_id}_delta_3857.tif"
            _export_masked_cog(
                depth_grid=delta_m,
                src_transform=src_transform,
                project_crs=project_crs,
                output_crs=output_crs,
                cog_path=delta_cog_path,
                land_mask=land_mask,
                min_depth_m=min_depth_m,
                nodata_value=nodata_value,
                blocksize=blocksize,
            )
            exported_deltas.add(sc_id)

    geojson_path = Path(export_cfg["benchmark_geojson_file"])
    export_benchmark_geojson(
        validation_ref_path=Path(domain["validation_reference"]),
        bbox_utm=domain["bbox_epsg26910"],
        project_crs=project_crs,
        output_path=geojson_path,
        simplify_tolerance_m=float(
            export_cfg.get("benchmark_simplify_tolerance_m", 1.0)
        ),
    )
    print(
        f"[04_export_cog] Wrote benchmark GeoJSON ({geojson_path.stat().st_size / 1024:.1f} KB) "
        f"-> {geojson_path}"
    )

    ref_311_raw = domain.get("validation_311_reference")
    out_311_raw = export_cfg.get("floods_311_geojson_file")
    if ref_311_raw and out_311_raw and Path(ref_311_raw).exists():
        out_311_path = export_311_geojson(
            source_path=Path(ref_311_raw),
            bbox_utm=domain["bbox_epsg26910"],
            project_crs=project_crs,
            output_path=Path(out_311_raw),
        )
        print(
            f"[04_export_cog] Wrote 311 flood GeoJSON ({out_311_path.stat().st_size / 1024:.1f} KB) "
            f"-> {out_311_path}"
        )

    url_prefix = str(export_cfg.get("web_data_url_prefix", "/data"))
    entries: list[dict[str, Any]] = []
    for sc in cfg.get("scenarios", []):
        sc_id = str(sc["id"])
        sc_cog = f"{sc_id}_depth_3857.tif"
        if sc_id in target_ids or (web_out_dir / sc_cog).exists():
            entries.append(
                build_manifest_entry(
                    scenario_cfg=sc,
                    rain_cfg=cfg["rain_events"][sc["rain"]],
                    slr_cfg=cfg["slr_increments"][sc["slr"]],
                    cog_filename=sc_cog,
                    metric="depth",
                    url_prefix=url_prefix,
                )
            )
    for sc in cfg.get("scenarios", []):
        sc_id = str(sc["id"])
        delta_cog = f"{sc_id}_delta_3857.tif"
        if sc_id in exported_deltas or (web_out_dir / delta_cog).exists():
            entries.append(
                build_manifest_entry(
                    scenario_cfg=sc,
                    rain_cfg=cfg["rain_events"][sc["rain"]],
                    slr_cfg=cfg["slr_increments"][sc["slr"]],
                    cog_filename=delta_cog,
                    metric="delta",
                    url_prefix=url_prefix,
                )
            )

    manifest = build_manifest(
        entries,
        version=str(export_cfg.get("manifest_version", "0.1")),
        drainage_tier=str(cfg.get("sfincs_model", {}).get("drainage_tier", "tier0")),
    )
    manifest_path = Path(export_cfg["manifest_file"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"[04_export_cog] Wrote manifest ({len(entries)} scenario(s)) -> {manifest_path}"
    )
    assert first_cog_path is not None
    return first_cog_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export SFINCS outputs to EPSG:3857 COGs."
    )
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
        help="Optional single scenario ID to export (defaults to project.baseline_scenario).",
    )
    parser.add_argument(
        "--all-scenarios",
        action="store_true",
        help="Export all depth COGs, derived SLR delta COGs, and GeoJSON overlays.",
    )
    args = parser.parse_args()
    export_cog(args.config, args.scenario, export_all=args.all_scenarios)


if __name__ == "__main__":
    main()
