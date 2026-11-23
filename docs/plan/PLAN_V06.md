# PLAN_V06.md — SF Multi-Tier Calibration, Validation Overlays & Public Publishing Release

**Goal:** Build the upgraded `web/` viewer (including 3D Terrain toggle, shareable URL hash, historical AR selector, and validation overlays) first so a local **3D visual QA pass** can catch any artificial freeway dams, bad trench cuts, or shoreline notches *before* running the **v0.6** anti-overfitting calibration pass ([`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) §3.4) and full 12-scenario SLR export. Finish by adding the Public Methodology page and publishing the upgraded SF dataset + viewer to Cloudflare R2 & Pages (`v0.6.0` Pre-release) before expanding to San Francisquito Creek in **v0.7**.

---

## Phase 1: Web Viewer 3D Terrain, UX Upgrades & 3D Visual QA (`web/`)
- [ ] Add an optional **"3D Terrain" toggle** (`map.setTerrain` using free AWS Terrarium DEM tiles + `~55°` camera pitch, verifying COG raster draping in the browser).
- [ ] Add **Viewer UX & Metadata Upgrades**:
  - Replace hardcoded `"v0.2"` / `"Dec 31, 2022 (424)"` labels with live `manifest.json` metadata (`version`, `rain_source`, `drainage_tier`, dynamic report counts).
  - Add **shareable URL hash state** (`#storm=...&slr=...&metric=...&lat=...&lng=...&z=...`), **quick-jump hotspot chips** (Alemany, SOMA / Mission Bay, Bayview / Islais Creek, Sunset / Wawona), and a **bounded address/intersection search** (Nominatim/Photon) that flies to the location and queries depth.
- [ ] Group historical storms (`Oct 24, 2021`, `Dec 31, 2022`, `Jan 9, 2023`) in the storm selector, auto-swapping matching 311 + Tier C road-closure overlays and disabling the SLR slider for observed-tide hindcasts.
- [ ] Add crisp **Validation Overlay Toggles** in the control panel: Tier B (`SFPUC 100-Yr`), Tier D (`311 Flood Reports`), Tier C (`Caltrans / CHP Road Closures` with route/incident popups), and Tier A (`NOAA Tide Gauge` pin showing observed vs. modeled peak `NAVD88`). Keep coarse Groundsource polygons and tract-level NFIP out of the default street map.
- [ ] Run a local **3D visual QA pass** on existing SF COGs and log any artificial freeway dams, bad trench cuts, or shoreline notches (with `lat, lon` / `#` URL and symptom) for Phase 2 remediation.

---

## Phase 2: Reproducible Terrain QA Fixes & Anti-Overfitting Calibration (`pipeline/01_prep_terrain.py` / `02_build_sfincs.py`)
- [ ] Remediate Phase 1 3D QA findings reproducibly in `configs/sf_citywide.yml` and `pipeline/01_prep_terrain.py` (e.g., `manual_underpass_cuts`, `carve_exclude_osm_ids`, or `shoreline_crest_half_width_m`; never hand-edit `.tif`s).
- [ ] Run one-factor-at-a-time ablations on `sf_citywide` enforcing all five [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) §3.4 guardrails (temporal holdout ARs, cross-tier consensus across `>= 2` tiers, physical parameter bounds, parsimony threshold `>= +0.01` `ROC-AUC` or `>= +0.005` `CSI`):
  - Mean-rate-matched `Tier 1a/1b` 2D drainage vs. `Tier 0` (`5.0 mm/hr`).
  - 5-day prior MRMS antecedent soil moisture (`AMC-II -> AMC-III`) for moderate ARs (e.g., `ar_20240204`).
  - Tier C-surfaced bridge/underpass hydro-enforcement fixes.
- [ ] Retain only changes that pass holdout + cross-tier consensus; freeze v0.6 SF defaults and publish `docs/reports/sf_v06_calibration_report.md`.

---

## Phase 3: Final Full-Matrix Export & Data Contracts (`pipeline/04_export_cog.py`)
- [ ] Confirm and update [`docs/DATA_CONTRACTS.md`](../DATA_CONTRACTS.md) with event-matched validation overlay GeoJSONs (`< 2 MB`, `EPSG:4326`: per-storm 311 points, Tier C Caltrans/CHP road closures, Tier A NOAA/USGS gauge pins with observed vs. modeled peak `NAVD88`) and `manifest.json` `"version": "0.6"`.
- [ ] Run the full `10-yr` / `100-yr` × `+0/+1/+3/+5 ft` SLR matrix + derived deltas and well-validated historical AR hindcasts (`ar_20211024` Bomb Cyclone, `ar_20221231` NYE AR, `ar_20230109` AR; include `ar_20240204` only if Phase 2 improves its skill) once on the QA'd terrain & calibrated defaults, and export `EPSG:3857` COGs verified via `gdalinfo`.

---

## Phase 4: Public Methodology Page, Cloudflare R2 + Pages Publish & `v0.6.0` Release
- [ ] Add a concise **Public Methodology** modal/page linked from `web/` populated with final v0.6 metrics (plain-English overview, workflow diagram, screening-level disclaimers, validation highlights, and links to [`docs/METHODS.md`](../METHODS.md) & [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md)).
- [ ] Verify `web/` with `cd web && node --test src/*.test.js && npm run lint && npm run build`.
- [ ] Upload refreshed `data/web_outputs/` (`*.tif` COGs, GeoJSON overlays, `manifest.json`) to Cloudflare R2 and verify live HTTP `Range` / CORS streaming on `https://bay-urban-flood-map.pages.dev`.
- [ ] Add `CITATION.cff` to the repo root, enable the GitHub → Zenodo integration before cutting `v0.6.0`, and badge the minted concept DOI in `README.md`.
- [ ] Cut `v0.6.0` (`--prerelease`) GitHub Release on `main` linking `docs/plan/PLAN_V06.md` and `docs/reports/sf_v06_calibration_report.md` (triggering the first permanent Zenodo DOI).

---

## Exit Criteria
- Local 3D visual QA pass completed and any terrain artifacts fixed reproducibly in `configs/` + `01_prep_terrain.py`.
- Anti-overfitting guardrails verified in `docs/reports/sf_v06_calibration_report.md`.
- `pytest`, `gdalinfo`, and `web/` (`node --test`, `npm run lint`, `npm run build`) all pass.
- Upgraded SF COGs, historical AR hindcasts, validation overlays, and public methodology are live on Cloudflare R2/Pages and tagged `v0.6.0` (`--prerelease`).
