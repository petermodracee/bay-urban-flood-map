# PLAN_V04.md — Literature-Backed Upgrades & Bay-Margin MS4 Pilot

**Goal:** Implement the remaining peer-reviewed compound flood modeling methods on top of the frozen **v0.2 Tier 0** baseline, run controlled A/B ablations on San Francisco citywide, and validate on a flatland, Bay-margin separate-MS4 watershed (e.g., Oakland Coliseum / Lion Creek or San Francisquito Creek).

## Frozen v0.2 Control Baseline (`sf_citywide`, Tier 0 `5.0 mm/hr`, `20 m / 2 m`)
| Metric | Frozen v0.2 Value | Source |
| --- | ---: | --- |
| SFPUC 100-Yr Citywide (`HR / FAR / CSI`) | `0.3884 / 0.7083 / 0.1999` | [`docs/reports/sf_v02_validation.md`](../reports/sf_v02_validation.md) |
| Dec 31, 2022 AR vs. 311 (`0 m` / `25 m` / `50 m` lift) | `3.59x / 1.71x / 1.26x` | [`docs/reports/sf_v02_validation.md`](../reports/sf_v02_validation.md) |
| Dec 31, 2022 AR vs. 311 (`25 m` `ROC-AUC`) | `0.6715` | [`docs/reports/sf_v02_validation.md`](../reports/sf_v02_validation.md) |

---

## Phase 0: MS4 Pilot Domain & Multi-Storm Data Fetch (`pipeline/00_fetch_data.py`)
- [ ] Select Bay-margin separate-MS4 pilot watershed (`configs/ms4_pilot.yml`) with gravity outfalls, pumped polder zones, and dense 311/CHP/USGS validation coverage.
- [ ] Fetch `1 m–2 m` USGS 3DEP topobathy DEM, NLCD, USDA SSURGO HSG, OSM roads/buildings, SFEI shoreline structures, and county creek centerlines.
- [ ] Fetch multi-gauge NOAA CO-OPS observed tides (`9414290` Presidio, `9414523` Redwood City, `9414750` Alameda, `9414863` Richmond, `9415020` Point Reyes as applicable).
- [ ] Fetch historical AR rainfall + 311/CHP/USGS validation records for a 3–4 storm ensemble (e.g., Oct 2021, Dec 31, 2022, Jan 2023, Feb 2024).

## Phase 1 (Upgrade 1): Multi-Gauge Spatially Varying Bay Tides (`Nederhoff et al., 2021`)
- [ ] Extend `pipeline/02_build_sfincs.py` to support multiple `(station_id, x, y)` tidal gauges in `sfincs.bnd` / `sfincs.bzs` so SFINCS interpolates estuarine tidal amplification along `msk = 2`.
- [ ] Add unit tests verifying multi-station `sfincs.bnd` / `sfincs.bzs` formatting and column alignment.
- [ ] Ablate on `sf_citywide` (and MS4 pilot) against the single-gauge v0.2 baseline.

## Phase 2 (Upgrade 2): Tier 1 Bay-Wide 2D Drainage & Pumped Polders (`Bates et al., 2021`)
- [ ] Implement **Tier 1a** in `pipeline/02_build_sfincs.py`: compute spatially varying 2D urban drainage abstraction from NLCD imperviousness/land cover and write SFINCS gridded net rainfall (`netamprfile`), keeping SCS Curve Number soil infiltration (`scsfile`) intact.
- [ ] Implement **Tier 1b** (experimental extension): attenuate Tier 1a gravity drainage smoothly as `(DEM - tide(t))` drops near/below Bay water level, explicitly flagging/excluding mapped pumped polders (and configuring SFINCS `pump` / `check_valve` structures where documented).
- [ ] Add optional **Tier 2** config hook (`domain.storm_drain_source: null` by default) to locally refine Tier 1 where open municipal storm-drain GIS exists, and record `drainage_tier` in `manifest.json`.
- [ ] Ablate Tier 1a alone and Tier 1a + 1b independently against v0.2 Tier 0 (`5.0 mm/hr`).

## Phase 3 (Upgrade 3): Multi-Storm AR Hindcast Ensemble (`Eilander et al., 2023`)
- [ ] Extend `pipeline/03_validate_sf.py` to score 3–4 historical atmospheric rivers in batch and report per-event and ensemble-mean `ROC-AUC`, capture rate, lift, and uncertainty spread across `0 / 25 / 50 m` buffers + USGS/CHP observations.
- [ ] Confirm parameters generalize across storms without overfitting to Dec 31, 2022.

## Phase 4 (Upgrade 4): SFINCS Quadtree Mesh Refinement (`Leijnse et al., 2021`)
- [ ] Add optional quadtree refinement configuration (`10 m` cells in low-lying urban/coastal corridors, `40–80 m` cells on steep uplands) in `pipeline/02_build_sfincs.py`.
- [ ] Benchmark `0 m` / `25 m` 311 hit rate, SFPUC `CSI`, and Docker runtime against the uniform `20 m` grid.

## Phase 5: Ablation Report & Living Methodology Sync
- [ ] Generate `docs/reports/sf_v02_vs_v04_ablation.md` comparing each upgrade side-by-side against the frozen v0.2 baseline; retain only changes that demonstrably improve skill or physical realism.
- [ ] Update `docs/METHODS.md` and `docs/MODEL_SPEC.md` to reflect the validated v0.4 defaults and publish MS4 pilot COGs to `data/web_outputs/`.

---

## Exit Criteria
- Every retained v0.4 upgrade shows measurable improvement (or verified physical necessity) in `docs/reports/sf_v02_vs_v04_ablation.md` over the frozen v0.2 Tier 0 baseline.
- Bay-margin separate-MS4 pilot validates against multi-storm historical flood records with documented pumped-polder handling and `drainage_tier` metadata.
