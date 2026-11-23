# VALIDATION_SPEC.md — Multi-Source Hydrodynamic Validation Specification

Operational validation specification for `bay-urban-flood-map` across San Francisco (`v0.4`), East/South Bay separate-MS4 pilots (`v0.5`), and the 9-county regional mosaic (`v1.0`). Designed to pair with [`docs/MODEL_SPEC.md`](MODEL_SPEC.md), [`docs/DATA_CONTRACTS.md`](DATA_CONTRACTS.md), and [`docs/METHODS.md`](METHODS.md).

---

## 1. Purpose, Scope & Hard Invariants

Because no single observational dataset captures every urban flood mechanism—gauges miss street ponding, 311 logs carry demographic/reporting bias, and satellites miss short-lived urban flash floods—`bay-urban-flood-map` uses a **triangulated, multi-source validation matrix**.

### Hard Invariants
- **Coordinate System & Units:** All raster downscaling, spatial buffering, and metric scoring run strictly in **meters**, **`EPSG:26910`** (NAD83 / UTM Zone 10N), and vertical datum **`NAVD88`**. Any `NGVD29` or `MLLW` gauge/high-water-mark record must be converted to `NAVD88` meters before scoring; never alter the base DEM datum.
- **Valid Land Mask (`land_valid_mask`):** Exclude open water (`NLCD == 11`) and sub-tidal/offshore cells (`hydro_dem_m < min_land_elevation_m`, default `0.5 m NAVD88`) from pluvial skill scores (`HR`, `FAR`, `CSI`, `ROC-AUC`, and random control sampling) so Bay/Ocean water never inflates or dilutes urban metrics.
- **Downscaling Standard:** Bilinearly interpolate coarse SFINCS peak water-surface elevation (`zsmax`) onto the `2 m` (or `1 m` benchmark) hydro-enforced DEM:
  $$d_{\text{2m}} = \max(0,\; \text{zsmax}_{\text{2m}} - \text{dem}_{\text{2m}})$$
- **Offline Testability:** All scoring primitives in `pipeline/03_validate_sf.py` (and future regional validators) must remain pure functions separated from file I/O and covered by fast, offline `pytest` fixtures—never triggering live downloads or SFINCS Docker runs inside tests.

### Peer-Reviewed Literature Contribution Map
Each validation tier and scoring rule in this specification is derived from specific peer-reviewed studies, while clearly distinguishing established literature from project-specific engineering extensions:

| Validation Component | Source & Citation | What Is Directly Derived From It | Spec Section & Status |
| --- | --- | --- | --- |
| **Tier A: SFINCS gauge/HWM & compound driver validation** | `Grimley et al. (2025)` | Validating SFINCS peak water levels across multi-county domains against USGS/FEMA high-water marks (HWMs) and stream/tide gauges (`RMSE`, `MAE`, `Bias`), plus pluvial-vs.-coastal driver attribution | Section 2 (Tier A); planned **v0.5+** |
| **Tier B: Local CA hydraulic benchmarks & regional drainage regimes** | `Sanders et al. (2023)`; `Schubert, Mach, & Sanders (2024)` | Demonstrates why continental/national flood models diverge from local infrastructure-resolving California models (`PRIMo` / `PRIMo-Drain`), motivating local flood-control benchmarks (SFPUC, Valley Water, ACFCD, OneShoreline) and county/watershed-specific drainage regimes | Sections 2 (Tier B) & 3.4; **v0.1–v0.4** (SFPUC), **v0.5+** (regional) |
| **Tier C: Roadway flood disruption validation** | `Praharaj et al. (2021)` | Filtering and scoring crowdsourced/agency roadway flood reports against local topography, depressions, and road corridors (adapted here to open Caltrans/CHP/511 feeds and optional open Waze-for-Cities archives) | Section 2 (Tier C); planned **v0.4+ / v0.5+** |
| **Tier D: Municipal 311 spatial capture, bias awareness & `ROC-AUC`** | `Agonafir et al. (2022)`; `Lerma et al. (2025)` | Geo-located 311 flood/sewer complaint scoring with circular spatial buffers, random control sampling, subtype breakdown (`Flooding` vs. `Sewage_back_up`), demographic/reporting-bias caveats, and Mann-Whitney `ROC-AUC` | Section 2 (Tier D); implemented **v0.2** (`03_validate_sf.py`) |
| **Multi-storm AR hindcast ensemble protocol** | `Eilander et al. (2023)` | Reproducible multi-event historical compound flood hindcast validation in `HydroMT-SFINCS` to separate generalizable model skill from single-storm overfitting | Section 3.2; **Implemented v0.4 (4-AR MRMS ensemble)** |
| **Tier E: Structural flood-loss cross-check (NFIP)** | `Wing et al. (2020)` | Leveraging FEMA NFIP claims big data to cross-check modeled spatial flood exposure and loss hotspots where physical gauges are sparse | Section 2 (Tier E); planned **v0.5+** |
| **News-derived flood archives & mean-rate drainage guardrail** | *Project engineering extensions* | Filtering Google Research Groundsource (`CC BY 4.0`) to `area_km2 <= 25` for storm discovery/sub-basin presence, and requiring mean-rate-matched drainage ablations after v0.4 Phase 2 | Sections 2 (Tier D) & 3.3; **v0.4+** |

