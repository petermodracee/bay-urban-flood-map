# METHODS.md — Scientific Methodology & Reference Formulation

This document is the living scientific methodology reference for `bay-urban-flood-map`. It documents the physical formulations, terrain preprocessing, boundary conditions, storm-drainage abstractions, validation protocol, known limitations, and peer-reviewed literature underpinning the current **v0.2** citywide baseline and the planned **v0.4** upgrades.

For operational pipeline contracts and CLI stages, see [`docs/MODEL_SPEC.md`](MODEL_SPEC.md). For upstream data licenses and provenance, see [`docs/DATA_SOURCES.md`](DATA_SOURCES.md).

---

## 1. Modeling Scope & Screening-Level Framing

`bay-urban-flood-map` is a **screening-level 2D overland pooling and compound sea level rise (SLR) flood hazard model** for the San Francisco Bay Area. It fills the gap between coastal bathtub SLR viewers (which omit rainfall runoff) and FEMA riverine/coastal floodplains (which omit localized urban pluvial ponding).

- **What it represents:** Rain-on-grid overland flow, subgrid topographic storage in urban street corridors and depressions, shoreline overtopping/backflow from dynamic tides and storm surge, and soil infiltration.
- **What it does not represent:** Full 1D pressurized storm-sewer pipe hydraulics, manhole surcharge, individual catch-basin clogs, or Pacific Ocean wave runup. Outputs must not be used as parcel-exact engineering design depths or regulatory FEMA determinations.

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
- **Hydrometeorology & Validation:** NOAA Atlas 14 Vol. 6 IDF depths, Open-Meteo ERA5/archive hourly rainfall, NOAA CO-OPS tide gauge `9414290` (San Francisco Presidio), SFPUC 100-Year Storm Flood Risk Map, and DataSF 311 flooding service requests.

---

## 3. Terrain Hydro-Enforcement & Urban Feature Representation

Raw LiDAR DSM/DEM surfaces contain elevated bridge decks and smoothed coastal berms that create artificial dams in 2D overland flow. `pipeline/01_prep_terrain.py` applies four deterministic steps at `2 m` subgrid resolution (`1 m` in v0.1):

1. **Building Block-Out (`Schubert & Sanders, 2012`):**
   Building footprints are raised by `+3.0 m` (`building_handling: "elevation_bump"`) and assigned elevated roughness (`n = 0.30`), preventing artificial storage inside buildings and routing runoff along street corridors.
2. **Bounded Underpass Hydro-Enforcement:**
   Road/rail crossings tagged with `bridge`, `tunnel`, or `layer` are carved linearly between approach medians (`min_bump_m = 0.15 m`, `carve_half_width_m = 5.0 m`). Corridors exceeding `max_bump_m = 6.0 m` are skipped so bored hillside tunnels (e.g., Broadway and Stockton tunnels) are not carved into open canyons.
3. **Upward-Only Shoreline Crest Enforcement:**
   SFEI engineered shoreline defense classes (levees, seawalls, floodwalls, berms, engineered road embankments) are burned upward-only (`dem = max(dem, Z_Mean)`) across a `±2.0 m` half-width so coastal defenses are preserved in `20 m` subgrid tables.
4. **Roughness & SCS Curve Number Grids:**
   Manning's $n$ is mapped by NLCD class and impervious fraction (`n_paved = 0.030`, `n_water = 0.020`, `n_open_space = 0.035`, `n_vegetation = 0.060`), and SCS Curve Numbers ($CN \in [30, 100]$) are assigned from NLCD × USDA SSURGO HSG (defaulting urban fill/unclassified cells to HSG `D`).

---

## 4. Hydrodynamic Solver & Subgrid Formulation

Simulations use **Deltares SFINCS** (`deltares/sfincs-cpu`) built via `HydroMT-SFINCS` (`Leijnse et al., 2021`; `Eilander et al., 2023`):

- **Governing Physics:** Reduced-physics 2D local-inertial shallow-water equations with advection (`advection = 1`), viscosity (`viscosity = 1`), wetting/drying threshold `huthresh = 0.01 m`, and CFL factor `alpha = 0.75`.
- **Subgrid Lookup Tables (`van Ormondt et al., 2025`):** A `20 m` computational grid (`650 × 620`, `329,070` active cells for citywide SF) stores pre-computed subgrid volume–depth and conveyance–roughness relationships (`sfincs.sbg`, `nr_subgrid_pixels = 10`, `nbins = 10`, `max_gradient = 5.0`) derived directly from the `2 m` hydro-enforced DEM and Manning's $n$ grid.
- **Infiltration:** Spatially distributed SCS Curve Number soil abstraction (`scsfile`) handles pervious rainfall losses.
- **Downscaling:** Peak coarse water-surface elevations (`zsmax`) are bilinearly interpolated onto the `2 m` hydro-enforced DEM (`depth_m = max(0, zsmax_2m - dem_2m)`).

---

## 5. Storm-Drain Abstraction — Three-Tier Progressive Architecture

