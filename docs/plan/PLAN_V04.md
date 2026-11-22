# PLAN_V04.md — San Francisco Literature-Backed Methodology Upgrades & Ablation

**Goal:** Implement the remaining peer-reviewed compound flood modeling methods on San Francisco (`configs/sf_citywide.yml`) and run controlled A/B ablations against the frozen **v0.2 Tier 0** baseline before expanding coverage to East/South Bay separate-MS4 watersheds in **v0.5**.

## Frozen v0.2 Control Baseline (`sf_citywide`, Tier 0 `5.0 mm/hr`, `20 m / 2 m`)
| Metric | Frozen v0.2 Value | Source |
| --- | ---: | --- |
| SFPUC 100-Yr Citywide (`HR / FAR / CSI`) | `0.3884 / 0.7083 / 0.1999` | [`docs/reports/sf_v02_validation.md`](../reports/sf_v02_validation.md) |
| Dec 31, 2022 AR vs. 311 (`0 m` / `25 m` / `50 m` lift) | `3.59x / 1.71x / 1.26x` | [`docs/reports/sf_v02_validation.md`](../reports/sf_v02_validation.md) |
| Dec 31, 2022 AR vs. 311 (`25 m` `ROC-AUC`) | `0.6715` | [`docs/reports/sf_v02_validation.md`](../reports/sf_v02_validation.md) |

---

## Phase 1 (Upgrade 1): Multi-Gauge Spatially Varying Bay Tides (`Nederhoff et al., 2021`)
- [x] Fetch multi-gauge NOAA CO-OPS observed & design tides across SF Bay (`9414290` Presidio, `9414750` Alameda, `9414863` Richmond, `9414523` Redwood City as boundary anchors).
- [x] Extend `pipeline/02_build_sfincs.py` to write multi-point `sfincs.bnd` / `sfincs.bzs` tables so SFINCS interpolates estuarine tidal gradients along `msk = 2`.
- [x] Add unit tests verifying multi-station `sfincs.bnd` / `sfincs.bzs` alignment and run A/B comparison on `sf_citywide`.

## Phase 2 (Upgrade 2): Tier 1 Bay-Wide 2D Drainage (`Bates et al., 2021`)
- [ ] Implement **Tier 1a** in `pipeline/02_build_sfincs.py`: compute spatially varying 2D urban drainage abstraction from NLCD imperviousness/land cover and write SFINCS gridded net rainfall (`netamprfile`), keeping SCS Curve Number soil infiltration (`scsfile`) intact.
- [ ] Implement **Tier 1b** (experimental extension): attenuate Tier 1a gravity drainage smoothly as `(DEM - tide(t)) -> 0`, with explicit polder exclusion flags.
- [ ] Add optional **Tier 2** config hook (`domain.storm_drain_source: null` by default) for municipal GIS enrichment and record `drainage_tier` in `manifest.json`.
- [ ] Ablate Tier 1a alone and Tier 1a + 1b independently on `sf_citywide` against frozen v0.2 Tier 0 (`5.0 mm/hr`).

## Phase 3 (Upgrade 3): Multi-Storm AR Hindcast Ensemble (`Eilander et al., 2023`)
- [ ] Fetch Open-Meteo / NOAA CO-OPS / DataSF 311 records for 3–4 historical San Francisco atmospheric rivers (e.g., Oct 2021, Dec 31, 2022, Jan 2023, Feb 2024).
- [ ] Extend `pipeline/03_validate_sf.py` to score the multi-storm ensemble and report per-event and ensemble-mean `ROC-AUC`, capture rate, and lift across `0 / 25 / 50 m` buffers.

## Phase 4 (Upgrade 4): SFINCS Quadtree Mesh Refinement (`Leijnse et al., 2021`)
- [ ] Add optional quadtree refinement configuration (`10 m` cells in low-lying urban/coastal corridors, `40–80 m` cells on steep uplands) in `pipeline/02_build_sfincs.py`.
- [ ] Benchmark `0 m` / `25 m` 311 hit rate, SFPUC `CSI`, and Docker runtime on `sf_citywide` against the uniform `20 m` grid.

## Phase 5: Formal v0.2 vs. v0.4 Ablation Report & Living Methods Sync
- [ ] Generate `docs/reports/sf_v02_vs_v04_ablation.md` comparing each upgrade side-by-side against frozen v0.2; retain only changes that demonstrably improve skill or physical realism.
- [ ] Update `docs/METHODS.md` and `docs/MODEL_SPEC.md` with the validated v0.4 defaults and re-export updated SF `EPSG:3857` COGs to `data/web_outputs/`.

---

## Exit Criteria
- Every retained v0.4 upgrade shows measurable improvement (or verified physical necessity) in `docs/reports/sf_v02_vs_v04_ablation.md` over the frozen v0.2 Tier 0 baseline.
- Validated methodology is ready to apply out-of-the-box to East/South Bay separate-MS4 watersheds in **v0.5**.