---

## 2. Five-Tier Open Validation Source Matrix

Each validation dataset is assigned to a tier based on its spatial precision, physical meaning, and Bay-wide availability.

| Tier | Source(s) & Provenance | What It Validates | Primary Metrics | Spatial Scale & Applicability |
| --- | --- | --- | --- | --- |
| **A: Physical Ground Truth** | USGS NWIS stream gauges, USGS STN High-Water Marks (HWMs), NOAA CO-OPS tide gauges, county ALERT / OneRain (`Grimley et al., 2025`; `Nederhoff et al., 2021`) | Peak water-surface elevation (`NAVD88`), hydrograph timing, compound tidal tailwater | `RMSE` (m), `MAE` (m), `Bias` (m), peak timing error (min), `NSE` / `KGE` | Point / reach; critical in **v0.5+** creek & Bay-margin watersheds (sparse in piped SF core) |
| **B: Authoritative Hydraulic Benchmarks** | SFPUC 100-Yr Flood Risk Map (SF); Valley Water, ACFCD, OneShoreline, Marin creek/flood maps; FEMA NFHL sanity check (`Sanders et al., 2023`; `Schubert, Mach, & Sanders, 2024`) | Design-storm inundation extent (`>= 0.1524 m` / `6 in`) | `HR`, `FAR`, `CSI`, `F1`, corridor breakdown, top FP clusters | Polygon-to-raster; SF (`v0.1–v0.4`) and county pilots (`v0.5+`) |
| **C: Roadway & Infrastructure Disruptions** | Caltrans QuickMap / PeMS, CHP incident logs, 511 SF Bay transit/road alerts *(open agency feeds only)* (`Praharaj et al., 2021`) | Highway/underpass & arterial flooding; verifies bridge/underpass hydro-enforcement | Road-snapped `25 m / 50 m` capture, lift vs. random road controls, `ROC-AUC` | Linear/point along major corridors; all 9 counties (`v0.4+ / v0.5+`) |
| **D: Crowdsourced Civic & News Archives** | Municipal 311 flood/sewer logs (`Agonafir et al., 2022`; `Lerma et al., 2025`) + **Google Research Groundsource** (`CC BY 4.0`, *project extension*) | Transient street/neighborhood flooding across multi-storm AR ensembles | **311:** `0 / 25 / 50 m` capture, lift vs. `2,000` land controls, Mann-Whitney `ROC-AUC`. **Groundsource:** neighborhood/sub-basin presence & storm discovery | **311:** `25–50 m` point buffers. **Groundsource:** polygon-level (`area_km2 <= 25`), **not** `25 m` point `ROC-AUC` |
| **E: Structural Loss & Remote Sensing** | **OpenFEMA NFIP Redacted Claims** (`Wing et al., 2020`) + **Sentinel-1 SAR** (Copernicus) | Multi-year property flood loss hotspots (NFIP); broad standing-water extent (SAR) | **NFIP:** census-tract Spearman $\rho$ vs. modeled building exposure. **SAR:** `CSI` / `HR` on open floodplains | **NFIP:** census tract / `0.1°` grid (`v0.5+`). **SAR:** flat East/South Bay floodplains only (excluded in dense SF urban canyons) |

### Tier C & D Licensing / Access Guardrails
- **No ToS-violating scraping or gated feeds:** Consumer Google Maps crisis layers must not be scraped. Google **Roads Management Insights (RMI)** and **Waze for Cities** feeds are restricted to partner public agencies; include them *only* if an agency publishes an open historical export.
- **Google Research Groundsource (`CC BY 4.0`):** Hosted as GeoParquet on Zenodo / Google Earth Engine (`area_km2`, `start_date`, `end_date`, WGS84 geometry). Filter to `area_km2 <= 25.0` and intersect with Bay Area sub-basins to (1) discover impactful historical AR dates across counties lacking 311 APIs and (2) verify neighborhood/city-level flood presence.

