# v0.2 vs. v0.4 Ablation Report — San Francisco Citywide (`sf_citywide`)

**Date:** 2026-11-22  
**Control Baseline:** Frozen **v0.2 Tier 0** (`configs/sf_citywide.yml`, uniform `20 m` grid / `2 m` subgrid, `5.0 mm/hr` uniform drainage, `n_paved = 0.030`, single NOAA gauge `9414290`).  
**Evaluation Rule:** Every candidate v0.4 upgrade is ablated **one-factor-at-a-time** against frozen v0.2 on San Francisco (`SFPUC 100-Year Storm Flood Risk Map` + `DataSF 311` flooded-street logs). Only upgrades showing measurable skill improvement or verified physical necessity are retained in the default `sf_citywide` configuration; others remain cleanly implemented as opt-in flags for **v0.5** East/South Bay separate-MS4 watersheds.

---

## 1. Executive Summary

| Upgrade (Issue / Phase) | Literature Basis | Key Metric Comparison vs. v0.2 Control | Decision for `sf_citywide` |
| --- | --- | --- | --- |
| **1. Multi-Gauge Bay Tides** (#34, Phase 1) | `Nederhoff et al. (2021)` | SFPUC `CSI 0.1998` vs `0.1999`; AR `25 m ROC-AUC 0.6713` vs `0.6715`; captures `+0.34 m` MHHW amplification & `+60 min` South Bay lag | **Retained (Default)** — zero skill loss, physically required for Bay-wide tides |
| **2. Tier 1a / 1b 2D Drainage** (#35, Phase 2) | `Bates et al. (2021)` + *Experimental* | Tier 1a (`2–10 mm/hr` NLCD): SFPUC `CSI 0.1137`, `25 m AUC 0.6483`; Tier 1b (`+ tide lock`): `CSI 0.1139`, `25 m AUC 0.6487` vs Tier 0 (`0.1999 / 0.6715`) | **Opt-In (`tier0` Default)** — `8–10 mm/hr` starves dense SF urban bowls; ready for v0.5 MS4s |
| **3. 4-AR Hindcast Ensemble** (#36, Phase 3) | `Eilander et al. (2023)` | Expands validation from 1 storm (`424` pts) to 4 storms (`1,535` 311 pts); reveals Tier 0 smears/over-abstracts moderate `~52–55 mm` ARs | **Retained (Validation Suite)** |
| **4. SFINCS Quadtree (`40 m → 10 m`)** (#37, Phase 4) | `Leijnse et al. (2021)` | `0 m` 311 capture `17.22% → 19.10%` (`6 in` lift `3.92x → 4.14x`), but `25 m AUC 0.6501` vs `0.6713`, `CSI 0.1910`, runtime `3.1x–5.6x` slower (`1,585 s / 3,738 s`) | **Opt-In (`--quadtree`)** — uniform `20 m` stays citywide screening default |
| **4b. NOAA MRMS 1 km Gridded QPE** (Phase 4b) | Operational radar-gauge QPE | 4-AR ensemble `25 m` capture `27.03% → 36.95%` (`+9.92 pp`), lift `0.819x → 1.251x`, `ROC-AUC 0.5629 → 0.5888`; `50 m` capture `38.91% → 57.30%` | **Retained (`rain_source: "mrms"`)** for historical AR hindcasts |

---

## 2. Frozen v0.2 Control Baseline

From [`docs/reports/sf_v02_validation.md`](sf_v02_validation.md):
- **SFPUC 100-Year Citywide (`>= 0.1524 m` / `6 in`):** `HR = 0.3884`, `FAR = 0.7083`, `CSI = 0.1999` (Alemany `CSI = 0.4157`, SOMA / Mission Bay `0.3591`, Bayview / Islais Creek `0.2798`).
- **Dec 31, 2022 AR (`128.5 mm` Open-Meteo) vs. 424 DataSF 311 Reports (`>= 0.05 m`):**
  - `0 m`: `17.22%` capture, `3.59x` lift (`>= 0.1524 m`: `12.74%` capture, `3.92x` lift)
  - `25 m`: `67.22%` capture, `1.71x` lift, `ROC-AUC = 0.6715`
  - `50 m`: `83.49%` capture, `1.26x` lift, `ROC-AUC = 0.6596`

---

## 3. Detailed Upgrade Ablations

### 3.1 Upgrade 1: Multi-Gauge Spatially Varying Bay Tides (`Nederhoff et al., 2021`)
Replaced single-station NOAA `9414290` forcing with 4 anchor gauges interpolated along `msk = 2` via `sfincs.bnd` / `sfincs.bzs`:
- `9414290` San Francisco (Presidio): `MHHW = 1.798 m NAVD88`, `amp = 0.829 m`, `lag = 0 min`
- `9414750` Alameda: `MHHW = 1.941 m NAVD88`, `amp = 0.960 m`, `lag = +18 min`
- `9414863` Richmond: `MHHW = 1.846 m NAVD88`, `amp = 0.850 m`, `lag = +24 min`
- `9414523` Redwood City: `MHHW = 2.140 m NAVD88`, `amp = 1.158 m`, `lag = +60 min`

| Metric | Frozen v0.2 (1 Gauge) | v0.4 Phase 1 (4 Gauges) |
| --- | ---: | ---: |
| SFPUC 100-Yr (`HR / FAR / CSI`) | `0.3884 / 0.7083 / 0.1999` | `0.3887 / 0.7086 / 0.1998` |
| AR 311 `0 m` (`>= 0.05 m` lift) | `3.59x` | `3.587x` |
| AR 311 `25 m` (`>= 0.05 m` cap / lift / `AUC`) | `67.22% / 1.71x / 0.6715` | `67.22% / 1.708x / 0.6713` |
| AR 311 `50 m` (`>= 0.05 m` cap / lift / `AUC`) | `83.49% / 1.26x / 0.6596` | `83.49% / 1.258x / 0.6592` |

**Verdict:** **Retained.** Preserves San Francisco skill while resolving the estuarine tidal amplification (`+0.34 m`) and phase-lag (`+60 min`) gradient required for South/East Bay domains in v0.5.

---

### 3.2 Upgrade 2: Tier 1a / 1b 2D Gridded Drainage (`Bates et al., 2021`)
Implemented gridded `netamprfile` (`sfincs_netampr.nc`) while keeping `scsfile` (`sfincs.scs`) intact:
- **Tier 1a:** Scales urban drainage `2.0–10.0 mm/hr` by NLCD impervious fraction (`0.0` on open water and pervious land).
- **Tier 1b (Experimental):** Smoothly attenuates Tier 1a capacity as `(DEM - tide(t)) / 1.5 m -> 0`, with `polder_mask_source` exclusion.

| Metric | Tier 0 (`5.0 mm/hr`) | Tier 1a (`2–10 mm/hr` NLCD) | Tier 1b (`Tier 1a + tide lock`) |
| --- | ---: | ---: | ---: |
| SFPUC 100-Yr (`HR / FAR / CSI`) | `0.3884 / 0.7083 / 0.1999` | `0.1788 / 0.7622 / 0.1137` | `0.1800 / 0.7630 / 0.1139` |
| AR 311 `0 m` (`>= 0.05 m` cap / lift) | `17.22% / 3.59x` | `8.96% / 2.390x` | `9.20% / 2.453x` |
| AR 311 `25 m` (`>= 0.05 m` cap / lift / `AUC`) | `67.22% / 1.71x / 0.6715` | `62.26% / 1.656x / 0.6483` | `62.26% / 1.649x / 0.6487` |
| AR 311 `50 m` (`>= 0.05 m` cap / lift / `AUC`) | `83.49% / 1.26x / 0.6596` | `82.08% / 1.237x / 0.6326` | `82.31% / 1.240x / 0.6328` |

**Verdict:** **Opt-In (`drainage_tier: "tier0"` default on `sf_citywide`).** Dense SF urban bowls (`80–100%` impervious) receive `8–10 mm/hr` abstraction under Tier 1a, starving downstream ponding (`CSI 0.1999 → 0.1137`). Tier 1b recovers a minor coastal margin (`CSI 0.1139`, `0 m` lift `2.453x`) and remains ready alongside the Tier 2 `storm_drain_source` hook for separate-MS4 Bay-margin watersheds in v0.5.

---

### 3.3 Upgrades 3 & 4b: 4-AR Hindcast Ensemble (`Eilander et al., 2023`) & NOAA MRMS 1 km Gridded QPE
Evaluated 4 historical San Francisco Atmospheric Rivers (`1,535` valid-land DataSF 311 reports) under identical physics (`20 m` grid, Tier 0 `5.0 mm/hr`, 4-gauge Bay tides), comparing single-point **Open-Meteo** against **NOAA MRMS `MultiSensor_QPE_01H_Pass2`** (`1 km` radar-gauge QPE, `0` missing hours):

| Event (48-hr) | 311 Pts | Open-Meteo Rain | MRMS Mean / Max | Open-Meteo `25 m` Cap / Lift / `AUC` | MRMS `25 m` Cap / Lift / `AUC` | MRMS `50 m` Cap / Lift / `AUC` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ar_20211024` | 567 | `108.9 mm` | `132.2 / 152.5 mm` | `40.92% / 1.568x / 0.5945` | `53.62% / 1.538x / 0.5997` | `79.01% / 1.248x / 0.5866` |
| `ar_20221231` | 424 | `128.5 mm` | `88.8 / 113.8 mm` | `67.22% / 1.708x / 0.6713` | `58.73% / 1.790x / 0.6463` | `77.59% / 1.257x / 0.6294` |
| `ar_20230109` | 403 | `54.8 mm` | `67.2 / 79.5 mm` | `0.00% / 0.000x / 0.4928` | `34.74% / 1.392x / 0.6092` | `70.47% / 1.391x / 0.6224` |
| `ar_20240204` | 141 | `51.9 mm` | `54.7 / 79.3 mm` | `0.00% / 0.000x / 0.4930` | `0.71% / 0.284x / 0.5000` | `2.13% / 0.413x / 0.4798` |
| **Ensemble Mean** | **1,535** | — | — | **`27.03% / 0.819x / 0.5629`** | **`36.95% / 1.251x / 0.5888`** | **`57.30% / 1.077x / 0.5796`** |

**Verdict:** **Both Retained (`rain_source: "mrms"` for historical AR hindcasts).** Replacing single-point Open-Meteo with 1 km MRMS QPE boosts ensemble-mean `25 m` capture by **`+9.92 pp`** (`27.03% → 36.95%`), `25 m` lift by **`+0.432x`** (`0.819x → 1.251x`), `25 m ROC-AUC` by **`+0.0259`** (`0.5629 → 0.5888`), and `50 m` capture by **`+18.39 pp`** (`38.91% → 57.30%`), rescuing `ar_20230109` (`0.0% → 34.7%` at `25 m`, `ROC-AUC = 0.6092`).

---

### 3.4 Upgrade 4: SFINCS Quadtree Mesh Refinement (`Leijnse et al., 2021`)
Built via `hydromt_sfincs 2.0.0` (`40 m` uplands → `20 m` transition → `10 m` in `<= 15 m NAVD88` urban/coastal corridors, `2 m` subgrid tables):

| Metric | Uniform `20 m` (Phase 1) | Quadtree `40 m → 10 m` |
| --- | ---: | ---: |
| Active computational cells | `329,070` | `454,906` (`1.38x`) |
| Docker runtime (`100-yr` / `AR 20221231`) | `505 s` / `672 s` | `1,585 s` / `3,738 s` (`3.1x` / `5.6x`) |
| Average solver timestep (`dt`) | `1.70 s` / `1.73 s` | `0.750 s` / `0.752 s` |
| SFPUC `100-Yr` (`HR / FAR / CSI`) | `0.3887 / 0.7086 / 0.1998` | `0.3960 / 0.7306 / 0.1910` |
| AR 311 `0 m` (`>= 0.05 m` cap / lift / `AUC`) | `17.22% / 3.587x / 0.5750` | `19.10% / 3.538x / 0.5764` |
| AR 311 `0 m` (`>= 0.1524 m` cap / lift) | `12.74% / 3.919x` | `15.09% / 4.135x` |
| AR 311 `25 m` (`>= 0.05 m` cap / lift / `AUC`) | `67.22% / 1.708x / 0.6713` | `59.91% / 1.664x / 0.6501` |

**Verdict:** **Opt-In (`quadtree.enabled: false` default, `--quadtree` CLI flag).** `10 m` refinement improves exact `0 m` street-point capture (`17.22% → 19.10%`, `>= 6 in` lift `3.92x → 4.14x`) at `3.1x–5.6x` higher runtime and slightly lower `25 m ROC-AUC` (`0.6501`) and SFPUC `CSI` (`0.1910`).

---

## 4. Validated v0.4 Defaults & v0.5 Readiness
- **Default `configs/sf_citywide.yml`:**
  - **Mesh:** Uniform `20 m` grid / `2 m` subgrid (`quadtree.enabled: false`, `--quadtree` available for targeted studies).
  - **Coastal Tides:** 4-gauge NOAA CO-OPS spatial interpolation (`9414290`, `9414750`, `9414863`, `9414523`).
  - **Drainage:** `drainage_tier: "tier0"` (`5.0 mm/hr` + `scsfile` CN infiltration); `tier1a`, `tier1b`, and `tier2` (`storm_drain_source`) ready for **v0.5** East/South Bay MS4 watersheds.
  - **Precipitation:** NOAA Atlas 14 alternating-block design storms (`10-yr` / `100-yr`) + **NOAA MRMS 1 km gridded QPE** (`rain_source: "mrms"`) for historical AR hindcasts.
