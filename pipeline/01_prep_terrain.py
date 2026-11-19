"""Prepare topobathy DEM, roughness, and urban drainage/building masks for SFINCS."""

from __future__ import annotations

import argparse
from pathlib import Path
import yaml


def prep_terrain(config_path: Path) -> None:
    """Load config and prepare terrain inputs for the SF benchmark domain."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    project = cfg.get("project", {})
    domain = cfg.get("domain", {})
    print(f"[01_prep_terrain] Project: {project.get('name')} (CRS: {project.get('crs')})")
    print(f"[01_prep_terrain] DEM source: {domain.get('dem_source')}")
    # TODO: Reproject/clip DEM to project CRS & vertical datum (NAVD88)
    # TODO: Burn culverts / MS4 trunk lines or enforce building footprints
    print("[01_prep_terrain] Stub complete (no terrain files written).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare terrain inputs for SFINCS.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/sf_benchmark.yml"),
        help="Path to scenario configuration YAML.",
    )
    args = parser.parse_args()
    prep_terrain(args.config)


if __name__ == "__main__":
    main()
