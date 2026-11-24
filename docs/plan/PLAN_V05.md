# PLAN_V05.md — 5-Tier Open Multi-Source Validation Layer (San Francisco Baseline)

**Goal:** Build, unit-test, and baseline-score all 5 open validation tiers ([`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md)) on the already-completed **San Francisco (`sf_citywide`)** v0.4 outputs with **zero model physics changes**. Locking in a verified 5-tier measurement suite (`docs/reports/sf_v05_multitier_validation.md`) establishes the objective yardstick for **v0.6** (SF multi-tier diagnostic audit & anti-overfitting calibration) and **v0.7** (San Francisquito Creek / East Palo Alto separate-MS4 pilot).

## Frozen v0.4 Regression Targets (`sf_citywide`, `20 m / 2 m`, Tier 0, 4-Gauge Tides, MRMS QPE)

| Metric | Frozen v0.4 Value | Source |
| --- | ---: | --- |
| SFPUC 100-Yr Citywide (`HR / FAR / CSI`) | `0.3887 / 0.7086 / 0.1998` | [`docs/reports/sf_v02_vs_v04_ablation.md`](../reports/sf_v02_vs_v04_ablation.md) |
| 4-AR MRMS Ensemble (`1,535` 311 pts, `25 m` mean `Cap / Lift / AUC`) | `36.95% / 1.251x / 0.5888` | [`docs/reports/sf_v02_vs_v04_ablation.md`](../reports/sf_v02_vs_v04_ablation.md) |
| 4-AR MRMS Ensemble (`50 m` mean `Cap / Lift / AUC`) | `57.30% / 1.077x / 0.5796` | [`docs/reports/sf_v02_vs_v04_ablation.md`](../reports/sf_v02_vs_v04_ablation.md) |

---

## Phase 0: Specs, Licensing & Planning Baseline
- [x] Land [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) (including Section 3.4 anti-overfitting guardrails), `docs/plan/PLAN_V05.md`, [`docs/DATA_SOURCES.md`](../DATA_SOURCES.md) validation entries, and [`docs/plan/ROADMAP.md`](ROADMAP.md) milestone split (`v0.5` / `v0.6` / `v0.7`).

---

## Phase 1: Contract Update & Region-Agnostic Validator Refactor
- [x] Confirm and update [`docs/DATA_CONTRACTS.md`](../DATA_CONTRACTS.md) with the additive multi-tier `validation_ensemble_metrics.json` contract (preserving all v0.4 keys: `n_events`, `total_311_points`, `events[]`, `ensemble_mean.by_buffer`).
- [x] Refactor `pipeline/03_validate_sf.py` scoring primitives into pure, region-agnostic functions separated from file I/O with fast, offline `pytest` fixtures.
- [x] Verify exact zero-regression on `sf_citywide` v0.4 metrics (`SFPUC CSI = 0.1998`; 4-AR MRMS `25 m` `36.95% / 1.251x / 0.5888`).

---

## Phase 2: Tier A (Physical Gauges) & Tier B (Pluggable Hydraulic Benchmarks)
- [x] Implement **Tier A** coastal/stream gauge & HWM (`NAVD88`) scoring (`RMSE`, `MAE`, `Bias`, peak timing error; `Grimley et al., 2025`; `Nederhoff et al., 2021`):
  - Run live coastal water-level verification against NOAA CO-OPS `9414290` (Presidio) for the 4 SF AR hindcasts.
  - Implement and offline-test USGS NWIS stream-gauge & STN High-Water Mark (`NGVD29 -> NAVD88` meters) scoring ready for live `11164500` validation in `v0.7`.
- [x] Generalize **Tier B** polygon-to-raster `HR / FAR / CSI` scoring into a pluggable benchmark interface (`Sanders et al., 2023`; `Schubert, Mach, & Sanders, 2024`) verified against SFPUC 100-Yr.

---

## Phase 3: Tier C (Roadway Closures) & Tier D (311 + Groundsource)
- [x] Fetch and score open **Tier C** Caltrans QuickMap / PeMS / CHP / 511 SF Bay roadway flood closures across SF (e.g., US-101 / I-280 / Alemany / Cesar Chavez) against the 4-AR MRMS ensemble (`25 m / 50 m` road-snapped capture, lift vs. random road controls, `ROC-AUC`; `Praharaj et al., 2021`), with offline `pytest` coverage.
- [x] Integrate **Google Research Groundsource** (`CC BY 4.0` GeoParquet, filtered to `area_km2 <= 25`) clipped to SF alongside DataSF 311 (`Agonafir et al., 2022`; `Lerma et al., 2025`) to verify sub-basin flood presence and identify 1–2 untouched holdout ARs for `v0.6` anti-overfitting validation.

---

## Phase 4: Tier E (OpenFEMA NFIP) & SF 5-Tier Baseline Report
- [x] Fetch **OpenFEMA NFIP Redacted Claims** (`FimaNfipClaims` API) aggregated by SF census tracts and compute Spearman $\rho$ against `sf_citywide_100yr_slr0ft` modeled building exposure (`Wing et al., 2020`), with offline `pytest` coverage.
- [x] Publish `docs/reports/sf_v05_multitier_validation.md` summarizing SF baseline scores across all 5 tiers, diagnostic failure modes (underpass carving, moderate-AR antecedent moisture, spatial drainage), and the frozen holdout-storm split for `v0.6`.

---

## Exit Criteria
- [x] All 5 validation tiers (`Tier A`–`Tier E`) are implemented as pure, offline-tested functions (`pytest` passes: `74 passed`), and live-scored on `sf_citywide` (NOAA Presidio, SFPUC 100-Yr, DataSF DEM/SFFD Tier C, DataSF 311 + Zenodo Groundsource Tier D, OpenFEMA `v3/NfipClaims` Tier E).
- [x] Zero regression against frozen v0.4 SF metrics (`SFPUC CSI = 0.1998`; 4-AR MRMS `25 m` `ROC-AUC = 0.5888`).
- [x] `docs/reports/sf_v05_multitier_validation.md` is published and establishes the holdout-protected baseline (`ar_20240204` + `ar_20241122`) for `v0.6` calibration.