Because complete underground storm-pipe invert elevations do not exist uniformly across the 9-county SF Bay Area, underground drainage is parameterized in three progressive tiers (recorded in [`docs/DECISIONS.md`](DECISIONS.md)):

| Tier | Method & Status | Data Required | SFINCS Mechanism |
| --- | --- | --- | --- |
| **Tier 0** *(v0.1 / v0.2 frozen baseline)* | Uniform urban drainage subtraction (`5.0 mm/hr` calibrated rate) | None | Scalar subtraction on `sfincs.prp` (`max(0, rain - 5.0)`) + SCS CN |
| **Tier 1a** *(v0.4 planned, literature-backed)* | Urban-density scaled 2D drainage abstraction (`Bates et al., 2021`) | NLCD imperviousness + land cover | 2D gridded net rainfall (`netamprfile`) + SCS CN |
| **Tier 1b** *(v0.4 planned, experimental extension)* | Dynamic tide-coupled gravity-outfall attenuation | DEM + NOAA CO-OPS tide + pumped-polder flags | Attenuates Tier 1a capacity in `netamprfile` as `(DEM - tide(t)) → 0` |
| **Tier 2** *(v0.4+ optional enrichment)* | Local refinement where open municipal storm-drain / pump GIS exists | Optional `domain.storm_drain_source` (`null` by default) | Locally refines Tier 1 grid + SFINCS `pump` / `culvert` structures |

---

## 6. Coastal Boundary Conditions & Compound Scenario Design

- **Domain Masking (`msk`):** Offshore `NoData` cells beyond the coastal DEM extent are masked inactive (`msk = 0`); Bay and Pacific shoreline perimeter cells (`z <= 0.5 m NAVD88`) receive dynamic water-level forcing (`msk = 2` via `sfincs.bnd` / `sfincs.bzs`); upland southern San Mateo County border cells (`z > 0.5 m`) act as open outflow (`msk = 3`).
- **2024 CA OPC Scenario Matrix:** Crosses `10-yr` (`90.4 mm` / 24 hr) and `100-yr` (`144.0 mm` / 24 hr) alternating-block NOAA Atlas 14 storms with fixed `0`, `+1`, `+3`, `+5 ft` SLR increments added onto a dynamic 24-hr semidiurnal tidal hydrograph (`MHHW 1.85 m + 0.35 m surge`), plus the **Dec 31, 2022 – Jan 1, 2023 Atmospheric River** hindcast (`128.5 mm` / 48 hr Open-Meteo rain + observed NOAA `9414290` tide).
- **Derived SLR Delta:** Added compound flood depth from sea level rise is isolated as:
  $$\Delta d_{\text{SLR}} = \max\bigl(0,\; d(\text{Storm} + \text{SLR}) - d(\text{Storm} + 0\,\text{ft})\bigr)$$
- **Planned Multi-Gauge Bay Interpolation (`v0.4`):** San Francisco Bay exhibits strong tidal amplification toward the South Bay (`Nederhoff et al., 2021`). In v0.4, multiple NOAA CO-OPS stations will be supplied in `sfincs.bnd` / `sfincs.bzs` for spatial interpolation along `msk = 2`.

---

## 7. Dual Validation Protocol & Frozen v0.2 Baseline

Every model upgrade is evaluated against two complementary datasets in `pipeline/03_validate_sf.py`:

1. **Design-Storm Benchmark (SFPUC 100-Year Storm Flood Risk Map):**
   Downscaled `2 m` depths are thresholded at `>= 0.1524 m` (`6 in`) over valid land (`dem >= 0.5 m NAVD88`, excluding `NLCD 11` open water) and scored using **Hit Rate (HR)**, **False Alarm Ratio (FAR)**, and **Critical Success Index (CSI)** citywide and across 7 urban corridors.
2. **Real-Storm Crowdsourced Validation (`Agonafir et al., 2022`; `Lerma et al., 2025`):**
   Historical hindcasts are scored against geo-located municipal 311 flood reports within `0 m`, `25 m`, and `50 m` circular buffers at `>= 0.05 m` (`2 in`) and `>= 0.1524 m` (`6 in`), reporting capture rate, **lift** over `2,000` seeded random land control points, and **Mann-Whitney `ROC-AUC`**.

**Frozen v0.2 Tier 0 Control Baseline** (full breakdown in [`docs/reports/sf_v02_validation.md`](reports/sf_v02_validation.md)):
- **Citywide SFPUC (`100-yr`, `5.0 mm/hr`, `n = 0.030`):** `HR = 0.3884`, `FAR = 0.7083`, `CSI = 0.1999` (Alemany `CSI = 0.4157`, SOMA / Mission Bay `CSI = 0.3591`, Bayview / Islais Creek `CSI = 0.2798`).
- **Dec 31, 2022 AR vs. 424 SF 311 Reports:** `0 m` lift `3.59x` (`>= 0.05 m`) / `3.92x` (`>= 0.1524 m`); `25 m` capture `67.2%` (`1.71x` lift, `ROC-AUC = 0.6715`); `50 m` capture `83.5%` (`1.26x` lift, `ROC-AUC = 0.6596`; SOMA `100.0%`, Mission `96.3%`, Central Waterfront `100.0%`).

