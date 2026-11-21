"""Build and run HydroMT-SFINCS model instances for storm x SLR scenarios."""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import rasterio
import yaml
from hydromt_sfincs import SfincsModel
from pyproj import CRS


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


def _build_and_run_single_scenario(
    cfg: Mapping[str, Any],
    scenario: Mapping[str, Any],
    drainage_rate_mm_hr: float,
    skip_run: bool,
    force: bool,
) -> None:
    scenario_id = str(scenario["id"])
    rain_key = str(scenario["rain"])
    slr_key = str(scenario["slr"])

    project = cfg["project"]
    domain = cfg["domain"]
    tprep = cfg["terrain_prep"]
    sfincs_cfg = cfg["sfincs_model"]
    runs_dir = Path(cfg["paths"]["runs_dir"])
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
    if float(slr_cfg.get("slr_m", 0.0)) > 0.0 and not slr_cfg.get(
        "tidal_hydrograph_file"
    ):
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

    hyetograph_rows = _read_hyetograph_csv(Path(rain_cfg["hyetograph_file"]))
    duration_hr = float(rain_cfg["duration_hr"])
    tref = datetime.fromisoformat(str(sfincs_cfg["reference_time_utc"])).replace(
        tzinfo=None
    )
    tstop = tref + timedelta(hours=duration_hr)

    precip_series = build_precip_series(
        hyetograph_rows=hyetograph_rows,
        reference_time_utc=tref,
        duration_hr=duration_hr,
        drainage_rate_mm_hr=drainage_rate_mm_hr,
    )

    run_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"[02_build_sfincs] Building {scenario_id}: {grid_spec['mmax']}x{grid_spec['nmax']} @ "
        f"{grid_spec['dx']} m (subgrid {grid_spec['nr_subgrid_pixels']}x), "
        f"drainage={drainage_rate_mm_hr:.1f} mm/hr -> {run_dir}"
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
        datasets_rgh=[{"manning": manning_n_path.resolve()}],
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

    inp_path = run_dir / "sfincs.inp"
    prp_path = run_dir / "sfincs.prp"
    if not inp_path.exists() or not prp_path.exists():
        raise RuntimeError(f"Expected SFINCS build files missing in {run_dir}")

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
        skip_run=args.skip_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
