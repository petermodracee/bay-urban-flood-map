# MODEL_SPEC.md — Hydrodynamic Modeling & Validation

Operational engineering specification for `pipeline/` and `configs/`. For full scientific rationale, equations, limitations, and peer-reviewed citations, see [`docs/METHODS.md`](METHODS.md).

## 1. Milestones
- **v0.1 (Complete):** ~20 km² San Francisco Mission/Alemany slice (`10 m` grid / `1 m` subgrid), validated against the SFPUC 100-Year Storm Flood Risk Map.
- **v0.2 (Complete):** Citywide San Francisco (`20 m` grid / `2 m` subgrid), SFEI shoreline crest enforcement, dynamic NOAA `9414290` tidal boundaries (`+0/+1/+3/+5 ft` SLR), Dec 31, 2022 AR hindcast + 3-way 311 validation, and derived SLR delta COGs.
- **v0.3 (Complete — see [`docs/plan/PLAN_V03.md`](plan/PLAN_V03.md)):** Static hosting of `data/web_outputs/` (`EPSG:3857` COGs, GeoJSON, `manifest.json`) on Cloudflare R2 + Cloudflare Pages.
- **v0.4 (Complete — see [`docs/plan/PLAN_V04.md`](plan/PLAN_V04.md) & [`docs/reports/sf_v02_vs_v04_ablation.md`](reports/sf_v02_vs_v04_ablation.md)):** Literature-backed upgrades on San Francisco (4-gauge Bay tides, Tier 1a/1b/2 2D drainage via `netamprfile`, 4-AR hindcast ensemble, `40 m -> 10 m` quadtree mesh via `hydromt_sfincs 2.0.0`, and NOAA MRMS 1 km gridded QPE) ablated against frozen v0.2. Retained defaults: 4-gauge Bay tides + NOAA MRMS 1 km QPE for historical ARs + uniform `20 m` Tier 0 (`5.0 mm/hr`), with Tier 1a/1b/2 and `--quadtree` opt-in.
- **v0.5 (Complete — see [`docs/plan/PLAN_V05.md`](plan/PLAN_V05.md), [`docs/VALIDATION_SPEC.md`](VALIDATION_SPEC.md), & [`docs/reports/sf_v05_multitier_validation.md`](reports/sf_v05_multitier_validation.md)):** 5-tier open multi-source validation layer (NOAA/USGS gauges, pluggable hydraulic benchmarks, DataSF DEM/SFFD Tier C, 311 + Google Research Groundsource, OpenFEMA NFIP) built and baseline-scored on frozen SF v0.4 outputs.
- **v0.6 (In Progress: Phases 1–4 complete — see [`docs/plan/PLAN_V06.md`](plan/PLAN_V06.md) & [`docs/reports/sf_v06_calibration_report.md`](reports/sf_v06_calibration_report.md)):** Upgraded 3D `web/` viewer & Tier A–E overlays (Phase 1), reproducible Phase 2A terrain QA (`carve_exclude_osm_ids`, `manual_underpass_cuts`), anti-overfitting Phase 2B calibration across 5 ARs + SFPUC 100-yr (retaining Phase 2A terrain + v0.4 physics as frozen v0.6 defaults, promoting out-of-sample validated `ar_20241122`), full 18-scenario `v0.6` COG export (Phase 3), and public/technical methodology pages (`web/about.html`, `web/methodology.html`; Phase 4); Cloudflare R2/Pages + Zenodo DOIs (Phase 5) remain.
- **v0.7 (Planned):** San Francisquito Creek / East Palo Alto separate-MS4 pilot with zero-shot transfer baseline, pumped-polder handling (`sfincs.pump`), optional Tier 2 municipal overrides, and multi-region `web/` switching.
- **v1.0 (Planned):** Urban Bay Core mosaic across SF, Peninsula, South Bay, and East Bay flatlands (SF, San Mateo, Santa Clara, Alameda, West Contra Costa); North Bay & Delta river basins (`sfincs.dis`) follow in `v1.1+`.

## 2. Core Data Sources & Invariants
Full licensing and provenance live in [`docs/DATA_SOURCES.md`](DATA_SOURCES.md).
- **Hard Coordinate Invariants:** All modeling and raster math use **meters**, **`EPSG:26910`** (UTM Zone 10N), and vertical datum **`NAVD88`**. Convert to `EPSG:3857` only in `04_export_cog.py` and to imperial units only in UI labels.
- **Terrain:** USGS 3DEP 1 m–2 m topobathy DEM (`NAVD88`).
- **Shoreline Structures:** SFEI SF Bay Shore Inventory (`SFBayShoreInventory_SFEI_2016_0`, `Z_Mean` crest elevations).
- **Hydro-Enforcement & Urban Vectors:** OpenStreetMap roads/rail (`bridge`, `tunnel`, `layer`), DataSF/OSM building footprints, and USGS NHD / county creeks.
- **Land Cover, Soils & Benchmarks:** USGS Annual NLCD (land cover + imperviousness), USDA SSURGO HSG, SFPUC 100-Year Flood Risk polygons, and DataSF 311 flooding service requests.

