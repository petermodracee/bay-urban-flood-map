"""Build HydroMT-SFINCS model instances for each rain x SLR scenario."""

from __future__ import annotations

import argparse
from pathlib import Path
import yaml


def build_sfincs(config_path: Path, scenario_id: str | None = None) -> None:
    """Generate SFINCS model directories from the scenario matrix."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    scenarios = cfg.get("scenarios", [])
    if scenario_id:
        scenarios = [s for s in scenarios if s["id"] == scenario_id]

    print(f"[02_build_sfincs] Preparing {len(scenarios)} scenario(s)...")
    for sc in scenarios:
        # TODO: Initialize SfincsModel via hydromt_sfincs, setup grid,forcing, and coastal BCs
        print(f"  - {sc['id']}: rain={sc['rain']}, slr={sc['slr']}")
    print("[02_build_sfincs] Stub complete (no SFINCS models built).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build HydroMT-SFINCS models.")
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
        help="Optional single scenario ID to build.",
    )
    args = parser.parse_args()
    build_sfincs(args.config, args.scenario)


if __name__ == "__main__":
    main()
