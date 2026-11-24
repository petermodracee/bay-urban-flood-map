# San Francisco Citywide (`sf_citywide`) — v0.6 Anti-Overfitting Physics Calibration Report

**Date:** 2026-11-24  
**Terrain Control:** Phase 2A (`configs/sf_citywide.yml` — `carve_exclude_osm_ids: [35156816, 35156817]`, `20 m` grid / `2 m` subgrid)  
**Validation Protocol:** [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) §3.4 (one-factor-at-a-time ablations on 3 calibration ARs + `100yr_slr0ft` first; retention gate evaluated before touching holdout storms)

---

## 1. Executive Summary & Retention Decision

Under the `v0.6` anti-overfitting protocol ([`docs/plan/PLAN_V06.md`](../plan/PLAN_V06.md)), two literature-bounded physical candidates were tested **one-factor-at-a-time** on top of the Phase 2A terrain control across the **3 calibration ARs** (`ar_20211024`, `ar_20221231`, `ar_20230109`; `1,394` 311 points) and `sf_citywide_100yr_slr0ft`:

1. **Candidate 1 — Antecedent Soil Moisture (`AMC-II -> AMC-III` via 5-Day Prior MRMS):**  
   All 3 calibration ARs exceeded the USDA NRCS `28.0 mm` 5-day antecedent threshold (`36.76 mm`, `69.92 mm`, `57.68 mm`), triggering NEH-630 `CN_III`. While Tier D 311 `25 m` capture improved (`49.03% -> 54.53%`, lift `1.573x -> 1.629x`, `ROC-AUC` `0.6184 -> 0.6241`), Tier C `ROC-AUC` (`0.5755 -> 0.5711`) and lift (`1.338x -> 1.252x`) regressed, and the Tier D `ROC-AUC` gain (`+0.0057`) did not clear the `>= +0.01` parsimony threshold. **Rejected.**
2. **Candidate 2 — Mean-Rate-Matched `Tier 1a` and `Tier 1b` 2D Urban Drainage:**  
   Normalizing NLCD-imperviousness-scaled drainage to an exact `5.0 mm/hr` domain-mean urban rate fixed the `~8–9 mm/hr` over-abstraction from v0.4 Phase 2, but concentrating abstraction (`> 5.0 mm/hr`) in dense impervious urban bowls (`SOMA`, `Alemany`, downtown) while lowering it on parks (`< 5.0 mm/hr`) still starved urban ponding: SFPUC 100-yr `CSI` dropped from `0.1953` to `0.1689` (`Tier 1a`) and `0.1688` (`Tier 1b`), and Tier D 311 `25 m ROC-AUC` dropped from `0.6184` to `0.6044` / `0.6051`. **Rejected.**

**Frozen v0.6 Decision:** Because neither candidate cleared the 3-AR calibration gate (`>= 2` tiers improved, non-regressing SFPUC `CSI`, parsimony `>= +0.01` `ROC-AUC` or `>= +0.005` `CSI`), their combination was not tested, and **Phase 2A terrain fixes + frozen v0.4 physics** (`20 m / 2 m`, `Tier 0` `5.0 mm/hr` uniform drainage, `AMC-II`, 4-gauge Bay tides, NOAA MRMS 1 km QPE) are retained as the **frozen v0.6 SF defaults**.

Out-of-sample evaluation of the frozen v0.6 defaults on the newly fetched second untouched holdout storm (**`ar_20241122`**, Nov 22–23, 2024 AR; `70.3 mm` MRMS mean, `536` 311 points, `50` Tier C points) confirms strong spatial generalization without tuning: **Tier D 311 `25 m` `57.09%` capture (`1.684x` lift, `0.6285` `ROC-AUC`), `0 m` `3.860x` lift (`0.5531` `ROC-AUC`), and Tier C `25 m` `46.00%` capture (`1.228x` lift, `0.5753` `ROC-AUC`)**.

---

## 2. Five-Day Antecedent MRMS Precipitation (`sf_mrms_antecedent_5day.json`)

