# ROADMAP.md — Milestones & Future Planning Index

This directory (`docs/plan/`) tracks active milestone checklists, completed plans, and upcoming roadmap work.

## Milestone Plans
| Version | Status | Plan Document | Scope |
| :--- | :--- | :--- | :--- |
| **v0.1** | Complete | [PLAN_V01.md](./PLAN_V01.md) | ~20 km² SF Mission/Alemany vertical slice (`10 m` grid / `1 m` subgrid), SFPUC 100-yr validation (`validation_summary.json`), `EPSG:3857` COG export, local MapLibre viewer. |
| **v0.2** | Complete | [PLAN_V02.md](./PLAN_V02.md) | Full San Francisco citywide model (`20 m` grid / `2 m` subgrid), SFEI shoreline structures, dynamic NOAA `9414290` tidal BCs (`+0/+1/+3/+5 ft` SLR), Dec 31, 2022 Atmospheric River hindcast + 311 validation, derived SLR delta COGs & upgraded viewer. |
| **v0.3** | Complete | [PLAN_V03.md](./PLAN_V03.md) | **Public Static Deployment & Hosting:** Upload `data/web_outputs/` (`*.tif` COGs, GeoJSON, `manifest.json`) to Cloudflare R2 (or S3) with HTTP `Range` + CORS headers; deploy `web/` to Cloudflare Pages / GitHub Pages with `VITE_DATA_BASE_URL` support. |
| **v0.4** | Complete | [PLAN_V04.md](./PLAN_V04.md) | **SF Literature-Backed Methodology Upgrades & Ablation:** Implemented & ablated **multi-gauge Bay tides** (*Nederhoff 2021*), **Tier 1a/1b/2 2D drainage** (`netamprfile`, *Bates 2021*), **4-AR hindcast ensemble** (*Eilander 2023*), **quadtree refinement** (*Leijnse 2021*), and **NOAA MRMS 1 km gridded QPE** on San Francisco ([`sf_v02_vs_v04_ablation.md`](../reports/sf_v02_vs_v04_ablation.md)). |
| **v0.5** | Complete | [PLAN_V05.md](./PLAN_V05.md) | **5-Tier Open Multi-Source Validation Layer (SF Baseline):** Built, unit-tested, and live baseline-scored Tiers A–E ([`VALIDATION_SPEC.md`](../VALIDATION_SPEC.md)) on completed SF v0.4 outputs ([`sf_v05_multitier_validation.md`](../reports/sf_v05_multitier_validation.md)) with zero model physics changes. |
| **v0.6** | In Progress (Phases 1–3 complete) | [PLAN_V06.md](./PLAN_V06.md) | **SF Multi-Tier Calibration, Validation Overlays & Public Publishing Release:** Upgraded 3D `web/` viewer + Tier A–E overlays (Phase 1), reproducible Phase 2A terrain QA, anti-overfitting Phase 2B calibration ([`sf_v06_calibration_report.md`](../reports/sf_v06_calibration_report.md)), and full 18-scenario `v0.6` COG export (Phase 3) complete; methodology pages (Phase 4) and Cloudflare R2/Pages + Zenodo DOIs (Phase 5) remain. |
| **v0.7** | Planned | TBD (`PLAN_V07.md`) | **San Francisquito Creek / East Palo Alto Separate-MS4 Pilot:** Zero-shot spatial transfer baseline, regional `00_fetch_data.py` generalization, SFINCS `pump` structures, rate-matched Tier 1a/1b/2 MS4 ablation, live Tier A USGS `11164500` validation, and multi-region `web/` switcher. |
| **v1.0** | Planned | TBD (`PLAN_V10.md`) | **Urban Bay Core Mosaic:** Batch-run ~60–90 urban & Bay-margin sub-basins across SF, San Mateo, Santa Clara, Alameda, and West Contra Costa (~80–85% of Bay Area population) using the unified rain-on-grid + Bay tides + SFEI levees + pumped-polder pipeline, and mosaic into regional COGs / PMTiles. |

## Backlog / Future TODOs
- **High-resolution SF pass (`10 m` grid / `1 m` subgrid):** Re-run full SF once v0.2 `20 m / 2 m` validation and drainage-lock deltas are verified.
- **Automated CI:** GitHub Actions workflow running `pytest`, `ruff check && ruff format --check`, `node --test`, `npm run lint`, and `npm run build` on pull requests.
- **Viewer UX enhancements (`v0.7+`):** Neighborhood summary stats panel and SFEI shoreline/levee crest overlay toggle (address/intersection search, hotspot chips, and shareable URL state are scheduled in `v0.6` via [`PLAN_V06.md`](./PLAN_V06.md)).
- **Three-tier progressive 2D drainage (`v0.4+`):** Tier 1 Bay-wide `(DEM - tide)` + NLCD net-rain grid (`netamprfile`) with optional Tier 2 `domain.storm_drain_source` municipal override and `drainage_tier` provenance in `manifest.json`.
- **Caltrans PeMS / 511 SF Bay Tier C provider (`v0.6`/`v0.7`):** Add a pluggable **Caltrans PeMS historical CHP incident CSV** (and/or 511 SF Bay) Tier C provider once the PeMS portal upgrade completes, for Bay-wide highway closure validation alongside the existing SF DataSF emergency-dispatch provider.
- **Climate-adjusted rainfall sensitivity:** Optional future-precipitation multiplier experiments once historical baseline is published.
- **Experimental 48-hr forward flood forecasting (`v0.8+`):** Swap MRMS QPE for **NOAA HRRR** (`3 km`, 0–48 hr) or **NBM** (`2.5 km`, 1–7 days; 50th/90th percentile QPF uncertainty bands) via the existing `netamprfile` pipeline, paired with **NOAA CO-OPS predicted tides / SFBOFS storm surge** and 5-day prior MRMS antecedent soil moisture (`AMC-III` / `wet` CN) for scheduled pre-storm street-ponding outlooks (explicitly framed as experimental screening, not official NWS warnings).
- **North Bay & Delta River Basins (`v1.1+ / v2.0`):** Expand beyond the `v1.0` Urban Bay Core to Napa, Sonoma, Solano, and East Contra Costa, adding upstream fluvial discharge boundary conditions (`sfincs.dis`) and Delta levee inventories.
- **Contributor "How-To" Run Guide:** step-by-step walkthrough to set up `conda-forge` + Docker (`deltares/sfincs-cpu`), fetch/place manual inputs (e.g., SFEI Bay Shore Inventory), run `00_fetch_data.py` through `04_export_cog.py`, and preview exported COGs locally in `web/`. *(Note: the plain-English **Public Methodology** and **Technical Methodology** pages are scheduled in `v0.6` via [`PLAN_V06.md`](./PLAN_V06.md) Phase 4.)*

## Release Convention
Each completed milestone is tagged on `main` using clean SemVer (`v0.x.0`, marked `--prerelease` on GitHub until `v1.0.0`, no tag suffix) targeting the milestone's final merge commit, with release notes summarizing key validation metrics and linking the milestone plan and report:
```bash
gh release create v0.x.0 --target <merge_commit_sha> --prerelease --title "v0.x.0 — <Milestone Title>"
```