---

## 8. Known Limitations & Caveats

- **Flat-street nuisance ponding vs. trunk surcharge (Tier 0):** Uniform `5.0 mm/hr` drainage over-ponds flat residential streets (`48.7%` of cells with slope `< 2%` reach `>= 0.05 m` in the Dec 2022 AR) while omitting uphill catch-basin/manhole surcharge; raising uniform drainage to `8–10 mm/hr` starves downstream bowls and reduces citywide `CSI` (`0.1999 → 0.1483`).
- **Linear-inertial steep-slope regime:** SFINCS is designed for mild/flat floodplains and baylands; steep hillside gutter sheetflow (e.g., Bernal Heights, Twin Peaks) is approximated coarsely.
- **`20 m` grid smoothing:** Narrow alleys and curb-gutter channels (`< 20 m`) are averaged within computational cells, motivating **SFINCS quadtree (`10 m` urban bowls)** in v0.4.
- **Tier 1b `(DEM - tide)` heuristic & pumped polders:** Tide-locked outfall attenuation is an experimental extension (not from `Bates et al., 2021`) and invalid inside pumped polders unless pumps/masks are explicitly modeled.
- **No wave runup:** Tidal boundary forcing captures still-water tide + storm surge inside SF Bay, not Pacific Ocean wave setup/runup along Ocean Beach.
- **311 reporting bias:** Citizen 311 complaints reflect population density, reporting behavior, and private lateral backups alongside true street flooding.

---

## 9. Peer-Reviewed References

- **Agonafir, C., Ramirez Pabon, A., Lakhankar, T., Khanbilvardi, R., & Devineni, N. (2022).** Understanding New York City street flooding through 311 complaints. *Journal of Hydrology*, 605, 127300. [https://doi.org/10.1016/j.jhydrol.2021.127300](https://doi.org/10.1016/j.jhydrol.2021.127300)
- **Bates, P. D., Quinn, N., Sampson, C., Smith, A., Wing, O., et al. (2021).** Combined modeling of US fluvial, pluvial, and coastal flood hazard under current and future climates. *Water Resources Research*, 57(2), e2020WR028673. [https://doi.org/10.1029/2020WR028673](https://doi.org/10.1029/2020WR028673)
- **Eilander, D., Couasnon, A., Leijnse, T., Ikeuchi, H., Yamazaki, D., et al. (2023).** A globally applicable framework for compound flood hazard modeling. *Natural Hazards and Earth System Sciences*, 23, 823–846. [https://doi.org/10.5194/nhess-23-823-2023](https://doi.org/10.5194/nhess-23-823-2023)
- **Leijnse, T. W. B., van Ormondt, M., Nederhoff, K., & van Dongeren, A. R. (2021).** Modeling compound flooding in coastal systems using a computationally efficient reduced-physics solver: Including fluvial, pluvial, tidal, wind- and wave-driven processes. *Coastal Engineering*, 163, 103796. [https://doi.org/10.1016/j.coastaleng.2020.103796](https://doi.org/10.1016/j.coastaleng.2020.103796)
- **Lerma, N. R., Goodall, J. L., & Quinn, J. D. (2025).** Uncovering the drivers of urban flood reports: An environmental and socioeconomic analysis using 311 data. *Water*, 17(21), 3178. [https://doi.org/10.3390/w17213178](https://doi.org/10.3390/w17213178)
- **Nederhoff, K., Saleh, R., Tehranirad, B., Herdman, L., Erikson, L., Barnard, P. L., et al. (2021).** Drivers of extreme water levels in a large, urban, high-energy coastal estuary – A case study of the San Francisco Bay. *Coastal Engineering*, 170, 103984. [https://doi.org/10.1016/j.coastaleng.2021.103984](https://doi.org/10.1016/j.coastaleng.2021.103984)
- **Sanders, B. F., & Schubert, J. E. (2019).** PRIMo: Parallel raster inundation model. *Advances in Water Resources*, 126, 79–95. [https://doi.org/10.1016/j.advwatres.2019.02.007](https://doi.org/10.1016/j.advwatres.2019.02.007)
- **Schubert, J. E., & Sanders, B. F. (2012).** Building treatments for urban flood inundation models and implications for predictive skill and modeling efficiency. *Advances in Water Resources*, 41, 49–64. [https://doi.org/10.1016/j.advwatres.2012.02.012](https://doi.org/10.1016/j.advwatres.2012.02.012)
- **van Ormondt, M., Leijnse, T., de Goede, R., Nederhoff, K., & van Dongeren, A. (2025).** Subgrid corrections for the linear inertial equations of a compound flood model – a case study using SFINCS 2.1.1 Dollerup release. *Geoscientific Model Development*, 18, 843–861. [https://doi.org/10.5194/gmd-18-843-2025](https://doi.org/10.5194/gmd-18-843-2025)
