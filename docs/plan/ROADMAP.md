# ROADMAP.md — Milestones & Future Planning Index

This directory (`docs/plan/`) tracks active milestone checklists, completed plans, and upcoming roadmap work.

## Milestone Plans
| Version | Status | Plan Document | Scope |
| :--- | :--- | :--- | :--- |
| **v0.1** | Complete | [PLAN_V01.md](./PLAN_V01.md) | ~20 km² SF Mission/Alemany vertical slice (`10 m` grid / `1 m` subgrid), SFPUC 100-yr validation (`validation_summary.json`), `EPSG:3857` COG export, local MapLibre viewer. |
| **v0.2** | Complete | [PLAN_V02.md](./PLAN_V02.md) | Full San Francisco citywide model (`20 m` grid / `2 m` subgrid), SFEI shoreline structures, dynamic NOAA `9414290` tidal BCs (`+0/+1/+3/+5 ft` SLR), Dec 31, 2022 Atmospheric River hindcast + 311 validation, derived SLR delta COGs & upgraded viewer. |
| **v0.3** | Complete | [PLAN_V03.md](./PLAN_V03.md) | **Public Static Deployment & Hosting:** Upload `data/web_outputs/` (`*.tif` COGs, GeoJSON, `manifest.json`) to Cloudflare R2 (or S3) with HTTP `Range` + CORS headers; deploy `web/` to Cloudflare Pages / GitHub Pages with `VITE_DATA_BASE_URL` support. |
| **v0.4** | Complete | [PLAN_V04.md](./PLAN_V04.md) | **SF Literature-Backed Methodology Upgrades & Ablation:** Implemented & ablated **multi-gauge Bay tides** (*Nederhoff 2021*), **Tier 1a/1b/2 2D drainage** (`netamprfile`, *Bates 2021*), **4-AR hindcast ensemble** (*Eilander 2023*), **quadtree refinement** (*Leijnse 2021*), and **NOAA MRMS 1 km gridded QPE** on San Francisco ([`sf_v02_vs_v04_ablation.md`](../reports/sf_v02_vs_v04_ablation.md)). |
| **v0.5** | Planned | TBD (`PLAN_V05.md`) | **East/South Bay Separate-MS4 Expansion Pilot:** Apply the validated v0.4 pipeline to a flatland, bay-margin MS4 watershed (e.g., Oakland Coliseum/Lion Creek or San Francisquito Creek), handle pumped polders (SFINCS `pump` structures/masks), test optional Tier 2 municipal overrides, and validate against historical AR 311/CHP/OneRain/USGS records. |
| **v1.0** | Planned | TBD (`PLAN_V10.md`) | **Regional Bay Area Mosaic:** Batch-run ~100–150 sub-basins across all 9 Bay Area counties and mosaic into regional COGs / PMTiles. |

## Backlog / Future TODOs
- **High-resolution SF pass (`10 m` grid / `1 m` subgrid):** Re-run full SF once v0.2 `20 m / 2 m` validation and drainage-lock deltas are verified.
- **Automated CI:** GitHub Actions workflow running `pytest`, `ruff check && ruff format --check`, `node --test`, `npm run lint`, and `npm run build` on pull requests.
- **Viewer UX enhancements:** Address/intersection geocoder search, neighborhood summary stats panel, and shareable URL state (`#scenario=...&lat=...`).
- **Three-tier progressive 2D drainage (`v0.4+`):** Tier 1 Bay-wide `(DEM - tide)` + NLCD net-rain grid (`netamprfile`) with optional Tier 2 `domain.storm_drain_source` municipal override and `drainage_tier` provenance in `manifest.json`.
- **Climate-adjusted rainfall sensitivity:** Optional future-precipitation multiplier experiments once historical baseline is published.
