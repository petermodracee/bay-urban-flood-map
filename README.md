# bay-urban-flood-map

An open-source urban stormwater and compound sea level rise (SLR) flood model and serverless web viewer for the San Francisco Bay Area, built with **HydroMT-SFINCS** and **MapLibre GL JS + Cloud Optimized GeoTIFFs (COGs)**.

**Live Viewer:** https://bay-urban-flood-map.pages.dev

**Status:** `v0.6.1` complete ([`docs/plan/PLAN_V06.md`](docs/plan/PLAN_V06.md), [`docs/reports/sf_v06_calibration_report.md`](docs/reports/sf_v06_calibration_report.md), [`docs/reports/sf_v061_rescore_report.md`](docs/reports/sf_v061_rescore_report.md), [`docs/reports/sf_v061_phase3_report.md`](docs/reports/sf_v061_phase3_report.md)) — upgraded 3D web viewer with Tier A–E validation overlays, reproducible Phase 2A terrain QA, anti-overfitting Phase 2B calibration across 5 Atmospheric Rivers (`2,071` 311 points) + SFPUC 100-yr, full 18-scenario `v0.6` `EPSG:3857` COG export (`manifest.json`), `v0.6.1` methodology audit/rescore & sensitivity analyses (drainage-rate sweep, Atlas 14 temporal distribution, SFPUC `wppz-u2hi` elevations, right-of-way controls, income stratification), and public (`web/about.html`) + technical (`web/methodology.html`) pages. Next up: `v0.6` Phase 5 (Cloudflare R2/Pages publish & Zenodo DOIs), followed by `v0.7` San Francisquito Creek / East Palo Alto separate-MS4 pilot.

> **Note:** This is a screening-level 2D overland pooling and coastal-compound flood model grounded in peer-reviewed literature and gated by controlled A/B ablations (see [`docs/METHODS.md`](docs/METHODS.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md)). It is not a 1D parcel-level storm-pipe capacity model or a regulatory FEMA flood map.

## Citywide Scenarios (`configs/sf_citywide.yml`)

10-year (`90.4 mm`) and 100-year (`144.0 mm`) NOAA Atlas 14 coincident stress scenarios (15-minute alternating-block rainfall paired with MHHW + 0.35 m surge; undefined joint return period) crossed with fixed `0`, `+1`, `+3`, and `+5 ft` water-level increments mapped to **2024 California Ocean Protection Council (OPC)** guidance (NOAA SF gauge `9414290`, 2000 baseline), plus 4 published historical Atmospheric River hindcasts (**Oct 24, 2021**, **Dec 31, 2022**, **Jan 9, 2023**, and **Nov 22, 2024**, forced with NOAA MRMS 1 km QPE and observed NOAA tides):

| SLR Increment | 2024 CA OPC Mapping |
| :--- | :--- |
| **0 ft** (`0.00 m`) | Baseline (Present Day) |
| **+1 ft** (`0.30 m`) | 2050 Intermediate (0.8 ft) / Intermediate-High (1.0 ft) |
| **+3 ft** (`0.91 m`) | 2100 Intermediate (3.1 ft) |
| **+5 ft** (`1.52 m`) | 2100 Intermediate-High (4.8 ft) |

## Project Structure

```text
├── AGENTS.md                 # Coding agent invariants & workflow guardrails
├── CITATION.cff              # Citation metadata (Zenodo & GitHub "Cite this repository")
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
│   │   ├── sf_v05_multitier_validation.md # v0.5 5-tier open validation baseline report
│   │   ├── sf_v06_calibration_report.md   # v0.6 anti-overfitting calibration & holdout report
│   │   ├── sf_v061_rescore_report.md      # v0.6.1 rescore (RoW controls, SFPUC filter & wppz-u2hi, NFIP)
│   │   └── sf_v061_phase3_report.md       # v0.6.1 drainage-rate sweep & Atlas 14 TD sensitivity report
│   └── plan/
│       ├── ROADMAP.md        # Milestone index & future backlog
│       ├── PLAN_V01.md       # v0.1 ~20 km² SF slice checklist (complete)
│       ├── PLAN_V02.md       # v0.2 citywide SF compound SLR plan (complete)
│       ├── PLAN_V03.md       # v0.3 static COG hosting & deployment plan (complete)
│       ├── PLAN_V04.md       # v0.4 SF literature upgrades & ablation plan (complete)
│       ├── PLAN_V05.md       # v0.5 5-tier open validation layer (complete)
│       └── PLAN_V06.md       # v0.6 SF calibration, validation overlays, & R2 publish
├── configs/
│   ├── sf_citywide.yml       # Citywide SF (20 m / 2 m) + AR hindcast config
│   └── sf_benchmark.yml      # v0.1 ~20 km² SF benchmark slice config
├── pipeline/
│   ├── 00_fetch_data.py      # Fetch/cache DEM, NLCD, HSG, OSM, NOAA tides, & Tier C/D/E sources
│   ├── 01_prep_terrain.py    # Underpass carving, SFEI crests, roughness, & CN grids
│   ├── 02_build_sfincs.py    # HydroMT-SFINCS builder & Docker solver runner
│   ├── 03_validate_sf.py     # 5-tier open multi-source validation (Tiers A–E) + report generator
│   ├── 03_rescore_v061.py    # v0.6.1 reproducible rescoring of frozen v0.6 outputs
│   ├── 03_phase3_v061.py     # v0.6.1 drainage-rate sweep & Atlas 14 TD sensitivity runner
│   └── 04_export_cog.py      # Downscale, SLR deltas, GeoJSON, & EPSG:3857 COGs
├── web/                      # Vite + MapLibre GL viewer (index.html, about.html, methodology.html)
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
