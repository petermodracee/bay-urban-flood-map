# METHODS.md — Scientific Methodology & Reference Formulation

This document is the living scientific methodology reference for `bay-urban-flood-map`. It explains what each peer-reviewed source contributes to the pipeline, documents the exact formulations and parameters used in the frozen **v0.2** San Francisco baseline, **v0.4** upgrades, and **v0.6** anti-overfitting calibration ([`docs/reports/sf_v06_calibration_report.md`](reports/sf_v06_calibration_report.md)), and distinguishes mitigable model limitations from structural 2D-modeling boundaries. For the 5-tier multi-source validation protocol (`v0.5+`), see [`docs/VALIDATION_SPEC.md`](VALIDATION_SPEC.md).

For operational pipeline contracts and CLI stages, see [`docs/MODEL_SPEC.md`](MODEL_SPEC.md). For upstream data licenses and provenance, see [`docs/DATA_SOURCES.md`](DATA_SOURCES.md).

---

## 1. Modeling Scope & Literature Provenance

`bay-urban-flood-map` is a **screening-level 2D overland pooling and compound sea level rise (SLR) flood hazard model** for the San Francisco Bay Area. It fills the gap between coastal bathtub SLR viewers (which omit rainfall runoff) and FEMA riverine/coastal floodplains (which omit localized urban pluvial ponding). Outputs must not be used as parcel-exact engineering design depths or regulatory FEMA determinations.

### Peer-Reviewed Literature Contribution Map
Each component of the pipeline is derived from—or benchmarked against—specific peer-reviewed methods, while clearly separating established literature from our own experimental extensions:

| Component | Source & Citation | What Is Derived From It | Status |
| --- | --- | --- | --- |
| **Automated model assembly** | `Eilander et al. (2023)` | Reproducible `HydroMT-SFINCS` clipping, reprojection, forcing assembly, and multi-event hindcast validation protocol | Implemented (`02_build_sfincs.py`, 4-AR ensemble in **v0.4**, 5-AR cal/holdout in **v0.6**) |
| **Reduced-physics compound solver** | `Leijnse et al. (2021)` | 2D local-inertial shallow-water equations with advection/viscosity, coupled rain + tide boundaries, and quadtree mesh refinement | Implemented (`v0.2` regular grid; `--quadtree` `40 m → 10 m` opt-in in **v0.4**) |
| **Subgrid topography & friction** | `van Ormondt et al. (2025)` | Exact subgrid volume–depth and wet-area conveyance lookup tables (`sfincs.sbg`) mapping `2 m` terrain onto `20 m` cells | Implemented (`v0.1`/`v0.2`) |
| **Urban building representation** | `Schubert & Sanders (2012)` | Building block-out (`+3.0 m` elevation bump + elevated roughness) to route runoff along street corridors rather than inside buildings | Implemented (`01_prep_terrain.py`) |
| **Upscaled 2D urban raster concepts** | `Sanders & Schubert (2019)` | Fine-scale urban storage/conveyance retention and simplified drainage abstraction concepts for fast regional raster inundation | Conceptual design reference |
| **2D urban drainage proxy** | `Wing et al. (2017)` | Design-standard 2D urban drainage abstraction (with project-specific **NLCD imperviousness** scaling in Tier 1a) instead of unobtainable 1D pipe-invert GIS | Implemented (`--drainage-tier tier1a`, `--mean-rate-matched`); SF default remains Tier 0 (`5.0 mm/hr` effective parameter) after v0.4 & v0.6 ablations |
| **Tide-locked outfall heuristic** | *Experimental extension* | Attenuating Tier 1a 2D drainage capacity as `(DEM - tide(t)) -> 0` (excluding pumped polders) | Implemented (`--drainage-tier tier1b`, **v0.4**); opt-in for **v0.7+** MS4s |
| **SF Bay tidal amplification** | `Nederhoff et al. (2021)` | Spatially varying estuarine water levels (tidal amplification toward South Bay + surge) via multi-gauge NOAA CO-OPS interpolation | **Retained (`v0.4`)** 4-gauge `sfincs.bnd`/`sfincs.bzs` (`9414290`, `9414750`, `9414863`, `9414523`) |
| **Gridded radar-gauge QPE** | NOAA MRMS (`MultiSensor_QPE_01H_Pass2`) | `1 km` hourly gauge-corrected radar precipitation (`netamprfile`) resolving convective AR rain bands and orographic gradients | **Retained (`v0.4`)** for historical AR hindcasts (`rain_source: "mrms"`) |
| **311 crowdsourced validation** | `Agonafir et al. (2022)`; `Lerma et al. (2025)` | Using geo-located municipal 311 flood complaints with buffer capture, control sampling, and `ROC-AUC` to validate urban pluvial models | Implemented (`03_validate_sf.py`, 5-AR cal/holdout ensemble in **v0.6**) |

