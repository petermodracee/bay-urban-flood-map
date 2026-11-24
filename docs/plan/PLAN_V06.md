# PLAN_V06.md — SF Multi-Tier Calibration, Validation Overlays & Public Publishing Release

**Goal:** Build the upgraded `web/` viewer (including 3D Terrain toggle, shareable URL hash, historical AR selector, and validation overlays) first so a local **3D visual QA pass** can catch any artificial freeway dams, bad trench cuts, or shoreline notches *before* running the **v0.6** terrain fixes (**Phase 2A**), anti-overfitting physics calibration (**Phase 2B**, [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) §3.4), and full 12-scenario SLR export (**Phase 3**). Finish by building plain-English and technical methodology pages (**Phase 4**) and publishing the upgraded SF dataset + viewer to Cloudflare R2 & Pages with Zenodo Code/Dataset DOIs (**Phase 5**, `v0.6.0` Pre-release) before expanding to San Francisquito Creek in **v0.7**.

---

## Frozen v0.5 Five-Tier Baseline & Holdout Split (`sf_citywide`)

From [`docs/reports/sf_v05_multitier_validation.md`](../reports/sf_v05_multitier_validation.md) (frozen v0.4 physics: `20 m / 2 m`, Tier 0 `5.0 mm/hr`, 4-gauge Bay tides, NOAA MRMS 1 km QPE):

| Tier | Baseline Dataset | Frozen v0.5 SF Baseline Score |
| --- | --- | --- |
| **Tier A** | NOAA CO-OPS `9414290` (Presidio, 4 ARs) | `RMSE = 0.0013–0.0019 m`, `MAE = 0.0010–0.0015 m`, `NSE = 1.0`, `0.0 min` peak timing error |
| **Tier B** | SFPUC 100-Yr (`>= 0.1524 m`) | `HR = 0.3887 / 0.3900`, `FAR = 0.7086 / 0.7189`, `CSI = 0.1998` (v0.4) / `0.1953` (v0.5 snapshot) |
| **Tier C** | SF DEM `TRAFFIC HAZARD` + SFFD `Water Rescue` (`260` pts) | 4-AR `25 m` mean `33.22%` cap / `1.140x` lift / `0.5554` AUC (**3-AR cal mean:** `43.95%` / `1.338x` / `0.5755`) |
| **Tier D** | DataSF 311 (`1,535` pts) + Groundsource (`332` records) | 4-AR `25 m` mean `36.89%` cap / `1.249x` lift / `0.5886` AUC; Groundsource `37/37` sub-basins matched (`1.000` HR) |
| **Tier E** | OpenFEMA NFIP (`259` tracts, `92` claims, `\$1.54M` paid) | `claim_count` vs. `flooded_land_area_m2` $\rho = 0.2289$ ($p = 0.0002$); vs. `exposed_buildings` $\rho = 0.1087$ ($p = 0.0807$) |

### Frozen Holdout-Storm Split & Retention Gate
- **Calibration Storms (3 ARs, `1,394` 311 pts):** `ar_20211024` (`567` pts), `ar_20221231` (`424` pts), `ar_20230109` (`403` pts).
- **Untouched Holdout Set (2 ARs):** `ar_20240204` (`141` pts, **never tuned on**) + `ar_20241122` (Nov 22–23, 2024 AR, `8` Groundsource records, `25.897 km^2`).
- **Retention Gate:** Retain a Phase 2B change only if it (a) holds neutral or improves on the untouched holdout set, (b) improves **at least two independent tiers** without regressing SFPUC `CSI` below `0.1998`, and (c) clears the parsimony threshold (`>= +0.01` `ROC-AUC` or `>= +0.005` `CSI`).

---

