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

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr
import yaml
from pyproj import CRS
from rasterio.transform import Affine, from_origin
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
    return_period_yr = int(rain_cfg["return_period_yr"])
    slr_ft_raw = float(slr_cfg["slr_ft"])
    slr_ft: int | float = (
        round(slr_ft_raw) if math.isclose(slr_ft_raw, round(slr_ft_raw)) else slr_ft_raw
    )

    if slr_ft == 0:
        label = f"{return_period_yr}-Yr Storm — Today (0 ft SLR)"
    else:
        label = f"{return_period_yr}-Yr Storm — +{slr_ft} ft SLR"

    return {
        "id": f"{scenario_id}_{metric}",
        "label": label,
        "storm": f"{return_period_yr}yr",
        "slr_ft": slr_ft,
        "metric": metric,
        "url": f"{url_prefix.rstrip('/')}/{cog_filename}",
    }


def build_manifest(
    entries: Sequence[Mapping[str, Any]], version: str = "0.1"
) -> dict[str, Any]:
    """Build the top-level manifest.json payload."""
    return {
        "version": str(version),
        "scenarios": [dict(entry) for entry in entries],
    }


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


def export_cog(config_path: Path, scenario_id: str | None = None) -> Path:
    """Mask dry cells, reproject to EPSG:3857 COG, and write benchmark GeoJSON + manifest."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    project = cfg["project"]
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    val_cfg = cfg["validation"]
    export_cfg = cfg["export"]

    project_crs = str(project["crs"])
    output_crs = str(project["output_crs"])
    assert_crs_equals(project_crs, "EPSG:26910", "config.project.crs")
    assert_crs_equals(output_crs, "EPSG:3857", "config.project.output_crs")

    build_mod = _load_pipeline_module("02_build_sfincs.py", "phase2_build_sfincs")
    target_scenario = build_mod.resolve_scenarios(cfg, scenario_id=scenario_id)[0]
    target_id = str(target_scenario["id"])

    depth_m, src_transform = _load_or_downscale_depth_m(cfg, target_id, project_crs)

    land_mask: np.ndarray | None = None
    if bool(export_cfg.get("mask_open_water", True)):
        hydro_dem_path = Path(tprep["hydro_dem_file"])
        with rasterio.open(hydro_dem_path) as dem_ds:
            assert_crs_equals(dem_ds.crs, project_crs, str(hydro_dem_path))
            dem_m = dem_ds.read(1).astype(np.float32)
            dem_nodata = float(dem_ds.nodata if dem_ds.nodata is not None else -9999.0)
        min_land_elev_m = float(val_cfg.get("min_land_elevation_m", 0.5))
        land_mask = (
            (dem_m != dem_nodata) & np.isfinite(dem_m) & (dem_m >= min_land_elev_m)
        )

    min_depth_m = float(export_cfg.get("min_depth_m", 0.05))
    nodata_value = float(export_cfg.get("nodata_value", -9999.0))
    masked_depth_m = mask_dry_cells(
        depth_m=depth_m,
        min_depth_m=min_depth_m,
        nodata_value=nodata_value,
        land_mask=land_mask,
    )

    web_out_dir = Path(cfg["paths"]["web_output_dir"])
    cog_filename = f"{target_id}_depth_3857.tif"
    cog_path = web_out_dir / cog_filename
    write_epsg3857_cog(
        masked_depth_m=masked_depth_m,
        src_transform=src_transform,
        src_crs=project_crs,
        output_cog_path=cog_path,
        output_crs=output_crs,
        nodata_value=nodata_value,
        blocksize=int(export_cfg.get("cog_blocksize", 256)),
    )

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

    url_prefix = str(export_cfg.get("web_data_url_prefix", "/data"))
    entries: list[dict[str, Any]] = []
    for sc in cfg.get("scenarios", []):
        sc_id = str(sc["id"])
        sc_cog = f"{sc_id}_depth_3857.tif"
        if sc_id == target_id or (web_out_dir / sc_cog).exists():
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

    manifest = build_manifest(
        entries, version=str(export_cfg.get("manifest_version", "0.1"))
    )
    manifest_path = Path(export_cfg["manifest_file"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    wet_cells = int(np.sum(masked_depth_m != np.float32(nodata_value)))
    print(
        f"[04_export_cog] Wrote COG ({wet_cells} wet cells >= {min_depth_m} m) -> {cog_path}"
    )
    print(
        f"[04_export_cog] Wrote benchmark GeoJSON ({geojson_path.stat().st_size / 1024:.1f} KB) "
        f"-> {geojson_path}"
    )
    print(
        f"[04_export_cog] Wrote manifest ({len(entries)} scenario(s)) -> {manifest_path}"
    )
    return cog_path


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
    args = parser.parse_args()
    export_cog(args.config, args.scenario)


if __name__ == "__main__":
    main()
