"""Validate SF benchmark output against the SFPUC 100-Year Storm Flood Risk Map."""

from __future__ import annotations

import argparse
from pathlib import Path
import yaml


def validate_sf(config_path: Path) -> None:
    """Compare modeled 100-yr baseline flood extent/depth against SFPUC reference."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    ref = cfg.get("domain", {}).get("validation_reference")
    print(f"[03_validate_sf] Reference dataset: {ref}")
    # TODO: Compute CSI / Hit Rate / False Alarm Ratio against SFPUC 100-Year Storm Flood Risk Map
    print("[03_validate_sf] Stub complete (no validation metrics computed).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate SF benchmark flood outputs.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/sf_benchmark.yml"),
        help="Path to scenario configuration YAML.",
    )
    args = parser.parse_args()
    validate_sf(args.config)


if __name__ == "__main__":
    main()
