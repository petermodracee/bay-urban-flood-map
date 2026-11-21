# bay-urban-flood-map

An experimental open-source urban stormwater and compound sea level rise (SLR) flood model and serverless web viewer for the San Francisco Bay Area, built with **HydroMT-SFINCS** and **MapLibre GL JS + Cloud Optimized GeoTIFFs (COGs)**.

**Status:** `v0.1` in progress — building and validating a ~15 km² San Francisco benchmark against the **SFPUC 100-Year Storm Flood Risk Map** before scaling to Bay-margin MS4 watersheds.

> **Note:** This is an experimental 2D overland pooling and tidal-lock model. It is not a parcel-level storm-pipe capacity model or a regulatory FEMA flood map.

## Benchmark Scenarios (`configs/sf_benchmark.yml`)

10-year and 100-year historical design rain events crossed with fixed 0, +1, +3, and +5 ft water-level increments mapped to **2024 California Ocean Protection Council (OPC)** guidance (SF gauge, 2000 baseline):

| SLR Increment | 2024 CA OPC Mapping |
| :--- | :--- |
| **0 ft** (`0.00 m`) | Baseline (Present Day) |
| **+1 ft** (`0.30 m`) | 2050 Intermediate (0.8 ft) / Intermediate-High (1.0 ft) |
| **+3 ft** (`0.91 m`) | 2100 Intermediate (3.1 ft) |
| **+5 ft** (`1.52 m`) | 2100 Intermediate-High (4.8 ft) |

## Project Structure

```text
├── AGENTS.md                 # Coding agent invariants & workflow guardrails
├── docs/
│   ├── DECISIONS.md          # Architecture & design rationale
│   ├── MODEL_SPEC.md         # Hydrodynamic modeling, datums, & validation
│   ├── DATA_CONTRACTS.md     # Shared COG, vector, & manifest schemas
│   ├── WEB_SPEC.md           # MapLibre frontend & UI specification
│   └── plan/
│       ├── ROADMAP.md        # Milestone index & future backlog
│       ├── PLAN_V01.md       # v0.1 SF slice checklist (complete)
│       └── PLAN_V02.md       # v0.2 citywide SF compound SLR plan
├── configs/
│   └── sf_benchmark.yml      # Rain × SLR scenario matrix & domain config
├── pipeline/
│   ├── 01_prep_terrain.py    # DEM hydro-enforcement, roughness, & infiltration
│   ├── 02_build_sfincs.py    # HydroMT-SFINCS builder & Docker solver runner
│   ├── 03_validate_sf.py     # Validation against SFPUC 100-yr flood map
│   └── 04_export_cog.py      # Downscale, mask, & export EPSG:3857 COGs
├── web/                      # Vite + MapLibre GL + maplibre-cog-protocol viewer
├── data/                     # Local raw/processed/web outputs (git-ignored)
└── environment.yml           # Conda environment (conda-forge)
```

## Quick Start

### Prerequisites
- Conda / Mamba (`conda-forge`)
- Docker (for running `deltares/sfincs-cpu`)
- Node.js (for `web/`)

### Python Modeling Pipeline

```bash
conda env create -f environment.yml
conda activate bay-urban-flood-map

python pipeline/01_prep_terrain.py --config configs/sf_benchmark.yml
python pipeline/02_build_sfincs.py --config configs/sf_benchmark.yml
python pipeline/03_validate_sf.py --config configs/sf_benchmark.yml
python pipeline/04_export_cog.py --config configs/sf_benchmark.yml
```

Run tests and linter:
```bash
pytest
ruff check && ruff format --check
```

### Web Viewer

```bash
cd web
npm install
npm run dev
```

Lint and build:
```bash
cd web
npm run lint
npm run build
```

Exported `EPSG:3857` COGs (e.g., `sf_100yr_slr0ft_depth_3857.tif`) and `manifest.json` are written to `data/web_outputs/` per [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md).