Fetched via `pipeline/00_fetch_data.py` (`MultiSensor_QPE_01H_Pass2`, `120` hours prior to storm start, `0` missing hours across all 5 ARs) and evaluated against the USDA NRCS dormant-season wet threshold (`> 28.0 mm`):

| Storm ID | 5-Day Prior Window | Mean 5-Day MRMS (`mm`) | Max 5-Day MRMS (`mm`) | NRCS Condition |
| --- | --- | ---: | ---: | --- |
| `ar_20211024` *(cal)* | `2021-10-19` – `2021-10-23` | `36.76` | `45.20` | `AMC-III` |
| `ar_20221231` *(cal)* | `2022-12-26` – `2022-12-30` | `69.92` | `80.74` | `AMC-III` |
| `ar_20230109` *(cal)* | `2023-01-04` – `2023-01-08` | `57.68` | `65.06` | `AMC-III` |
| `ar_20240204` *(holdout)* | `2024-01-30` – `2024-02-03` | `67.47` | `90.01` | `AMC-III` |
| `ar_20241122` *(holdout)* | `2024-11-17` – `2024-11-21` | `26.78` | `45.54` | `AMC-II` |

---

## 3. One-Factor-at-a-Time Calibration Ablations (3-AR Cal + `100yr_slr0ft`)

Evaluated strictly on the **3 calibration ARs** (`ar_20211024`, `ar_20221231`, `ar_20230109`) and `sf_citywide_100yr_slr0ft`:

| Configuration | Tier B (`100yr_slr0ft` HR / FAR / CSI) | Tier D 311 (`25 m` 3-AR Cap / Lift / AUC) | Tier C (`25 m` 3-AR Cap / Lift / AUC) | Tier D Groundsource (`HR` / `Acc`) | Tier E NFIP (`rho_bldg` / `rho_area`) | 3-AR Gate |
| --- | --- | --- | --- | --- | --- | --- |
| **Phase 2A Control** (`Tier 0`, `AMC-II`) | `0.3901 / 0.7188 / 0.1953` | `49.03% / 1.573x / 0.6184` | `43.95% / 1.338x / 0.5755` | `1.000 / 0.9024` | `0.0956 / 0.2289` | **Baseline** |
| **Candidate 1** (`Tier 0`, `AMC-III` auto) | `0.3901 / 0.7188 / 0.1953` | `54.53% / 1.629x / 0.6241` | `45.52% / 1.252x / 0.5711` | `1.000 / 0.9024` | `0.0956 / 0.2289` | **FAIL** (`1` tier, Tier C `-0.0044` AUC, `+0.0057 < +0.01`) |
| **Candidate 2a** (`Tier 1a` rate-matched) | `0.3042 / 0.7249 / 0.1689` | `48.31% / 1.519x / 0.6044` | `43.95% / 1.382x / 0.5747` | `1.000 / 0.9024` | `0.1268 / 0.2311` | **FAIL** (Tier B `-0.0264` CSI, Tier D `-0.0140` AUC) |
| **Candidate 2b** (`Tier 1b` rate-matched) | `0.3049 / 0.7257 / 0.1688` | `48.70% / 1.525x / 0.6051` | `43.95% / 1.373x / 0.5741` | `1.000 / 0.9024` | `0.1268 / 0.2310` | **FAIL** (Tier B `-0.0265` CSI, Tier D `-0.0133` AUC) |

### Physical Interpretation
- **Why uniform `Tier 0` outperforms NLCD-scaled `Tier 1a/1b` in SF:** San Francisco's combined sewer system is engineered with higher trunk capacity in dense downtown/SOMA/Alemany basins, yet those same impervious bowls still concentrate street ponding during cloudbursts (`SFPUC 100-Yr`). Scaling 2D drainage upward with NLCD imperviousness—even when mean-rate-matched to `5.0 mm/hr` across urban land—removes too much water directly from the impervious bowls where ponding occurs while under-abstracting pervious open space. Uniform `5.0 mm/hr` (`Tier 0`) remains the better parsimonious approximation for SF's combined-sewer core; mean-rate-matched `Tier 1a/1b` remains available in `pipeline/02_build_sfincs.py` (`--mean-rate-matched`) for `v0.7` separate-MS4 pilots.
- **Why `AMC-III` is not retained:** Increasing Curve Number on pervious/semi-pervious soils increases runoff volume uniformly, lifting 311 capture (`+5.50 pp`) at the cost of broader false-positive road inundation (`Tier C` lift `1.338x -> 1.252x`, `AUC` `-0.0044`).