## 3. Scenario Matrix (2024 CA OPC Guidance)
Simulate fixed water-level increments and map them to 2024 CA OPC scenarios (SF tide gauge `9414290`) in metadata/UI:

| Scenario Key | Water Level Increment | 2024 CA OPC Mapping |
| :--- | :--- | :--- |
| `slr0ft` | `0.00 m` (`0.0 ft`) | Baseline Today |
| `slr1ft` | `0.30 m` (`+1.0 ft`) | 2050 Intermediate (0.8 ft) / Intermediate-High (1.0 ft) |
| `slr3ft` | `0.91 m` (`+3.0 ft`) | 2100 Intermediate (3.1 ft) |
| `slr5ft` | `1.52 m` (`+5.0 ft`) | 2100 Intermediate-High (4.8 ft) |

- **Storm Events:** `10yr` (`90.4 mm` / 24 hr), `100yr` (`144.0 mm` / 24 hr), and historical hindcasts (e.g., `ar_20221231`, `128.5 mm` / 48 hr).
- **Tidal Tailwater:** Dynamic 24–48 hr tidal hydrograph (`sfincs.bnd` / `sfincs.bzs`) peaking at MHHW + winter storm surge (never a static bathtub).
- **Derived Delta:** `max(0, depth(Storm + SLR) - depth(Storm + 0 ft SLR))`.

## 4. Pipeline Stages (`pipeline/`)
All scripts must be idempotent, driven by YAML configs in `configs/` (e.g., `configs/sf_citywide.yml`), and assert CRS at boundaries:

1. **`00_fetch_data.py`**
   - Fetch/cache DEM, NLCD, SSURGO HSG, OSM roads/rail, DataSF/OSM buildings, SFEI shoreline crests, 311 reports, Tier C/D/E validation sources, Open-Meteo/MRMS rainfall (including 5-day prior MRMS antecedent precipitation in `sf_mrms_antecedent_5day.json`), and NOAA CO-OPS / design tidal hydrographs into `data/raw/`.
2. **`01_prep_terrain.py`**
   - Enforce upward-only SFEI shoreline crest elevations (`Z_Mean`, `±2.0 m` half-width).
   - Burn underpasses/bridges at OSM crossings (with reproducible `carve_exclude_osm_ids` and `manual_underpass_cuts` YAML controls) while skipping bored hillside tunnels (`max_bump_m: 6.0`).
   - Apply building footprints (`+3.0 m` bump, `n = 0.30`) and write `hydro_dem`, `manning_n`, and `curve_number` GeoTIFFs (`EPSG:26910`).
3. **`02_build_sfincs.py`**
   - Build `HydroMT-SFINCS` model (`20 m` grid / `2 m` subgrid in `sf_citywide.yml`), mask offshore `NoData` (`msk = 0`), set coastal `msk = 2` (`zmax: 0.5 m`) with native `sfincs.bnd`/`sfincs.bzs`, and preserve upland `msk = 3` outflow.
   - Apply Tier 0 uniform drainage subtraction (`5.0 mm/hr`, `sfincs.prp`) and NOAA MRMS 1 km QPE (`netamprfile`), with opt-in calibration/ablation flags (`--drainage-tier`, `--amc-mode`, `--mean-rate-matched`, `--quadtree`, `--runs-dir`), and run `deltares/sfincs-cpu` via Docker.
4. **`03_validate_sf.py`**
   - Downscale `zsmax` onto `2 m` DEM, exclude `NLCD == 11` open water from `land_valid_mask`, and score 5-tier open validation (Tier A NOAA gauges, Tier B SFPUC `>= 0.1524 m`, Tier C emergency/road disruptions, Tier D 311 `0 / 25 / 50 m` + Groundsource sub-basins, Tier E OpenFEMA NFIP tracts) into `validation_ensemble_metrics.json` (`docs/VALIDATION_SPEC.md`).
5. **`04_export_cog.py`**
   - Mask dry/trace (`< 0.05 m`) and open-water cells (`< 0.5 m` or `NLCD == 11`), compute derived SLR deltas (`--all-scenarios`), reproject to `EPSG:3857` COGs, export simplified GeoJSON overlays (`< 2 MB`), and write `manifest.json` per [`docs/DATA_CONTRACTS.md`](DATA_CONTRACTS.md).

## 5. Known Domain Traps
- **SFPUC Polygons Are Parcel-Snapped:** DataSF's 100-year polygons snap to property lines and exclude parks/beaches/open space; evaluate validation on urban bowls and street corridors rather than overfitting parcel edges.
- **Pumped Polders (`v0.7+`):** Low-lying Bay-margin polders (e.g., East Palo Alto/Foster City, parts of Alameda/Oakland) sit below high tide and require explicit `sfincs.pump` structures or `polder_mask_source` exclusions.
- **Vertical Datum Boundary:** Apply `SFCD -> NAVD88` offsets *only* if comparing water-surface elevations against SFPUC records; never alter the base DEM datum.
