# PLAN_V02.md — v0.2 San Francisco Citywide Compound SLR & Historical Storm Validation

Goal: Scale the calibrated v0.1 pipeline (`drainage_rate_mm_hr: 5.0`, `manning_n_paved: 0.030`) to **all of San Francisco (~121 km²)** at a `20 m` computational grid + `2 m` subgrid terrain (`configs/sf_citywide.yml`), add dynamic NOAA `9414290` tidal boundary conditions (`+0 / +1 / +3 / +5 ft` SLR), validate citywide `100yr` + the **Dec 31, 2022 – Jan 1, 2023 Atmospheric River** first before running the remaining 7 SLR/10-yr matrix scenarios, and export derived SLR "drainage lock" delta COGs alongside upgraded MapLibre controls.

---

## Phase 0: Full-SF Config & Raw Data (`configs/sf_citywide.yml`)
- [x] Define full-SF bounding box (`EPSG:26910`, `NAVD88`) with `grid_resolution_m: 20.0`, `subgrid_resolution_m: 2.0`, and calibrated defaults (`drainage_rate_mm_hr: 5.0`, `manning_n_paved: 0.030`) in `configs/sf_citywide.yml`.
- [x] Download citywide raw inputs into `data/raw/sf_citywide/` (git-ignored):
  - [x] Full-SF USGS 3DEP 1 m LiDAR DEM (`CA_SanFrancisco_B23` tiles covering the entire peninsula + Farallon exclusion).
  - [x] USGS Annual NLCD 2021 land cover & imperviousness and USDA SSURGO HSG (`CA689`).
  - [x] OSM roads, rail, and building footprints covering full SF.
  - [x] SFEI SF Bay Shore Inventory shoreline structures (seawalls, levees, berms).
  - [x] NOAA CO-OPS `9414290` (San Francisco) 6-min `NAVD88` tide series for **2022-12-31 – 2023-01-01** plus dynamic 24–48 hr design tidal hydrographs shifted by `+0 / +1 / +3 / +5 ft` (`0.0 / 0.3048 / 0.9144 / 1.5240 m`) synchronized with peak rainfall.
  - [x] Dec 31, 2022 observed rainfall hyetograph (MRMS / Downtown SF / KSFO gauge) for `sf_ar_20221231`.
  - [x] DataSF 311 Cases filtered to `2022-12-31` – `2023-01-01` street flooding / catch-basin / sewer-backup reports.

## Phase 1: Citywide Hydro-Enforcement & Shoreline Structures (`pipeline/01_prep_terrain.py`)
- [x] Run hydro-enforcement across all SF freeway/rail underpasses and tunnels (US-101, I-280, I-80 approaches, Caltrain, Geary/Stockton underpasses) at `2 m` subgrid resolution.
- [x] Burn/enforce SFEI shoreline structure crest elevations onto the `2 m` DEM so coastal seawalls and berms are preserved rather than smoothed out.
- [x] Generate citywide `sf_citywide_hydro_dem_2m.tif`, `sf_citywide_manning_n_2m.tif`, and `sf_citywide_curve_number_2m.tif` in `data/processed/`.

## Phase 2A: Coastal Tidal BCs & Initial Citywide Validation Runs (`pipeline/02_build_sfincs.py`)
- [x] Extend `pipeline/02_build_sfincs.py` to configure coastal water-level boundary cells (`msk=2` + `sfincs.bzs`/`sfincs.bnd`) along SF Bay & Pacific Ocean shorelines from `tidal_hydrograph_file`, keeping `msk=3` outflow along the southern San Mateo County boundary and masking deep offshore water.
- [x] Run **citywide baseline `sf_100yr_slr0ft`** (`20 m` grid / `2 m` subgrid).
- [x] Run **real-storm hindcast `sf_ar_20221231`** (Dec 31, 2022 observed rainfall + observed NOAA `9414290` tide).

## Phase 3: Dual Citywide Validation (`pipeline/03_validate_sf.py`)
- [ ] Score citywide `sf_100yr_slr0ft` (`>= 0.1524 m`) against the full SFPUC 100-Year Storm Flood Risk Map (citywide + neighborhood breakdown: Mission, Alemany, SOMA, Marina, Sunset/Lake Merced, Bayview/Islais Creek, Visitacion Valley).
- [ ] Score `sf_ar_20221231` against **Dec 31, 2022 SF 311 flood reports** (spatial hit/capture rate within `25 m` and `50 m` buffers vs. random dry control points).
- [ ] Generate a 3-way validation report (`docs/reports/sf_v02_validation.md` + JSON metrics in `data/processed/sf_citywide/`) comparing Dec 31, 2022 SF 311 cases against both our SFINCS hindcast (`sf_ar_20221231`) and the official SFPUC 100-Year Flood Risk Map: capture rates at `0 m / 25 m / 50 m` buffers (`>= 0.05 m` and `>= 0.1524 m`), lift/ROC-AUC vs. random dry control points, subtype breakdown (`Flooding` vs. `Sewage_back_up`), neighborhood breakdown, and top missed 311 clusters.
- [ ] Inspect top false positives/negatives across citywide corridors and resolve any remaining unburned underpasses or shoreline berm gaps before running the full matrix.

## Phase 2B: Full 8-Scenario Compound SLR Matrix (`pipeline/02_build_sfincs.py`)
- [ ] Once Phase 3 validation passes, run the remaining 7 scenarios (`sf_100yr_slr{1,3,5}ft` and `sf_10yr_slr{0,1,3,5}ft`) with dynamic NOAA `9414290` tidal boundary conditions.

## Phase 4: Derived SLR Deltas & Multi-Scenario COG Export (`pipeline/04_export_cog.py`)
- [ ] Compute derived **drainage-lock delta grids** (`delta = max(0, depth(storm + SLR) - depth(storm + 0 ft SLR))`) for `+1 / +3 / +5 ft` SLR across `10yr` and `100yr` storms.
- [ ] Export `EPSG:3857` COGs for all `depth`, `delta`, and `sf_ar_20221231` scenarios to `data/web_outputs/`.
- [ ] Export citywide simplified `sf_sfpuc_100yr_benchmark.geojson` (`< 2 MB`), `sf_311_20221231_floods.geojson`, and multi-scenario `manifest.json` (including `2024 CA OPC` horizon metadata if needed in UI labels).

## Phase 5: Compound SLR & Dual-Validation Viewer (`web/`)
- [ ] Upgrade control panel to select **Storm** (`10-Yr`, `100-Yr`, `Dec 31, 2022 AR`) × **SLR** (`Today`, `+1 ft`, `+3 ft`, `+5 ft` with 2024 CA OPC pathway labels) × **Metric** (`Total Depth` vs. `Added SLR Delta`).
- [ ] Add toggleable **Dec 31, 2022 311 Flood Reports** point overlay alongside the SFPUC 100-Year polygon toggle.
- [ ] Update initial map center/bounds to frame all of San Francisco (`[-122.445, 37.760]`, zoom `12.0`).
- [ ] Verify Node tests (`node --test`), `npm run lint`, `npm run build`, and `vite preview` Range serving.

---

## Exit Criteria (Ready for v0.3 Static Hosting & v0.4 MS4 Pilot)
- Full-SF `100yr` + `Dec 31, 2022 AR` runs pass both SFPUC spatial validation and 311 report capture checks without artificial highway or seawall dam artifacts.
- All 9 citywide runs (`8` Storm × SLR + `1` Dec 2022 AR) and derived `+1/+3/+5 ft` SLR delta COGs render smoothly in `web/`.