---

## 2. Study Domain, Coordinate Systems & Core Data Sources

All terrain processing, hydrodynamic modeling, and raster scoring are performed strictly in:
- **Horizontal CRS:** `EPSG:26910` (NAD83 / UTM Zone 10N, meters)
- **Vertical Datum:** `NAVD88` (meters)
- **Web Export CRS:** `EPSG:3857` (Web Mercator) Cloud Optimized GeoTIFFs (COGs)

Core datasets (full citations and licenses in [`docs/DATA_SOURCES.md`](DATA_SOURCES.md)):
- **Topobathy DEM:** USGS 3DEP 1 m–2 m LiDAR DEM (`NAVD88`).
- **Shoreline Defenses:** SFEI San Francisco Bay Shore Inventory (`SFBayShoreInventory_SFEI_2016_0`, `Z_Mean` crest elevations).
- **Urban Features:** OpenStreetMap roads/rail (`bridge`, `tunnel`, `layer`) and DataSF / OSM building footprints.
- **Land Cover & Soils:** USGS Annual NLCD (land cover + % imperviousness) and USDA SSURGO Hydrologic Soil Groups (`HSG` A–D).
- **Hydrometeorology & Validation:** NOAA Atlas 14 Vol. 6 IDF depths, Open-Meteo archive hourly rainfall, NOAA CO-OPS tide gauge `9414290` (San Francisco Presidio), SFPUC 100-Year Storm Flood Risk Map, and DataSF 311 flooding service requests.

---

## 3. Terrain Hydro-Enforcement & Urban Feature Representation

Raw LiDAR DEMs contain elevated bridge decks and smoothed coastal berms that block 2D overland flow. `pipeline/01_prep_terrain.py` applies four deterministic steps at `2 m` subgrid resolution (`1 m` in v0.1):

1. **Building Block-Out (`Schubert & Sanders, 2012`):**
   Following Schubert & Sanders' *building block-out (BB)* treatment, building footprints are raised by `+3.0 m` (`building_handling: "elevation_bump"`) and assigned elevated roughness (`n = 0.30`), preventing artificial ponding inside structures and forcing runoff along street corridors.
2. **Bounded Underpass Hydro-Enforcement:**
   Road/rail crossings tagged `bridge`, `tunnel`, or `layer` are carved linearly between approach medians (`min_bump_m = 0.15 m`, `carve_half_width_m = 5.0 m`), with reproducible YAML QA overrides (`carve_exclude_osm_ids`, `manual_underpass_cuts` in `v0.6`). Corridors exceeding `max_bump_m = 6.0 m` are skipped so bored hillside tunnels (e.g., Broadway and Stockton tunnels) are not carved into open canyons.
3. **Upward-Only Shoreline Crest Enforcement:**
   SFEI engineered shoreline defense classes (levees, seawalls, floodwalls, berms, engineered road embankments) are burned upward-only (`dem = max(dem, Z_Mean)`) across a `±2.0 m` half-width so narrow coastal defenses are preserved in coarse subgrid tables.