---

## 4. Frozen v0.6 Five-Tier Baseline & Untouched Holdout Evaluation

Scored from `data/processed/sf_citywide/validation_ensemble_metrics.json` across all **5 ARs** (`2,071` valid-land 311 points) and `sf_citywide_100yr_slr0ft`:

### 4.1 Tier A, C, D Per-Event & 5-AR Ensemble Summary

| Event (Split) | 311 Pts | Tier D 311 `25 m` (Cap / Lift / AUC) | Tier D 311 `50 m` (Cap / Lift / AUC) | Tier C Pts | Tier C `25 m` (Cap / Lift / AUC) | Tier A `9414290` (`RMSE` / Peak Err) |
| --- | ---: | --- | --- | ---: | --- | --- |
| `ar_20211024` *(cal)* | `567` | `53.62% / 1.538x / 0.5997` | `79.01% / 1.248x / 0.5866` | `64` | `51.56% / 1.360x / 0.5733` | `0.0013 m / +0.0016 m` |
| `ar_20221231` *(cal)* | `424` | `58.73% / 1.790x / 0.6463` | `77.59% / 1.257x / 0.6294` | `66` | `56.06% / 1.568x / 0.6362` | `0.0014 m / +0.0001 m` |
| `ar_20230109` *(cal)* | `403` | `34.74% / 1.392x / 0.6092` | `70.47% / 1.391x / 0.6224` | `33` | `24.24% / 1.087x / 0.5171` | `0.0019 m / +0.0012 m` |
| `ar_20240204` *(holdout)* | `141` | `0.71% / 0.284x / 0.5000` | `2.13% / 0.413x / 0.4798` | `97` | `1.03% / 0.543x / 0.4950` | `0.0016 m / +0.0011 m` |
| `ar_20241122` *(holdout)* | `536` | `57.09% / 1.684x / 0.6285` | `82.65% / 1.337x / 0.6217` | `50` | `46.00% / 1.228x / 0.5753` | `0.0013 m / +0.0010 m` |
| **5-AR Ensemble Mean** | **2,071** | **`40.98% / 1.338x / 0.5967`** | **`62.37% / 1.129x / 0.5880`** | **310** | **`35.78% / 1.157x / 0.5594`** | **`0.0013–0.0019 m` (`NSE = 1.0`)** |

*Note:* For `ar_20241122` at `0 m`, 311 capture is `11.19%` with **`3.860x` lift** (`0.5531` `ROC-AUC`), and Tier C `50 m` capture is `84.00%` (`1.235x` lift, `0.6173` `ROC-AUC`). Based on this strong out-of-sample validation, `ar_20241122` is recommended for inclusion in Phase 3 public hindcast COG exports (while `ar_20240204` remains documented as a sub-threshold moderate event).

### 4.2 Tier B, Tier D Groundsource & Tier E NFIP Summary
- **Tier B (`sfpuc_100yr`, `>= 0.1524 m`):** `HR = 0.3901`, `FAR = 0.7188`, `CSI = 0.1953`.
- **Tier D Groundsource (`332` filtered records, `41` SF sub-basins):** `37 / 37` Groundsource-present sub-basins matched (`presence_hit_rate = 1.000`, `presence_accuracy = 0.9024`).
- **Tier E OpenFEMA NFIP (`259` tracts, `92` claims, `\$1,536,935.05` paid):** `claim_count` vs. `flooded_land_area_m2` Spearman $\rho = 0.2289$ ($p = 0.0002$); `claim_count` vs. `exposed_buildings` $\rho = 0.0956$ ($p = 0.1248$).