---

## 3. Multi-Storm Ensemble, Ablation & Regional Regime Rules

### 3.1 Frozen Control Baseline
The **v0.2 Tier 0** San Francisco baseline (`configs/sf_citywide.yml`, `20 m / 2 m`, `5.0 mm/hr`, `n_paved = 0.030`, single gauge `9414290`) is **frozen and immutable**:
- **SFPUC 100-Yr Citywide (`>= 0.1524 m`):** `HR = 0.3884`, `FAR = 0.7083`, `CSI = 0.1999` (`docs/reports/sf_v02_validation.md`).
- **Dec 31, 2022 AR vs. 311 (`>= 0.05 m`):** `0 m / 25 m / 50 m` lift `3.59x / 1.71x / 1.26x`, `25 m ROC-AUC = 0.6715`.
- Validation runs for v0.4+ ablations must **not** overwrite `docs/reports/sf_v02_validation.md`; ensemble and ablation reports write to dedicated report paths (e.g., `docs/reports/sf_v02_vs_v04_ablation.md`).

### 3.2 Multi-Storm AR Hindcast Ensemble (`Eilander et al., 2023`)
- Evaluate **3–4 historical SF atmospheric rivers** (e.g., `Oct 2021`, `Dec 31, 2022`, `Jan 2023`, `Feb 2024`) spanning different storm intensities and tidal phases (`Eilander et al., 2023`). As of v0.4 Phase 4b, **NOAA MRMS `MultiSensor_QPE_01H_Pass2` (`1 km` radar-gauge QPE)** is the default historical rainfall forcing (`rain_source: "mrms"`), which belongs to the modeling/forcing layer rather than the observational validation layer.
- Report **per-event** metrics and **ensemble-mean** (plus min–max range) for `ROC-AUC`, capture rate, and lift across `0 / 25 / 50 m` buffers at both `>= 0.05 m` (`2 in`) and `>= 0.1524 m` (`6 in`), alongside subtype (`Flooding` vs. `Sewage_back_up`) and missed-cluster diagnostics (`Agonafir et al., 2022`; `Lerma et al., 2025`).

### 3.3 Controlled A/B Ablation & Mean-Rate-Matched Guardrail
- **One-factor-at-a-time:** Each methodology upgrade (multi-gauge tides, Tier 1a/1b drainage, quadtree mesh) is ablated independently against the frozen v0.2 control before combining.
- **Retain criterion:** Keep an upgrade as the regional default only if it improves ensemble skill (`CSI`, `25 m ROC-AUC`, `0 m` lift) **or** provides verified physical necessity (e.g., multi-gauge tidal amplification/phase lag toward South Bay; `Nederhoff et al., 2021`) without degrading citywide skill.
- **Mean-rate-matched drainage guardrail (Phase 2 lesson):** When comparing spatially varying 2D drainage (`Tier 1a/1b`, `netamprfile`; `Bates et al., 2021`) against uniform `Tier 0` (`5.0 mm/hr`), do not conflate spatial pattern with a higher domain-mean abstraction rate (e.g., `2–10 mm/hr` scaled on dense SF imperviousness raised effective urban removal to `~8–9 mm/hr`, starving urban bowls and dropping `CSI` `0.1999 -> 0.1137`). Test rate-matched or slope-aware parameterizations when tuning MS4 regimes.

### 3.4 Regional Parameterization Without County-Line Seams
- **Watershed/sub-basin boundaries, county defaults:** Following `Sanders et al. (2023)` and `Schubert, Mach, & Sanders (2024)`—which show that uniform national drainage assumptions mischaracterize local California infrastructure—model domains follow hydrological sub-basins (`v0.5+` / `v1.0`) while inheriting drainage-tier defaults from the governing flood-control regime rather than hardcoding arbitrary county-line jumps:
  - **Combined-sewer core (SFPUC):** `Tier 0` (`5.0 mm/hr`) default unless rate-matched Tier 1 proves superior across the multi-AR ensemble.
  - **Separate MS4 flatlands (ACFCD, Valley Water, OneShoreline, Contra Costa):** `Tier 1a/1b` with county design-standard caps, explicit `polder_mask_source` exclusions for levee-protected pumped basins (e.g., Foster City, Redwood Shores, Bay Farm Island), and optional `Tier 2` `domain.storm_drain_source` municipal GIS enrichment.