## Phase 1: Validation Overlay Contracts, Web Viewer 3D Terrain & Visual QA (`04_export_cog.py` + `web/`)
- [ ] Confirm and update [`docs/DATA_CONTRACTS.md`](../DATA_CONTRACTS.md) with event-matched validation overlay GeoJSONs (`< 2 MB`, `EPSG:4326`: per-storm 311 points, Tier C road-closure points, Tier A NOAA/USGS gauge pins with observed vs. modeled peak `NAVD88`) and `manifest.json` `"version": "0.6"`.
- [ ] Export lightweight Tier A / Tier C / per-storm 311 GeoJSON overlays + `manifest.json` from existing v0.4/v0.5 outputs (`pipeline/04_export_cog.py`, no SFINCS re-run) so all overlays can be inspected locally during 3D QA.
- [ ] Add an optional **"3D Terrain" toggle** (`map.setTerrain` using free AWS Terrarium DEM tiles + `~55°` camera pitch, verifying COG raster draping in the browser).
- [ ] Add **Viewer UX & Metadata Upgrades**:
  - Replace hardcoded `"v0.2"` / `"Dec 31, 2022 (424)"` labels with live `manifest.json` metadata (`version`, `rain_source`, `drainage_tier`, dynamic report counts).
  - Add **shareable URL hash state** (`#storm=...&slr=...&metric=...&lat=...&lng=...&z=...`), **quick-jump hotspot chips** (Alemany, SOMA / Mission Bay, Bayview / Islais Creek, Sunset / Wawona), and a **bounded address/intersection search** (Nominatim/Photon) that flies to the location and queries depth.
- [ ] Group historical storms (`Oct 24, 2021`, `Dec 31, 2022`, `Jan 9, 2023`) in the storm selector, auto-swapping matching 311 + Tier C road-closure overlays and disabling the SLR slider for observed-tide hindcasts.
- [ ] Add crisp **Validation Overlay Toggles** in the control panel: Tier B (`SFPUC 100-Yr`), Tier D (`311 Flood Reports`), Tier C (`SF DEM Traffic Hazards & SFFD Water Rescues` — pluggable for Caltrans PeMS/CHP once available), and Tier A (`NOAA Tide Gauge` pin showing observed vs. modeled peak `NAVD88`). Keep coarse Groundsource polygons and tract-level NFIP out of the default street map.
- [ ] Run a local **3D visual QA pass** on existing SF COGs with Tier C + 311 + SFPUC overlays active and log any artificial freeway dams, bad trench cuts, or shoreline notches (with `lat, lon` / `#` URL and symptom) for Phase 2A remediation.

---

## Phase 2A: Reproducible Terrain QA Fixes & Re-Baselining (`pipeline/01_prep_terrain.py` / `configs/sf_citywide.yml`)
- [ ] Log Phase 1 3D visual QA findings (`lat, lon`, symptom) and v0.5 Tier C corridor/underpass misses into reproducible YAML controls in `configs/sf_citywide.yml` and `pipeline/01_prep_terrain.py` (`manual_underpass_cuts`, `carve_exclude_osm_ids`, `shoreline_crest_half_width_m`; never hand-edit `.tif`s).
- [ ] Re-run `100yr_slr0ft` + the 4 baseline ARs (`ar_20211024`, `ar_20221231`, `ar_20230109`, `ar_20240204`) under unchanged v0.4 physics on the fixed terrain, recording the clean **Phase 2A terrain control** across Tiers A–E (confirming zero regression before Phase 2B).

---

