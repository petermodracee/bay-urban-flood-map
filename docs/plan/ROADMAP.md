# ROADMAP.md — Milestones & Future Planning Index

This directory (`docs/plan/`) tracks active milestone checklists, completed plans, and upcoming roadmap work.

## Milestone Plans
| Version | Status | Plan Document | Scope |
| :--- | :--- | :--- | :--- |
| **v0.1** | Complete | [PLAN_V01.md](./PLAN_V01.md) | ~20 km² SF Mission/Alemany vertical slice (`10 m` grid / `1 m` subgrid), SFPUC 100-yr validation (`validation_summary.json`), `EPSG:3857` COG export, local MapLibre viewer. |
| **v0.2** | Complete | [PLAN_V02.md](./PLAN_V02.md) | Full San Francisco citywide model (`20 m` grid / `2 m` subgrid), SFEI shoreline structures, dynamic NOAA `9414290` tidal BCs (`+0/+1/+3/+5 ft` SLR), Dec 31, 2022 Atmospheric River hindcast + 311 validation, derived SLR delta COGs & upgraded viewer. |
| **v0.3** | Complete | [PLAN_V03.md](./PLAN_V03.md) | **Public Static Deployment & Hosting:** Upload `data/web_outputs/` (`*.tif` COGs, GeoJSON, `manifest.json`) to Cloudflare R2 (or S3) with HTTP `Range` + CORS headers; deploy `web/` to Cloudflare Pages / GitHub Pages with `VITE_DATA_BASE_URL` support. |
| **v0.4** | Complete | [PLAN_V04.md](./PLAN_V04.md) | **SF Literature-Backed Methodology Upgrades & Ablation:** Implemented & ablated **multi-gauge Bay tides** (*Nederhoff 2021*), **Tier 1a/1b/2 2D drainage** (`netamprfile`, *Bates 2021*), **4-AR hindcast ensemble** (*Eilander 2023*), **quadtree refinement** (*Leijnse 2021*), and **NOAA MRMS 1 km gridded QPE** on San Francisco ([`sf_v02_vs_v04_ablation.md`](../reports/sf_v02_vs_v04_ablation.md)). |
| **v0.5** | In Progress | [PLAN_V05.md](./PLAN_V05.md) | **5-Tier Open Multi-Source Validation Layer (SF Baseline):** Build, unit-test, and baseline-score Tiers A–E ([`VALIDATION_SPEC.md`](../VALIDATION_SPEC.md)) on completed SF v0.4 outputs (`docs/reports/sf_v05_multitier_validation.md`) with zero model changes. |
| **v0.6** | Planned | TBD (`PLAN_V06.md`) | **SF Multi-Tier Diagnostic Audit & Calibration:** Use the v0.5 validator and anti-overfitting guardrails (temporal holdout ARs, cross-tier consensus, parsimony threshold) to test mean-rate-matched Tier 1 drainage, 5-day MRMS antecedent moisture (`AMC-III`), and Tier C underpass fixes; republish upgraded SF COGs to R2. |
| **v0.7** | Planned | TBD (`PLAN_V07.md`) | **San Francisquito Creek / East Palo Alto Separate-MS4 Pilot:** Zero-shot spatial transfer baseline, regional `00_fetch_data.py` generalization, SFINCS `pump` structures, rate-matched Tier 1a/1b/2 MS4 ablation, live Tier A USGS `11164500` validation, and multi-region `web/` switcher. |
| **v1.0** | Planned | TBD (`PLAN_V10.md`) | **Regional Bay Area Mosaic:** Batch-run ~100–150 sub-basins across all 9 Bay Area counties and mosaic into regional COGs / PMTiles. |

## Backlog / Future TODOs
- **High-resolution SF pass (`10 m` grid / `1 m` subgrid):** Re-run full SF once v0.2 `20 m / 2 m` validation and drainage-lock deltas are verified.
- **Automated CI:** GitHub Actions workflow running `pytest`, `ruff check && ruff format --check`, `node --test`, `npm run lint`, and `npm run build` on pull requests.
- **Viewer UX enhancements:** Address/intersection geocoder search, neighborhood summary stats panel, and shareable URL state (`#scenario=...&lat=...`).
- **Three-tier progressive 2D drainage (`v0.4+`):** Tier 1 Bay-wide `(DEM - tide)` + NLCD net-rain grid (`netamprfile`) with optional Tier 2 `domain.storm_drain_source` municipal override and `drainage_tier` provenance in `manifest.json`.
- **Climate-adjusted rainfall sensitivity:** Optional future-precipitation multiplier experiments once historical baseline is published.
- **Experimental 48-hr forward flood forecasting (`v0.8+`):** Swap MRMS QPE for **NOAA HRRR** (`3 km`, 0–48 hr) or **NBM** (`2.5 km`, 1–7 days; 50th/90th percentile QPF uncertainty bands) via the existing `netamprfile` pipeline, paired with **NOAA CO-OPS predicted tides / SFBOFS storm surge** and 5-day prior MRMS antecedent soil moisture (`AMC-III` / `wet` CN) for scheduled pre-storm street-ponding outlooks (explicitly framed as experimental screening, not official NWS warnings).
- **Public & Contributor Documentation Suite:**
  - **Public Methodology page** (linked from `web/`): plain-English explanation of rain-on-grid + tide-locked storm drain modeling, visual workflow diagram, screening-level disclaimers, and Bay Area validation highlights for general users.
  - **Technical Methodology reference:** web-readable synthesis of [`METHODS.md`](../METHODS.md), [`VALIDATION_SPEC.md`](../VALIDATION_SPEC.md), [`DECISIONS.md`](../DECISIONS.md), and [`DATA_SOURCES.md`](../DATA_SOURCES.md) with governing equations, peer-reviewed DOIs, and v0.2–v0.6 ablation results.
  - **Contributor "How-To" Run Guide:** step-by-step walkthrough to set up `conda-forge` + Docker (`deltares/sfincs-cpu`), fetch/place manual inputs (e.g., SFEI Bay Shore Inventory), run `00_fetch_data.py` through `04_export_cog.py`, and preview exported COGs locally in `web/`.

## Release Convention
Each completed milestone is tagged on `main` using clean SemVer (`v0.x.0`, marked `--prerelease` on GitHub until `v1.0.0`, no tag suffix) targeting the milestone's final merge commit, with release notes summarizing key validation metrics and linking the milestone plan and report:
```bash
gh release create v0.x.0 --target <merge_commit_sha> --prerelease --title "v0.x.0 — <Milestone Title>"
```