---

## 4. Config & JSON Output Contracts

### 4.1 Backward-Compatible YAML Extensions (`configs/*.yml`)
Existing v0.2 single-event keys (`validation.hindcast_scenario`, `domain.validation_311_reference`) remain supported. Multi-storm and multi-source configs add optional lists:

```yaml
validation:
  depth_threshold_m: 0.1524
  min_land_elevation_m: 0.5
  report_markdown_file: "docs/reports/sf_v02_vs_v04_ablation.md"
  ensemble_metrics_file: "data/processed/sf_citywide/validation_ensemble_metrics.json"
  hindcast_ensemble:
    - id: "ar_20211024"
      scenario: "sf_citywide_ar_20211024"
      rain_event: "rain_ar_20211024"
      slr_increment: "tide_ar_20211024"
      points_311_file: "data/raw/sf_citywide/sf_311_20211024_floods.gpkg"
      road_closures_file: null
      gauges_file: null
    - id: "ar_20221231"
      scenario: "sf_citywide_ar_20221231"
      rain_event: "rain_ar_20221231"
      slr_increment: "tide_ar_20221231"
      points_311_file: "data/raw/sf_citywide/sf_311_20221231_floods.gpkg"
      road_closures_file: null
      gauges_file: null
  sources:
    groundsource_file: null     # Optional CC BY 4.0 GeoParquet filtered to bbox
    nfip_claims_file: null      # Optional OpenFEMA tract-aggregated claims
```

### 4.2 Standardized Ensemble Metrics JSON (`validation_ensemble_metrics.json`)
```json
{
  "region": "sf_citywide",
  "drainage_tier": "tier0",
  "design_benchmark": {
    "source": "sfpuc_100yr",
    "depth_threshold_m": 0.1524,
    "hit_rate": 0.3887,
    "false_alarm_ratio": 0.7086,
    "csi": 0.1998
  },
  "ensemble_summary": {
    "n_events": 4,
    "buffers_m": [0.0, 25.0, 50.0],
    "mean_roc_auc_25m": 0.0,
    "mean_capture_05m_25m": 0.0,
    "mean_lift_05m_25m": 0.0
  },
  "events": [
    {
      "event_id": "ar_20221231",
      "scenario_id": "sf_citywide_ar_20221231",
      "tiers_evaluated": ["311"],
      "ar_311_validation": {},
      "road_closures_validation": null,
      "gauge_hwm_validation": null
    }
  ],
  "regional_crosschecks": {
    "groundsource_subbasin_match": null,
    "nfip_tract_spearman_rho": null
  }
}
```

---

## 5. Upstream Validation Data Licensing & Attribution (for `docs/DATA_SOURCES.md`)

| Dataset | Provider / Source | License / Terms | Usage & Attribution Notes |
| --- | --- | --- | --- |
| **USGS NWIS & STN High-Water Marks** | USGS Water Data / Flood Event Viewer | **Public Domain** | Credit *U.S. Geological Survey*. Convert any `NGVD29` records to `NAVD88` meters. |
| **Caltrans QuickMap / CHP Incident Logs / 511 SF Bay** | Caltrans / CHP / MTC 511 | **Public / CA Open Data** | Open agency feeds for highway & underpass flood closures. |
| **Google Research Groundsource** | Google Research (Zenodo / GEE GeoParquet) | **CC BY 4.0** | Credit *Google Research Groundsource (CC BY 4.0)*. Filter `area_km2 <= 25` for storm discovery & neighborhood presence. |
| **OpenFEMA NFIP Redacted Claims (`FimaNfipClaims`)** | FEMA OpenFEMA API | **Public Domain** | Tract / `0.1°` privacy-aggregated; use only for tract-level loss correlation (`v0.5+`). |
| **Copernicus Sentinel-1 SAR** | ESA / Copernicus Data Space | **Copernicus Open Access** | Credit *Copernicus Sentinel data*. Flat Bay-margin/floodplain extent only (`v0.5+`). |

---

## 6. Phased Implementation Rollout

