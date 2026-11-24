# bay-urban-flood-map

An open-source urban stormwater and compound sea level rise (SLR) flood model and serverless web viewer for the San Francisco Bay Area, built with **HydroMT-SFINCS** and **MapLibre GL JS + Cloud Optimized GeoTIFFs (COGs)**.

**Live Viewer:** https://bay-urban-flood-map.pages.dev

**Status:** `v0.5` 5-tier open multi-source validation baseline complete ([`docs/reports/sf_v05_multitier_validation.md`](docs/reports/sf_v05_multitier_validation.md)) — live SF baseline scored across Tiers A–E (NOAA Presidio tides `NSE = 1.0`, SFPUC 100-yr `CSI = 0.1998`, DataSF DEM/SFFD Tier C `260` pts, DataSF 311 `1,535` pts + Groundsource `37/37` sub-basins, OpenFEMA NFIP `259` tracts / `92` claims) on top of the frozen `v0.4` model ([`docs/reports/sf_v02_vs_v04_ablation.md`](docs/reports/sf_v02_vs_v04_ablation.md)) and live `v0.3` Cloudflare R2 + Pages viewer. Next up: `v0.6` SF multi-tier calibration audit and `v0.7` San Francisquito Creek / East Palo Alto separate-MS4 pilot.

> **Note:** This is a screening-level 2D overland pooling and coastal-compound flood model grounded in peer-reviewed literature and gated by controlled A/B ablations (see [`docs/METHODS.md`](docs/METHODS.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md)). It is not a 1D parcel-level storm-pipe capacity model or a regulatory FEMA flood map.

## Citywide Scenarios (`configs/sf_citywide.yml`)

10-year (`90.4 mm`) and 100-year (`144.0 mm`) NOAA Atlas 14 design storms crossed with fixed `0`, `+1`, `+3`, and `+5 ft` water-level increments mapped to **2024 California Ocean Protection Council (OPC)** guidance (NOAA SF gauge `9414290`, 2000 baseline), plus the **Dec 31, 2022 Atmospheric River** (`128.5 mm` / 48 hr) hindcast:

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
│   ├── METHODS.md            # Scientific methodology, equations, limitations, & DOIs
│   ├── MODEL_SPEC.md         # Operational pipeline spec, datums, & domain traps
│   ├── VALIDATION_SPEC.md    # 5-tier open validation matrix & anti-overfitting rules
│   ├── DATA_CONTRACTS.md     # Shared COG, vector, & manifest schemas
│   ├── DATA_SOURCES.md       # Upstream dataset licenses & attribution
│   ├── DECISIONS.md          # Architecture & calibration design rationale
│   ├── WEB_SPEC.md           # MapLibre frontend & UI specification
│   ├── reports/
│   │   ├── sf_v02_validation.md           # v0.2 citywide SFPUC & 311 validation report
│   │   ├── sf_v02_vs_v04_ablation.md      # v0.4 controlled ablation & MRMS ensemble report
│   │   └── sf_v05_multitier_validation.md # v0.5 5-tier open validation baseline report
│   └── plan/
│       ├── ROADMAP.md        # Milestone index & future backlog
│       ├── PLAN_V01.md       # v0.1 ~20 km² SF slice checklist (complete)
│       ├── PLAN_V02.md       # v0.2 citywide SF compound SLR plan (complete)
│       ├── PLAN_V03.md       # v0.3 static COG hosting & deployment plan (complete)
│       ├── PLAN_V04.md       # v0.4 SF literature upgrades & ablation plan (complete)
│       └── PLAN_V05.md       # v0.5 5-tier open validation layer (complete)
├── configs/
│   ├── sf_citywide.yml       # Citywide SF (20 m / 2 m) + AR hindcast config
│   └── sf_benchmark.yml      # v0.1 ~20 km² SF benchmark slice config
├── pipeline/
│   ├── 00_fetch_data.py      # Fetch/cache DEM, NLCD, HSG, OSM, NOAA tides, & Tier C/D/E sources
│   ├── 01_prep_terrain.py    # Underpass carving, SFEI crests, roughness, & CN grids
│   ├── 02_build_sfincs.py    # HydroMT-SFINCS builder & Docker solver runner
│   ├── 03_validate_sf.py     # 5-tier open multi-source validation (Tiers A–E) + report generator
│   └── 04_export_cog.py      # Downscale, SLR deltas, GeoJSON, & EPSG:3857 COGs
├── web/                      # Vite + MapLibre GL + maplibre-cog-protocol viewer
├── data/                     # Local raw/processed/runs/web outputs (git-ignored)
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

python pipeline/00_fetch_data.py --config configs/sf_citywide.yml
python pipeline/01_prep_terrain.py --config configs/sf_citywide.yml
python pipeline/02_build_sfincs.py --config configs/sf_citywide.yml --all-scenarios
python pipeline/03_validate_sf.py --config configs/sf_citywide.yml
python pipeline/04_export_cog.py --config configs/sf_citywide.yml --all-scenarios
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

Test, lint, and build:
```bash
cd web
node --test src/*.test.js
npm run lint
npm run build
```

Exported `EPSG:3857` COGs (e.g., `sf_citywide_100yr_slr0ft_depth_3857.tif`, `sf_citywide_100yr_slr5ft_delta_3857.tif`), GeoJSON overlays, and `manifest.json` are written to `data/web_outputs/` per [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md).

## License & Attribution

- **Code:** Repository source code (`pipeline/`, `web/`, `configs/`, `deploy/`) is licensed under the **Apache License, Version 2.0** ([`LICENSE`](LICENSE)).
- **Upstream & Derived Geospatial Data:** Input datasets and derived flood rasters remain subject to their respective upstream licenses and attribution terms in [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md), including **SFEI SF Bay Shore Inventory** (`GNU GPL v3.0`), **OpenStreetMap** (`ODbL 1.0`, © OpenStreetMap contributors), **Open-Meteo** & **Google Research Groundsource** (`CC BY 4.0`), **DataSF / SFPUC / SF 311 / SF DEM / SFFD** (`Open Data Commons PDDL`), **USGS 3DEP / NLCD**, **USDA NRCS SSURGO**, **OpenFEMA NFIP**, and **NOAA CO-OPS / Atlas 14 / MRMS** (Public Domain).
