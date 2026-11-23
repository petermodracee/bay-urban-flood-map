# PLAN_V05.md — Multi-Source Validation Layer & East/South Bay Separate-MS4 Pilot

**Goal:** Build and verify the 5-tier open validation layer ([`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md)) on the already-completed **San Francisco (`sf_citywide`)** v0.4 outputs first, then apply the validated pipeline and multi-tier validator to the cross-county **San Francisquito Creek / East Palo Alto** separate-MS4 pilot (`configs/sfc_east_palo_alto.yml`) and add multi-region switching in `web/`.

## Pilot & Baseline Summary

| Domain | Role in v0.5 | Key Jurisdictions & Gauges | Grid & Datum |
| :--- | --- | --- | --- |
| **`sf_citywide`** | **Phase 1 Validation Calibration Domain:** Verify Tiers A–E against frozen v0.4 MRMS 4-AR ensemble + `100-yr` design storm before introducing new watershed physics | SFPUC, DataSF 311, NOAA `9414290` (Presidio), Caltrans US-101/I-280, OpenFEMA NFIP | `EPSG:26910` / `NAVD88`, `20 m` grid / `2 m` subgrid |
| **`sfc_east_palo_alto`** | **Phases 2–4 Expansion Pilot:** San Francisquito Creek / East Palo Alto / Palo Alto flatland Bay-margin separate-MS4 watershed | SFCJPA, Valley Water (Santa Clara), OneShoreline (San Mateo), NOAA `9414523` (Redwood City), USGS `11164500` (San Francisquito Creek at Stanford), US-101 corridor | `EPSG:26910` / `NAVD88`, `20 m` grid / `2 m` subgrid |

---

## Phase 0: Specs, Licensing & Planning Baseline
- [x] Land [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md), `docs/plan/PLAN_V05.md`, [`docs/DATA_SOURCES.md`](../DATA_SOURCES.md) validation entries, and [`docs/plan/ROADMAP.md`](ROADMAP.md) update.

---

## Phase 1: Build & Verify 5-Tier Open Validation Matrix on San Francisco (`sf_citywide`)
- [ ] Confirm and update [`docs/DATA_CONTRACTS.md`](../DATA_CONTRACTS.md) with the standardized multi-tier `validation_ensemble_metrics.json` contract.
- [ ] Refactor/generalize `pipeline/03_validate_sf.py` into a region-agnostic multi-tier validator (preserving exact SF v0.4 regression metrics) with pure, offline-tested scoring functions (`pytest`), and run all 5 tiers against existing `sf_citywide` v0.4 outputs:
  - **Tier A (Physical Ground Truth):** Verify NOAA CO-OPS `9414290` (Presidio) coastal water-level boundary check (`RMSE`, `MAE`, `Bias`, peak timing error) on SF, and implement & unit-test the USGS NWIS stream-gauge / STN HWM (`NAVD88`) scorer ready for Phase 2 creek gauges (`Grimley et al., 2025`; `Nederhoff et al., 2021`).
  - **Tier B (Authoritative Hydraulic Benchmarks):** Generalize SFPUC 100-Yr polygon-to-raster `HR / FAR / CSI` scoring into a pluggable benchmark interface; verify zero regression against SF v0.4 (`CSI = 0.1998`) (`Sanders et al., 2023`; `Schubert, Mach, & Sanders, 2024`).
  - **Tier C (Roadway Disruptions):** Fetch and score open Caltrans QuickMap / PeMS / CHP / 511 SF Bay roadway flood closures across SF (e.g., US-101 / I-280 Alemany / Cesar Chavez) against the 4-AR MRMS ensemble (`25 m / 50 m` road-snapped capture, lift vs. random road controls, `ROC-AUC`; `Praharaj et al., 2021`).
  - **Tier D (Civic & News Archives):** Pair the existing DataSF 311 4-AR ensemble (`1,535` pts) with **Google Research Groundsource** (`CC BY 4.0` GeoParquet, `area_km2 <= 25`) clipped to SF to verify storm discovery & sub-basin presence (`Agonafir et al., 2022`; `Lerma et al., 2025`).
  - **Tier E (Structural Loss Cross-Check):** Fetch **OpenFEMA NFIP Redacted Claims** (`FimaNfipClaims` API) aggregated by SF census tracts and compute Spearman $\rho$ against `sf_citywide_100yr_slr0ft` modeled building exposure (`Wing et al., 2020`).
- [ ] Record the San Francisco 5-tier validation baseline in `docs/reports/sf_v05_multitier_validation.md` so every tier is proven on known data before expanding to a new watershed.

---

## Phase 2: Regional Pipeline Generalization, San Francisquito Creek Domain & Pumped Polders
- [ ] Generalize `pipeline/00_fetch_data.py` for non-SF counties: fallback building footprints fetcher (OSM/Overture when no municipal Open Data URL is configured), regional USGS 3DEP / NLCD / SSURGO / SFEI South Bay shoreline structures, USGS NWIS `11164500` stage hydrographs, and regional NOAA Atlas 14 IDF support.
- [ ] Create `configs/sfc_east_palo_alto.yml` following hydrological watershed boundaries across the San Mateo / Santa Clara county line (`EPSG:26910`, `NAVD88`, `20 m / 2 m`, 4-gauge Bay tides anchored by `9414523`, MRMS 4-AR ensemble, NOAA Atlas 14 `10-yr` / `100-yr` × `+0/+1/+3/+5 ft` SLR).
- [ ] Fetch and hydro-enforce regional terrain (`00_fetch_data.py` → `01_prep_terrain.py`), including SFEI South Bay levees and US-101 / creek bridge hydro-enforcement.
- [ ] Implement **SFINCS `pump` structure support** (`sfincs.pump`) alongside `polder_mask_source` in `pipeline/02_build_sfincs.py` for levee-protected Bay-margin pumped basins, with offline `pytest` coverage.

---

## Phase 3: Separate-MS4 Drainage Ablation & Compound SLR Matrix (`sfc_east_palo_alto`)
- [ ] Run the 4-AR **MRMS** hindcast ensemble (`ar_20211024`, `ar_20221231`, `ar_20230109`, `ar_20240204` plus any Groundsource-flagged high-impact basin storms) and score using the Phase 1 5-tier validator (live Tier A USGS `11164500` stage/crest validation, Tier B SFCJPA/Valley Water/OneShoreline/FEMA benchmark, Tier C US-101/Caltrans closures, Tier D Groundsource/311, Tier E OpenFEMA NFIP).
- [ ] Run one-factor-at-a-time ablations enforcing the **mean-rate-matched guardrail**: `Tier 0` vs. mean-rate-matched `Tier 1a` vs. `Tier 1b` (tide-locked gravity attenuation) vs. optional `Tier 2` (`domain.storm_drain_source` municipal storm-drain GIS), plus an optional `--quadtree` check along San Francisquito Creek & US-101.
- [ ] Run the full `10-yr` / `100-yr` × `+0 / +1 / +3 / +5 ft` SLR compound matrix and compute derived SLR delta rasters.

---

## Phase 4: Multi-Region Export, Web Viewer Switcher & v0.5 Report
- [ ] Confirm and update [`docs/DATA_CONTRACTS.md`](../DATA_CONTRACTS.md) with backward-compatible multi-region `manifest.json` (`regions[]` with `id`, `label`, `bbox_wgs84`, `center`, `zoom`, `drainage_tier`, `scenarios[]`).
- [ ] Update `pipeline/04_export_cog.py` to export `sfc_*_3857.tif` COGs + regional overlays and generate the multi-region `manifest.json`.
- [ ] Add a **Region Selector** dropdown with smooth `fitBounds` / `flyTo` in `web/` to switch between San Francisco and San Francisquito Creek / East Palo Alto; verify with `cd web && npm run lint && npm run build`.
- [ ] Publish `docs/reports/v05_sfc_east_palo_alto_validation.md` summarizing 5-tier validation scores, MS4 drainage ablation findings, and v1.0 regional scaling readiness.

---

## Exit Criteria
- All 5 validation tiers are implemented as pure, offline-tested functions (`pytest` passes) and verified end-to-end on the completed San Francisco (`sf_citywide`) v0.4 outputs (`docs/reports/sf_v05_multitier_validation.md`).
- San Francisquito Creek / East Palo Alto 4-AR MRMS hindcast + SLR matrix are validated across all 5 tiers and documented in `docs/reports/v05_sfc_east_palo_alto_validation.md`.
- Exported `sfc_*_3857.tif` COGs pass `gdalinfo` (`EPSG:3857`, `LAYOUT=COG`, internal overviews) and `web/` builds cleanly with multi-region switching.