1. **v0.4 (Complete — San Francisco Ensemble & Ablation):** Implemented Tier D 4-AR 311 ensemble scoring (`Oct 2021`, `Dec 31 2022`, `Jan 2023`, `Feb 2024`), 4-gauge Bay tides, Tier 1a/1b/2 drainage hooks, opt-in quadtree refinement, and NOAA MRMS 1 km QPE forcing in [`docs/reports/sf_v02_vs_v04_ablation.md`](reports/sf_v02_vs_v04_ablation.md).
2. **v0.5 (Active — [`docs/plan/PLAN_V05.md`](plan/PLAN_V05.md)):** Phase 1 builds and verifies all 5 validation tiers (Tier A USGS/NOAA, Tier B hydraulic benchmarks, Tier C Caltrans/CHP/511, Tier D 311 + Groundsource, Tier E OpenFEMA NFIP) on the completed **San Francisco (`sf_citywide`)** v0.4 outputs first; Phases 2–4 then apply the verified multi-tier validator and SFINCS `pump` structures to the **San Francisquito Creek / East Palo Alto** separate-MS4 pilot.
3. **v1.0 (9-County Regional Mosaic):** Automated per-sub-basin validation summaries aggregated into a regional accuracy dashboard.

### Peer-Reviewed References
- **Agonafir, C., Ramirez Pabon, A., Lakhankar, T., Khanbilvardi, R., & Devineni, N. (2022).** Understanding New York City street flooding through 311 complaints. *Journal of Hydrology*, 605, 127300. [https://doi.org/10.1016/j.jhydrol.2021.127300](https://doi.org/10.1016/j.jhydrol.2021.127300)
- **Bates, P. D., Quinn, N., Sampson, C., Smith, A., Wing, O., et al. (2021).** Combined modeling of US fluvial, pluvial, and coastal flood hazard under current and future climates. *Water Resources Research*, 57(2), e2020WR028673. [https://doi.org/10.1029/2020WR028673](https://doi.org/10.1029/2020WR028673)
- **Eilander, D., Couasnon, A., Leijnse, T., Ikeuchi, H., Yamazaki, D., et al. (2023).** A globally applicable framework for compound flood hazard modeling. *Natural Hazards and Earth System Sciences*, 23, 823–846. [https://doi.org/10.5194/nhess-23-823-2023](https://doi.org/10.5194/nhess-23-823-2023)
- **Grimley, L. E., Sebastian, A., Leijnse, T., Eilander, D., Ratcliff, J., & Luettich, R. (2025).** Determining the relative contributions of runoff, coastal, and compound processes to flood exposure across the Carolinas during Hurricane Florence. *Water Resources Research*, 61(3), e2023WR036727. [https://doi.org/10.1029/2023WR036727](https://doi.org/10.1029/2023WR036727)
- **Lerma, N. R., Goodall, J. L., & Quinn, J. D. (2025).** Uncovering the drivers of urban flood reports: An environmental and socioeconomic analysis using 311 data. *Water*, 17(21), 3178. [https://doi.org/10.3390/w17213178](https://doi.org/10.3390/w17213178)
- **Nederhoff, K., Saleh, R., Tehranirad, B., Herdman, L., Erikson, L., Barnard, P. L., et al. (2021).** Drivers of extreme water levels in a large, urban, high-energy coastal estuary – A case study of the San Francisco Bay. *Coastal Engineering*, 170, 103984. [https://doi.org/10.1016/j.coastaleng.2021.103984](https://doi.org/10.1016/j.coastaleng.2021.103984)
- **Praharaj, S., Zahura, F. T., Chen, T. D., Shen, Y., Zeng, L., & Goodall, J. L. (2021).** Assessing trustworthiness of crowdsourced flood incident reports using Waze data: A Norfolk, Virginia case study. *Transportation Research Record*, 2675(12), 650–662. [https://doi.org/10.1177/03611981211031212](https://doi.org/10.1177/03611981211031212)
- **Sanders, B. F., Schubert, J. E., Kahl, D. T., Mach, K. J., Brady, D., et al. (2023).** Large and inequitable flood risks in Los Angeles, California. *Nature Sustainability*, 6, 47–57. [https://doi.org/10.1038/s41893-022-00977-7](https://doi.org/10.1038/s41893-022-00977-7)
- **Schubert, J. E., Mach, K. J., & Sanders, B. F. (2024).** National-scale flood hazard data unfit for urban risk management. *Earth's Future*, 12(7), e2024EF004549. [https://doi.org/10.1029/2024EF004549](https://doi.org/10.1029/2024EF004549)
- **Wing, O. E. J., Pinter, N., Bates, P. D., & Kousky, C. (2020).** New insights into US flood vulnerability revealed from flood insurance big data. *Nature Communications*, 11, 1444. [https://doi.org/10.1038/s41467-020-15264-2](https://doi.org/10.1038/s41467-020-15264-2)
