"""Export SFINCS max flood depth grids to EPSG:3857 Cloud Optimized GeoTIFFs (COGs)."""

from __future__ import annotations

import argparse
from pathlib import Path
import yaml


def export_cog(config_path: Path, scenario_id: str | None = None) -> None:
    """Reproject SFINCS zsmax/hmax outputs to EPSG:3857 and write COGs."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = cfg.get("paths", {}).get("cog_output_dir", "web/public/cogs")
    out_crs = cfg.get("project", {}).get("output_crs", "EPSG:3857")
    print(f"[04_export_cog] Target CRS: {out_crs}, output dir: {out_dir}")
    if scenario_id:
        print(f"[04_export_cog] Filtering to scenario: {scenario_id}")
    # TODO: Read sfincs_map.nc with rioxarray, mask dry cells, reproject to EPSG:3857, write COG
    print("[04_export_cog] Stub complete (no COGs exported).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SFINCS outputs to EPSG:3857 COGs.")
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
        help="Optional single scenario ID to export.",
    )
    args = parser.parse_args()
    export_cog(args.config, args.scenario)


if __name__ == "__main__":
    main()
