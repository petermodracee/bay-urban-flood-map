# ROADMAP.md — Milestones & Future Planning Index

This directory (`docs/plan/`) tracks active milestone checklists, completed plans, and upcoming roadmap work.

## Milestone Plans
| Version | Status | Plan Document | Scope |
| :--- | :--- | :--- | :--- |
| **v0.1** | Complete | [PLAN_V01.md](./PLAN_V01.md) | ~20 km² SF Mission/Alemany vertical slice (`10 m` grid / `1 m` subgrid), SFPUC 100-yr validation (`validation_summary.json`), `EPSG:3857` COG export, local MapLibre viewer. |
| **v0.2** | Active (Planned) | [PLAN_V02.md](./PLAN_V02.md) | Full San Francisco citywide model (`20 m` grid / `2 m` subgrid), SFEI shoreline structures, dynamic NOAA `9414290` tidal BCs (`+0/+1/+3/+5 ft` SLR), Dec 31, 2022 Atmospheric River hindcast + 311 validation, derived SLR delta COGs & upgraded viewer. |
| **v0.3** | Planned | TBD (`PLAN_V03.md`) | **Public Static Deployment & Hosting:** Upload `data/web_outputs/` (`*.tif` COGs, GeoJSON, `manifest.json`) to Cloudflare R2 (or S3) with HTTP `Range` + CORS headers; deploy `web/` to Cloudflare Pages / GitHub Pages with `VITE_DATA_BASE_URL` support. |
| **v0.4** | Planned | TBD (`PLAN_V04.md`) | **East/South Bay Separate-MS4 Compound Pilot:** Select a bay-margin MS4 watershed (e.g., Oakland Coliseum/Lion Creek or San Francisquito Creek), implement **Tier 1 Bay-wide 2D drainage proxy** (`(DEM - tide)` + NLCD via `netamprfile`) with optional **Tier 2 municipal infrastructure overrides**, handle pumped polders, and validate against historical AR 311/CHP/OneRain/USGS records. |
| **v1.0** | Planned | TBD (`PLAN_V10.md`) | **Regional Bay Area Mosaic:** Batch-run ~100–150 sub-basins across all 9 Bay Area counties and mosaic into regional COGs / PMTiles. |

## Backlog / Future TODOs
- **High-resolution SF pass (`10 m` grid / `1 m` subgrid):** Re-run full SF once v0.2 `20 m / 2 m` validation and drainage-lock deltas are verified.
- **Automated CI:** GitHub Actions workflow running `pytest`, `ruff check && ruff format --check`, `node --test`, `npm run lint`, and `npm run build` on pull requests.
- **Viewer UX enhancements:** Address/intersection geocoder search, neighborhood summary stats panel, and shareable URL state (`#scenario=...&lat=...`).
- **Three-tier progressive 2D drainage (`v0.4+`):** Tier 1 Bay-wide `(DEM - tide)` + NLCD net-rain grid (`netamprfile`) with optional Tier 2 `domain.storm_drain_source` municipal override and `drainage_tier` provenance in `manifest.json`.
- **Climate-adjusted rainfall sensitivity:** Optional future-precipitation multiplier experiments once historical baseline is published.