## Phase 2B: Anti-Overfitting Physics Calibration (`pipeline/02_build_sfincs.py`)
- [ ] Fetch the second untouched holdout storm (`ar_20241122` — Nov 22–23, 2024 AR) via MRMS QPE, NOAA CO-OPS `9414290`–`9414523`, DataSF 311, and Tier C emergency dispatches.
- [ ] Run **one-factor-at-a-time** ablations on top of the Phase 2A terrain control, evaluating the **3 calibration ARs + `100yr_slr0ft` first**:
  - **Candidate 1 — Antecedent Soil Moisture (`AMC-II -> AMC-III`, Diagnostic #1):** Fetch 5-day prior MRMS rainfall before each storm start; when 5-day prior rainfall exceeds the standard USDA NRCS dormant-season wet threshold (`> 28 mm`), apply $\text{CN}_{\text{III}} = \frac{23\,\text{CN}_{\text{II}}}{10 + 0.13\,\text{CN}_{\text{II}}}$ (NEH-630) while keeping `Tier 0` `5.0 mm/hr` unchanged.
  - **Candidate 2 — Mean-Rate-Matched `Tier 1a/1b` 2D Drainage (Diagnostic #2):** Scale 2D urban drainage by NLCD imperviousness (and optional `(DEM - tide)` attenuation for `Tier 1b`) while normalizing the domain-mean urban land abstraction rate to exactly `5.0 mm/hr` (fixing v0.4 Phase 2's `~8–9 mm/hr` over-abstraction).
- [ ] **Holdout & Retention Gate:** Evaluate only candidates that pass the 3-AR calibration gate (`>= 2` tiers improved, SFPUC `CSI >= 0.1998`, parsimony `>= +0.01` `ROC-AUC` or `>= +0.005` `CSI`) **once** on the untouched 2-AR holdout set (`ar_20240204` + `ar_20241122`). If both candidates pass individually, test their combination once; if neither clears the gate, retain Phase 2A terrain fixes + v0.4 physics as the frozen v0.6 defaults.
- [ ] Score all 5 tiers (tracking both NFIP `flooded_land_area_m2` and `exposed_buildings` per v0.5 Tier E findings); freeze winning v0.6 SF defaults and publish `docs/reports/sf_v06_calibration_report.md`.

---

## Phase 3: Final Full-Matrix Export (`pipeline/04_export_cog.py`)
- [ ] Run the full `10-yr` / `100-yr` × `+0/+1/+3/+5 ft` SLR matrix + derived deltas and well-validated historical AR hindcasts (`ar_20211024` Bomb Cyclone, `ar_20221231` NYE AR, `ar_20230109` AR; include `ar_20240204` / `ar_20241122` only if Phase 2B validates them) once on the frozen Phase 2A/2B terrain & physics.
- [ ] Re-export `EPSG:3857` COGs (`gdalinfo` verified), refreshed Tier A gauge peak WSE GeoJSON, and `manifest.json` (`"version": "0.6"`), then run a quick local 3D sanity-check on the final exports.

---

## Phase 4: Public & Technical Methodology Pages (`web/` + `docs/`)
- [ ] Add a plain-English **Public Methodology** modal/page linked from `web/` populated with final v0.6 metrics (visual workflow diagram, screening-level disclaimers, and Bay Area validation highlights).
- [ ] Add a **Technical Methodology** page/view synthesizing [`docs/METHODS.md`](../METHODS.md), [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md), [`docs/DECISIONS.md`](../DECISIONS.md), and [`docs/DATA_SOURCES.md`](../DATA_SOURCES.md) (governing equations, peer-reviewed DOIs, and v0.2–v0.6 ablation/calibration tables).
- [ ] Verify `web/` with `cd web && node --test src/*.test.js && npm run lint && npm run build`.

---

## Phase 5: Cloudflare R2 + Pages Publish, Zenodo DOIs & `v0.6.0` Release
- [ ] Upload refreshed `data/web_outputs/` (`*.tif` COGs, GeoJSON overlays, `manifest.json`) to Cloudflare R2 and verify live HTTP `Range` / CORS streaming on `https://bay-urban-flood-map.pages.dev`.
- [ ] Add `CITATION.cff` to the repo root, enable the GitHub → Zenodo integration before cutting `v0.6.0`, and badge the minted concept DOI in `README.md`.
- [ ] Publish a companion **Zenodo Dataset** (`CC BY 4.0`) containing only the final derived outputs (`data/web_outputs/*.tif` COGs, `manifest.json`, `validation_*.json`, and an `ATTRIBUTION.md` mirroring [`docs/DATA_SOURCES.md`](../DATA_SOURCES.md) — no raw SFEI or OSM vectors) and link both Code and Dataset DOIs in `README.md`.
- [ ] Cut `v0.6.0` (`--prerelease`) GitHub Release on `main` linking `docs/plan/PLAN_V06.md` and `docs/reports/sf_v06_calibration_report.md` (triggering the first permanent Zenodo DOI).

---

## Exit Criteria
- Local 3D visual QA pass completed and Phase 2A terrain fixes verified with zero regression before Phase 2B calibration.
- Anti-overfitting guardrails and frozen v0.5 holdout split verified in `docs/reports/sf_v06_calibration_report.md`.
- `pytest`, `gdalinfo`, and `web/` (`node --test`, `npm run lint`, `npm run build`) all pass.
- Public & technical methodology pages verified, upgraded SF COGs + validation overlays live on Cloudflare R2/Pages, tagged `v0.6.0` (`--prerelease`), and archived with Zenodo Code + `CC BY 4.0` Dataset DOIs.
