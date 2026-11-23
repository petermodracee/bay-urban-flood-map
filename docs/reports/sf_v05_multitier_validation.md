# San Francisco Citywide (`sf_citywide`) — v0.5 Five-Tier Validation Baseline Report

**Date:** 2026-11-23  
**Model Configuration:** Frozen **v0.4** defaults ([`configs/sf_citywide.yml`](../../configs/sf_citywide.yml)) — uniform `20 m` grid / `2 m` subgrid, `drainage_tier: "tier0"` (`5.0 mm/hr` uniform drainage + `scsfile` CN infiltration), `n_paved = 0.030`, 4-gauge NOAA CO-OPS Bay tides (`9414290`, `9414750`, `9414863`, `9414523`), NOAA MRMS 1 km QPE (`rain_source: "mrms"`) for historical AR hindcasts, and NOAA Atlas 14 for the 100-year design storm.  
**v0.5 Invariant:** Zero model physics changes relative to v0.4 ([`docs/plan/PLAN_V05.md`](../plan/PLAN_V05.md)). This report locks in the 5-tier measurement baseline ([`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md)) prior to `v0.6` calibration.

---

## 1. Five-Tier Validation Status & Baseline Summary

| Tier | Source & Literature Basis | Implementation & Test Status | SF Baseline Score (`sf_citywide`) |
| --- | --- | --- | --- |
| **Tier A: Physical Gauges & HWMs** | NOAA CO-OPS `9414290` (Presidio), USGS NWIS & STN HWMs (`Grimley et al., 2025`; `Nederhoff et al., 2021`) | Live NOAA CO-OPS scored across 4 ARs; USGS NWIS/STN (`NGVD29 -> NAVD88`) offline-tested | **4-AR Presidio `9414290`:** `RMSE = 0.0013–0.0019 m`, `MAE = 0.0010–0.0015 m`, `Bias = +0.0007 to +0.0013 m`, peak timing error `0.0 min`, `NSE = 1.0` |
| **Tier B: Hydraulic Benchmarks** | SFPUC 100-Yr Storm Flood Risk Map (`>= 0.1524 m` / `6 in`; `Sanders et al., 2023`; `Schubert, Mach, & Sanders, 2024`) | Pluggable `BenchmarkSpec` interface implemented & offline-tested | **Citywide (`v0.4` frozen / `v0.5` run):** `HR = 0.3887 / 0.3900`, `FAR = 0.7086 / 0.7189`, `CSI = 0.1998 / 0.1953` |
| **Tier C: Roadway Closures** | Caltrans D4 LCS / CHP / 511 SF Bay (`Praharaj et al., 2021`) | Parsers (`00_fetch_data.py`) & road-snapped `25 m / 50 m` + road-control lift / `ROC-AUC` scorer (`03_validate_sf.py`) offline-tested | `road_closures_file: null` in `configs/sf_citywide.yml` (live real-time feeds lack free historical backfill; ready for live AR capture) |
| **Tier D: Civic 311 + Groundsource** | DataSF 311 (`1,535` pts across 4 ARs; `Agonafir et al., 2022`; `Lerma et al., 2025`) + Google Research Groundsource (`CC BY 4.0`, `area_km2 <= 25`) | 4-AR MRMS ensemble & live SF Groundsource (`332` filtered records / `41` sub-basins) scored | **4-AR MRMS `25 m` mean:** `36.89%` capture, `1.249x` lift, `ROC-AUC = 0.5886` (`50 m`: `57.24%` cap, `1.076x` lift, `0.5791` AUC); **Groundsource:** `1.000` presence HR, `0.9024` accuracy (`37/37` GS-present sub-basins matched) |
| **Tier E: Structural Loss (NFIP)** | OpenFEMA NFIP Redacted Claims (`v3/NfipClaims`) vs. modeled building exposure (`Wing et al., 2020`) | Live OpenFEMA county `06075` + TIGERweb tracts (`259` tracts, `92` claims, `\$1.54M` paid) scored | **`claim_count` vs. `exposed_buildings`:** $\rho = 0.1087$ ($p = 0.0807$); **`claim_count` vs. `flooded_land_area_m2`:** $\rho = 0.2289$ ($p = 0.0002$) |

---

## 2. Detailed Baseline Metrics by Tier

### 2.1 Tier A: Coastal Water-Level Verification (NOAA CO-OPS `9414290`, `NAVD88`)

Evaluated from `data/processed/sf_citywide/validation_ensemble_metrics.json` (`47` hourly/sub-hourly aligned samples per 48-hr AR simulation, snap distance `11.04 m`):

| Event | Observed Peak (`m NAVD88`) | Modeled Peak (`m NAVD88`) | `RMSE` (`m`) | `MAE` (`m`) | `Bias` (`m`) | Peak Timing Error (`min`) | `NSE` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ar_20211024` | `2.096` | `2.0976` | `0.0013` | `0.0010` | `+0.0007` | `0.0` | `1.0` |
| `ar_20221231` | `2.066` | `2.0661` | `0.0014` | `0.0011` | `+0.0007` | `0.0` | `1.0` |
| `ar_20230109` | `2.088` | `2.0892` | `0.0019` | `0.0015` | `+0.0013` | `0.0` | `1.0` |
| `ar_20240204` | `2.240` | `2.2411` | `0.0016` | `0.0012` | `+0.0009` | `0.0` | `1.0` |

*Note:* USGS NWIS stream gauges and STN High-Water Marks (`NGVD29 -> NAVD88` conversion) are implemented and offline-tested in `pipeline/03_validate_sf.py` for creek-dominated domains (`v0.7` San Francisquito Creek `11164500`).

### 2.2 Tier B: Authoritative Hydraulic Benchmark (SFPUC 100-Yr, `>= 0.1524 m`)

Citywide land (`>= 0.5 m NAVD88`, `NLCD != 11`) achieves `HR = 0.3887`, `FAR = 0.7086`, `CSI = 0.1998` in the frozen v0.4 ablation (`0.3900 / 0.7189 / 0.1953` in the v0.5 `validation_ensemble_metrics.json` snapshot). Corridor performance from [`docs/reports/sf_v02_validation.md`](sf_v02_validation.md):

| Corridor | Hit Rate | False Alarm Ratio | `CSI` |
| --- | ---: | ---: | ---: |
| **Alemany** | `0.4435` | `0.1309` | `0.4157` |
| **SOMA / Mission Bay** | `0.4632` | `0.3849` | `0.3591` |
| **Bayview / Islais Creek** | `0.4155` | `0.5386` | `0.2798` |
| **Mission** | `0.2596` | `0.7115` | `0.1583` |
| **Sunset / Lake Merced** | `0.3191` | `0.8260` | `0.1269` |
| **Marina / Crissy Field** | `0.3124` | `0.9024` | `0.0804` |
| **Visitacion Valley** | `0.0760` | `0.9776` | `0.0176` |

### 2.3 Tier D: 4-AR MRMS Hindcast Ensemble vs. DataSF 311 (`1,535` Valid-Land Points)

Per-event and ensemble-mean scores (`>= 0.05 m` depth threshold against `2,000` random valid-land controls):

| Event | 311 Pts | `0 m` Cap / Lift / `AUC` | `25 m` Cap / Lift / `AUC` | `50 m` Cap / Lift / `AUC` |
| --- | ---: | ---: | ---: | ---: |
| `ar_20211024` | `567` | `4.06% / 1.726x / 0.5128` | `53.62% / 1.538x / 0.5996` | `79.01% / 1.248x / 0.5865` |
| `ar_20221231` | `424` | `7.08% / 3.076x / 0.5270` | `58.49% / 1.783x / 0.6457` | `77.59% / 1.257x / 0.6293` |
| `ar_20230109` | `403` | `0.99% / 0.902x / 0.5061` | `34.74% / 1.392x / 0.6092` | `70.22% / 1.386x / 0.6209` |
| `ar_20240204` | `141` | `0.00% / 0.000x / 0.4978` | `0.71% / 0.284x / 0.5000` | `2.13% / 0.413x / 0.4798` |
| **Ensemble Mean** | **1,535** | **`3.03% / 1.426x / 0.5109`** | **`36.89% / 1.249x / 0.5886`** | **`57.24% / 1.076x / 0.5791`** |

#### Groundsource Sub-Basin Cross-Check & Holdout Storm Discovery (`sf_groundsource_floods.parquet`)

From `517` SF-clipped Groundsource records (`332` retained after `area_km2 <= 25` filtering) evaluated across `41` SF sub-basins (`>= 0.05 m` modeled depth):
- **Sub-Basin Presence:** `37 / 37` Groundsource-flooded sub-basins are captured by the model (`presence_hit_rate = 1.000`, `presence_accuracy = 0.9024`, `4` model-only sub-basins, `0` Groundsource-only).
- **Matched Ensemble Events:** All `4` modeled ARs (`ar_20211024`, `ar_20221231`, `ar_20230109`, `ar_20240204`) appear in Groundsource.
- **Top Untouched Holdout Candidates Outside the 4-AR Set:** `2024-11-22` to `2024-11-23` (`8` records, `25.897 km^2` — recent MRMS-era AR) and `2019-12-07` to `2019-12-08` (`12` records, `40.519 km^2`).

### 2.4 Tier E: OpenFEMA NFIP Structural Loss Cross-Check (`sf_citywide_100yr_slr0ft`)

Evaluated across `259` San Francisco census tracts (`44` tracts with claims, `92` total claims, `\$1,536,935.05` total paid):

| Comparison | Spearman $\rho$ | $p$-value |
| --- | ---: | ---: |
| `claim_count` vs. `exposed_buildings` (primary) | `0.1087` | `0.0807` |
| `claim_count` vs. `exposed_building_area_m2` | `0.1125` | `0.0707` |
| `claim_count` vs. `exposed_building_fraction` | `0.1073` | `0.0848` |
| `total_paid_usd` vs. `exposed_buildings` | `0.0454` | `0.4667` |
| `claim_count` vs. `flooded_land_area_m2` | `0.2289` | `0.0002` |
| `total_paid_usd` vs. `flooded_land_area_m2` | `0.1734` | `0.0051` |

*Diagnostic note:* Tract `flooded_land_area_m2` correlates more strongly with NFIP claims ($\rho = 0.2289$, $p = 0.0002$) than `exposed_buildings` ($\rho = 0.1087$), consistent with `building_elevation_bump_m: 3.0` deflecting ponded water around building footprints.

---

## 3. Diagnostic Failure Modes (Targets for `v0.6` Calibration)

1. **Moderate-AR Antecedent Moisture & Uniform Abstraction (`ar_20240204`):**  
   Under dry-start `AMC-II` conditions and uniform `5.0 mm/hr` drainage (`240 mm` over 48 hr), moderate storms such as `ar_20240204` (`54.7 mm` MRMS mean, `51.9 mm` Open-Meteo) are almost completely abstracted before surface ponding can form (`25 m` capture `0.71%`, `ROC-AUC = 0.5000`). In `v0.6`, 5-day antecedent MRMS precipitation (`AMC-II -> AMC-III`) will be tested to represent pre-storm soil saturation.
2. **Spatial Drainage & Mean-Rate Guardrail:**  
   As shown in [`docs/reports/sf_v02_vs_v04_ablation.md`](sf_v02_vs_v04_ablation.md), scaling Tier 1a drainage (`2–10 mm/hr`) linearly by NLCD imperviousness raised effective urban abstraction to `~8–9 mm/hr`, starving urban bowls and dropping SFPUC `CSI` from `0.1999` to `0.1137`. Any `v0.6` spatial drainage test must enforce **mean-rate matching** (`5.0 mm/hr` domain mean; [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) §3.3).
3. **Underpass & Highway Corridor Hydro-Enforcement:**  
   Major arterial/highway underpasses (e.g., `US-101` / `I-280` / Alemany / Cesar Chavez) are sensitive to LiDAR bridge-deck removal and `max_bump_m` carving bounds. Tier C road-closure scoring (`Praharaj et al., 2021`) provides an independent check on underpass ponding vs. over-carved artificial sinks.
4. **Park & Open-Space False Positives vs. SFPUC Urban Parcel Scope:**  
   Large coastal/park clusters (Ocean Beach, Crissy Field, Golden Gate Park) dominate citywide false alarms (`FAR ~ 0.71–0.72`) because SFPUC's 100-year layer focuses on urban parcel/street flood risk rather than natural open space.

---

## 4. Frozen Holdout-Storm Split for `v0.6` Anti-Overfitting

Per [`docs/VALIDATION_SPEC.md`](../VALIDATION_SPEC.md) §3.4, all `v0.6` calibration ablations on `sf_citywide` must obey this frozen split:

- **Calibration Storms (3 ARs, `1,394` 311 points):**
  - `ar_20211024` (Oct 24, 2021 Atmospheric River — `567` pts)
  - `ar_20221231` (Dec 31, 2022 Atmospheric River — `424` pts)
  - `ar_20230109` (Jan 9, 2023 Atmospheric River — `403` pts)
- **Untouched Holdout Set (2 ARs, `141+` 311 points):**
  - `ar_20240204` (Feb 4, 2024 Atmospheric River — `141` pts, **never tuned on**)
  - `ar_20241122` (Nov 22–23, 2024 Atmospheric River — `8` Groundsource records, `25.897 km^2`, selected via `discover_holdout_storm_candidates()`)
- **v0.6 Retention Gate:** A candidate mechanism is retained only if it (a) holds neutral or improves on the untouched holdout set, (b) improves **at least two independent tiers** without regressing SFPUC `CSI` below `0.1998`, and (c) clears the parsimony threshold (`>= +0.01` `ROC-AUC` or `>= +0.005` `CSI`).