4. **Roughness & SCS Curve Number Grids:**
   Manning's $n$ is mapped by NLCD class and impervious fraction (`n_paved = 0.030`, `n_water = 0.020`, `n_open_space = 0.035`, `n_vegetation = 0.060`), and SCS Curve Numbers ($CN \in [30, 100]$; USDA NRCS NEH-630 Ch. 9, *Hydrologic Soil-Cover Complexes*) are assigned from NLCD × USDA SSURGO HSG (defaulting urban fill/unclassified cells to HSG `D`).

---

## 4. Hydrodynamic Solver & Subgrid Formulation

Simulations use **Deltares SFINCS** (`deltares/sfincs-cpu`) assembled automatically via `HydroMT-SFINCS` (`Leijnse et al., 2021`; `Eilander et al., 2023`):

- **Governing Physics (`Leijnse et al., 2021`):** Solves the 2D local-inertial shallow-water equations with advection (`advection = 1`), viscosity (`viscosity = 1`), wetting/drying threshold `huthresh = 0.01 m`, and CFL factor `alpha = 0.75`.
- **Subgrid Lookup Tables (`van Ormondt et al., 2025`; cf. `Sanders & Schubert, 2019`):** A `20 m` computational grid (`650 × 620`, `329,070` active cells for citywide SF) stores pre-computed subgrid volume–depth and wet-area conveyance relationships (`sfincs.sbg`, `nr_subgrid_pixels = 10`, `nbins = 10`, `max_gradient = 5.0`) derived directly from the `2 m` hydro-enforced DEM and Manning's $n$ grid.
- **Infiltration:** Spatially distributed SCS Curve Number soil abstraction (`scsfile`) handles pervious rainfall losses.
- **Downscaling:** Peak coarse water-surface elevations (`zsmax`) are bilinearly interpolated onto the `2 m` hydro-enforced DEM (`depth_m = max(0, zsmax_2m - dem_2m)`).

---

## 5. Storm-Drain Abstraction — Three-Tier Progressive Architecture

Because complete 1D underground storm-pipe invert elevations and pump curves do not exist uniformly across the 9-county SF Bay Area (`Wing et al., 2017`; [`docs/DECISIONS.md`](DECISIONS.md)), urban drainage is parameterized in three progressive tiers:

| Tier | Method & Provenance | Data Required | SFINCS Mechanism |
| --- | --- | --- | --- |
| **Tier 0** *(v0.1 / v0.2 frozen baseline)* | Effective (calibrated) uniform subtraction (`5.0 mm/hr`, not physical pipe capacity; well below SFPUC's SSIP LOS 5-yr 3-hr mean of `10.84 mm/hr` and peak-hour `~18.3 mm/hr`, San Francisco Public Works, 2017, Sec. 3.1 & Table 3, approximate comparison on 1941 City IDF vs. NOAA Atlas 14) | None | Scalar subtraction on `sfincs.prp` (`max(0, rain - 5.0)`) + SCS CN |
| **Tier 1a** *(v0.4, extended from `Wing et al., 2017`)* | NLCD imperviousness-scaled 2D drainage abstraction | NLCD imperviousness + land cover | 2D gridded net rainfall (`netamprfile`) + SCS CN |
| **Tier 1b** *(v0.4, experimental extension)* | Dynamic tide-coupled gravity-outfall attenuation | DEM + NOAA CO-OPS tide + pumped-polder flags | Attenuates Tier 1a capacity in `netamprfile` as `(DEM - tide(t)) -> 0` |
| **Tier 2** *(v0.7+ optional enrichment)* | Local refinement where open municipal storm-drain / pump GIS exists | Optional `domain.storm_drain_source` (`null` by default) | Locally refines Tier 1 grid + SFINCS `pump` / `culvert` structures |

---

## 6. Coastal Boundary Conditions & Compound Scenario Design

- **Domain Masking (`msk`):** Offshore `NoData` cells beyond the coastal DEM extent are masked inactive (`msk = 0`); Bay and Pacific shoreline perimeter cells (`z <= 0.5 m NAVD88`) receive dynamic water-level forcing (`msk = 2` via `sfincs.bnd` / `sfincs.bzs`); upland southern San Mateo County border cells (`z > 0.5 m`) act as open outflow (`msk = 3`).
- **2024 CA OPC Coincident Stress Scenarios:** Crosses `10-yr` (`90.4 mm` / 24 hr) and `100-yr` (`144.0 mm` / 24 hr) 15-minute alternating-block NOAA Atlas 14 storms with fixed `0`, `+1`, `+3`, `+5 ft` SLR increments added onto a dynamic 24-hr semidiurnal tidal hydrograph (`MHHW 1.85 m + 0.35 m surge`; joint return period undefined), plus historical 48-hr hourly MRMS Atmospheric River hindcasts. Because the `5.0 mm/hr` effective drainage rate was evaluated on hourly MRMS hindcasts but applied to 15-minute alternating-block stress scenarios, synthetic extents may be biased wet and are sensitive to storm shape (replacing the alternating block with the Atlas 14 50% median temporal distribution cuts 100-yr 0 ft flooded area $\ge 0.05\text{ m}$ by ~44%, from `7.373` to `4.160 km²`).
- **Derived SLR Delta:** Added compound flood depth from sea level rise is isolated as:
  $$\Delta d_{\text{SLR}} = \max\bigl(0,\; d(\text{Storm} + \text{SLR}) - d(\text{Storm} + 0\,\text{ft})\bigr)$$
- **Multi-Gauge Bay Interpolation (`v0.4`, `Nederhoff et al., 2021`):** San Francisco Bay exhibits strong tidal amplification toward the South Bay plus localized surge/wind setup. Validated v0.4 supplies 4 NOAA CO-OPS anchor stations (`9414290` Presidio, `9414750` Alameda, `9414863` Richmond, `9414523` Redwood City) in `sfincs.bnd` / `sfincs.bzs` for spatial interpolation along `msk = 2`. Full v0.2 vs. v0.4 ablation results live in [`docs/reports/sf_v02_vs_v04_ablation.md`](reports/sf_v02_vs_v04_ablation.md).

---

## 7. Dual Validation Protocol & Frozen v0.2 Baseline

Every upgrade is evaluated against two complementary datasets in `pipeline/03_validate_sf.py`:

1. **Design-Storm Benchmark (SFPUC 100-Year Storm Flood Risk Map):**
   Downscaled `2 m` depths are thresholded at `>= 0.1524 m` (`6 in`) over valid land (`dem >= 0.5 m NAVD88`, excluding `NLCD 11` open water) and scored using **Hit Rate (HR)**, **False Alarm Ratio (FAR)**, and **Critical Success Index (CSI)** citywide and across 7 urban corridors.
2. **Real-Storm Crowdsourced Validation (`Agonafir et al., 2022`; `Lerma et al., 2025`):**
   Following Agonafir et al.'s use of 311 street-flooding/sewer-backup logs and Lerma et al.'s `ROC-AUC` evaluation, historical hindcasts are scored against geo-located 311 reports within `0 m`, `25 m`, and `50 m` circular buffers at `>= 0.05 m` (`2 in`) and `>= 0.1524 m` (`6 in`), reporting capture rate, **lift** over `2,000` seeded random land control points, and **Mann-Whitney `ROC-AUC`**.

**Frozen v0.2 Tier 0 Control Baseline** (full breakdown in [`docs/reports/sf_v02_validation.md`](reports/sf_v02_validation.md)):
- **Citywide SFPUC (`100-yr`, `5.0 mm/hr`, `n = 0.030`):** `HR = 0.3884`, `FAR = 0.7083`, `CSI = 0.1999` (Alemany `CSI = 0.4157`, SOMA / Mission Bay `CSI = 0.3591`, Bayview / Islais Creek `CSI = 0.2798`).
- **Dec 31, 2022 AR vs. 424 SF 311 Reports:** `0 m` lift `3.59x` (`>= 0.05 m`) / `3.92x` (`>= 0.1524 m`); `25 m` capture `67.2%` (`1.71x` lift, `ROC-AUC = 0.6715`); `50 m` capture `83.5%` (`1.26x` lift, `ROC-AUC = 0.6596`; SOMA `100.0%`, Mission `96.3%`, Central Waterfront `100.0%`).

---

## 8. Known Limitations: Mitigable vs. Structural

### Mitigable in Planned Milestones (`v0.4`–`v0.7`)
- **Flat-street nuisance ponding (Tier 0 uniform drainage):** Uniform `5.0 mm/hr` over-ponds flat residential streets (`48.7%` of cells with slope `< 2%` reach `>= 0.05 m` in the Dec 2022 AR), while raising uniform drainage starves downstream bowls (`CSI` drops `0.1999 -> 0.1483`). *v0.6 ablation outcome:* Mean-rate-matched **Tier 1a/1b** (`--mean-rate-matched`, extended from `Wing et al., 2017`) was tested in `v0.6` Phase 2B and still starved SF's dense combined-sewer bowls (`CSI 0.1953 -> 0.1689`), so uniform `Tier 0` (`5.0 mm/hr` effective parameter; roughly flat skill across `2.5–5.0 mm/hr` in the `v0.6.1` sweep) remains the frozen SF default while `--mean-rate-matched` is deferred to `v0.7` separate-MS4 testing.
- **`20 m` sub-cell street smoothing:** Narrow alleys and curb-gutter channels (`< 20 m`) are averaged within computational cells, limiting exact `0 m` point accuracy (`17.2%`). *Mitigation (`v0.4`):* **SFINCS quadtree refinement** (`10 m` cells in low-lying urban/coastal corridors, `40–80 m` on steep uplands; `Leijnse et al., 2021`).
- **Single-storm & 311 reporting bias:** Single-event validation and citizen 311 reporting biases (demographics, clogged catch basins, lateral backups; `Agonafir et al., 2022`). *Mitigation (`v0.4` / `v0.5`):* **4-AR MRMS hindcast ensemble** (`Eilander et al., 2023`) paired with 5-tier open validation (NOAA/USGS gauges, Caltrans/CHP closures, Groundsource, OpenFEMA NFIP; [`docs/VALIDATION_SPEC.md`](VALIDATION_SPEC.md)).
- **Pumped polders:** Tier 1b `(DEM - tide)` lock-up is physically invalid inside pump-drained polders (e.g., East Palo Alto/Foster City, Alameda/Oakland low-lying basins). *Mitigation (`v0.7`):* Explicit `polder_mask_source` exclusions and SFINCS `pump` structures.
- **Pacific Ocean wave runup:** Still-water tide gauges (`sfincs.bnd`/`sfincs.bzs`) omit Ocean Beach wave setup/runup (`Nederhoff et al., 2021`). *Mitigation (future):* Optional coupling with SFINCS `SnapWave` along open-coast Pacific boundaries.

### Structural 2D-Model Boundaries (No Practical Bay-Wide Fix)
- **No 1D parcel-level storm-pipe hydraulics:** Modeling individual inlets, pipe slopes, and manhole surcharging requires complete municipal 1D pipe-invert GIS across 101 cities and 9 counties—data that is fragmented, inconsistent, or unavailable publicly across the Bay Area. Subsurface trunk conveyance and manhole surcharging (e.g., uphill catch-basin backups in Bernal Heights or pressurized trunk surcharges in Mission/Visitacion Valley) remain an irreducible gap in any Bay-wide 2D-only screening model.
- **Linear-inertial steep-slope approximation:** SFINCS's reduced-physics momentum equations omit full convective acceleration terms needed for supercritical flow on steep hillsides (`> 10%` slope, e.g., Twin Peaks/Bernal Heights); steep-hillside sheetflow remains approximate.

---

## 9. Peer-Reviewed References

- **Agonafir, C., Ramirez Pabon, A., Lakhankar, T., Khanbilvardi, R., & Devineni, N. (2022).** Understanding New York City street flooding through 311 complaints. *Journal of Hydrology*, 605, 127300. [https://doi.org/10.1016/j.jhydrol.2021.127300](https://doi.org/10.1016/j.jhydrol.2021.127300)
- **Bates, P. D., Quinn, N., Sampson, C., Smith, A., Wing, O., et al. (2021).** Combined modeling of US fluvial, pluvial, and coastal flood hazard under current and future climates. *Water Resources Research*, 57(2), e2020WR028673. [https://doi.org/10.1029/2020WR028673](https://doi.org/10.1029/2020WR028673)
- **Eilander, D., Couasnon, A., Leijnse, T., Ikeuchi, H., Yamazaki, D., et al. (2023).** A globally applicable framework for compound flood hazard modeling. *Natural Hazards and Earth System Sciences*, 23, 823–846. [https://doi.org/10.5194/nhess-23-823-2023](https://doi.org/10.5194/nhess-23-823-2023)
- **Leijnse, T. W. B., van Ormondt, M., Nederhoff, K., & van Dongeren, A. R. (2021).** Modeling compound flooding in coastal systems using a computationally efficient reduced-physics solver: Including fluvial, pluvial, tidal, wind- and wave-driven processes. *Coastal Engineering*, 163, 103796. [https://doi.org/10.1016/j.coastaleng.2020.103796](https://doi.org/10.1016/j.coastaleng.2020.103796)
- **Lerma, N. R., Goodall, J. L., & Quinn, J. D. (2025).** Uncovering the drivers of urban flood reports: An environmental and socioeconomic analysis using 311 data. *Water*, 17(21), 3178. [https://doi.org/10.3390/w17213178](https://doi.org/10.3390/w17213178)
- **Nederhoff, K., Saleh, R., Tehranirad, B., Herdman, L., Erikson, L., Barnard, P. L., et al. (2021).** Drivers of extreme water levels in a large, urban, high-energy coastal estuary – A case study of the San Francisco Bay. *Coastal Engineering*, 170, 103984. [https://doi.org/10.1016/j.coastaleng.2021.103984](https://doi.org/10.1016/j.coastaleng.2021.103984)
- **San Francisco Public Works (2017).** *Folsom Area Stormwater Improvement Project: Needs Assessment and Alternatives Analysis Report.* Prepared for the San Francisco Public Utilities Commission, Sewer System Improvement Program (Project ID CWWSIPFCDB14), Section 3.1 & Table 3. [https://sfpuc.gov/sites/default/files/documents/Alternative-Analysis-Report-FASIP.pdf](https://sfpuc.gov/sites/default/files/documents/Alternative-Analysis-Report-FASIP.pdf)
- **Sanders, B. F., & Schubert, J. E. (2019).** PRIMo: Parallel raster inundation model. *Advances in Water Resources*, 126, 79–95. [https://doi.org/10.1016/j.advwatres.2019.02.007](https://doi.org/10.1016/j.advwatres.2019.02.007)
- **Schubert, J. E., & Sanders, B. F. (2012).** Building treatments for urban flood inundation models and implications for predictive skill and modeling efficiency. *Advances in Water Resources*, 41, 49–64. [https://doi.org/10.1016/j.advwatres.2012.02.012](https://doi.org/10.1016/j.advwatres.2012.02.012)
- **van Ormondt, M., Leijnse, T., de Goede, R., Nederhoff, K., & van Dongeren, A. (2025).** Subgrid corrections for the linear inertial equations of a compound flood model – a case study using SFINCS 2.1.1 Dollerup release. *Geoscientific Model Development*, 18, 843–861. [https://doi.org/10.5194/gmd-18-843-2025](https://doi.org/10.5194/gmd-18-843-2025)
- **Wing, O. E. J., Bates, P. D., Sampson, C. C., Smith, A. M., Johnson, K. A., & Erickson, T. A. (2017).** Validation of a 30 m resolution flood hazard model of the conterminous United States. *Water Resources Research*, 53(9), 7968–7986. [https://doi.org/10.1002/2017WR020917](https://doi.org/10.1002/2017WR020917)
