# DECISIONS.md — Architecture & Design Rationale

This file records *why* key architectural choices were made so future contributors and coding agents do not re-litigate or drift from them.

## 1. Standalone Repository First
- **Decision:** Build and publish `bay-urban-flood-map` as a standalone experimental project.
- **Why:** Shipping an experimental hydrodynamic model separately from official sources allows open iteration, public validation scores, and transparent caveats without risking credibility of official sources.
- **Graduation Path:** Validate standalone → produce as an external resource → optionally consume published COG/PMTiles endpoints as an opt-in Bay Area regional layer once calibrated.

## 2. 2D Rain-on-Grid (SFINCS) Over Fill-Sink or 1D Pipe Models
- **Decision:** Use **Deltares SFINCS** (2D reduced-physics compound flood solver with 1m–2m subgrid terrain) and approximate underground storm drains with a calibrated uniform drainage subtraction rate.
- **Why:**
  - *Why not a DEM fill-sink map?* Raw LiDAR depression maps generate massive false positives at unburned highway bridges/culverts and completely miss pumped polders.
  - *Why not a full 1D–2D pipe model?* Across the 9-county Bay Area, underground storm drain ownership is fragmented across 101 cities and 9 county flood control districts, with widespread gaps in pipe invert elevations and CAD/PDF-only records.
  - *Why SFINCS?* Runs coupled pluvial + tidal simulations in minutes on local workstation hardware while capturing curb-level overland pooling and tidal tailwater lock.

## 3. Fixed Water-Level Increments (`0 / +1 / +3 / +5 ft`)
- **Decision:** Simulate fixed water-level increments and map them to 2024 California OPC Sea Level Rise Guidance (Intermediate and Intermediate-High) in metadata and UI labels.
- **Why:** State SLR projections update roughly every five years. Hardcoding "2050" or "2100" into raster filenames makes the dataset go stale; fixed foot increments remain durable and align directly with NOAA, BCDC, and USGS CoSMoS viewers.

## 4. Two-Stage Validation Strategy
- **Decision:** Validate in two distinct phases: (1) San Francisco against SFPUC's 100-Year Storm Flood Risk Map, then (2) a flatland, bay-margin MS4 watershed against real-storm observations (311 flooded-street logs, CHP/Caltrans closures, county OneRain gauges).
- **Why:** San Francisco is an ideal sandbox for verifying terrain hydro-enforcement and baseline drainage subtraction, *but* SF operates a combined sewer system and SFPUC's map explicitly excludes Bay tides. Stage 2 is strictly required to validate separate MS4 drainage and tidal backwater mechanics.

## 5. v0.1 SF Benchmark Calibration Baseline (Phase 3)
- **Decision:** Update `configs/sf_benchmark.yml` baseline defaults from `drainage_rate_mm_hr: 15.0` / `manning_n_paved: 0.020` to `drainage_rate_mm_hr: 5.0` / `manning_n_paved: 0.030` (`validation_summary.json`).
- **Why:**
  - *Sweep performance:* On the `sf_100yr_slr0ft` benchmark (`144.0 mm` 24-hr NOAA Atlas 14 storm, `>= 0.1524 m` threshold over land `>= 0.5 m` NAVD88), `15.0 mm/hr` subtracted ~88% of total rainfall (`17.30 mm` net runoff), yielding `HR=0.0735`, `FAR=0.6196`, `CSI=0.0656`. Lowering uniform drainage subtraction to `5.0 mm/hr` (`47.40 mm` net runoff) with `manning_n_paved=0.030` increased Hit Rate 4x (`0.3047`), reduced FAR (`0.5065`), and increased CSI 3.5x (`0.2321`).
  - *Structural score ceiling (do not overfit):* SFPUC 100-year polygons snap to full parcels (including building footprints), whereas the 1 m hydro-enforced DEM bumps buildings `+3.0 m` so ponding stays in street corridors. Combined with unmodeled spatial variation between older combined-sewer bowls and modern separated-sewer fill (e.g., Mission Bay, the largest false-positive cluster at `104,800 m²`), pixel-level CSI is expected to stay modest without artificial highway-dam artifacts.

## 6. Three-Tier Progressive Drainage Architecture (v0.2 Baseline vs. v0.4+)
- **Decision:** Keep **Tier 0** (`drainage_rate_mm_hr: 5.0` uniform baseline) as the San Francisco `sf_citywide` default after the v0.4 Phase 2 ablation, while retaining **Tier 1a/1b** (`netamprfile`) and **Tier 2** (`storm_drain_source`) as opt-in capabilities for **v0.5** East/South Bay separate-MS4 watersheds.
- **Architecture:**
  - **Tier 0 — Uniform Baseline (v0.1 / v0.2 / v0.4 SF default):** Subtracts a calibrated uniform rate (`5.0 mm/hr`) from rainfall (`sfincs.prp`) while preserving SCS Curve Number soil infiltration. Confirmed optimal on San Francisco citywide (`CSI = 0.1999`, Alemany `0.4157`, SOMA `0.3591`; 311 `0 m` lift `3.59x`, `25 m` `AUC = 0.6715`).
  - **Tier 1a/1b — Universal Bay-Wide Physical Proxy (`--drainage-tier tier1a|tier1b`):** Uses only datasets already fetched across all 9 Bay Area counties (**DEM + NOAA tide + NLCD imperviousness/landcover**) to generate a 2D gridded net rainfall NetCDF (`netamprfile`, keeping SCS CN intact). In v0.4 Phase 2 ablation on SF, `2–10 mm/hr` NLCD scaling over-abstracted dense SF urban bowls (`CSI 0.1999 -> 0.1137`, `25 m AUC 0.6715 -> 0.6483`), so it remains opt-in for separate-MS4 Bay-margin basins in v0.5.
  - **Tier 2 — Optional Municipal Infrastructure Override (`domain.storm_drain_source`):** Opt-in municipal storm-drain/pump GIS enrichment (`null` by default); provenance is tracked via `drainage_tier` in `manifest.json`.

## 7. Peer-Reviewed Scholarly Literature Foundation & Controlled Ablation Gate
- **Decision:** Ground every v0.4+ modeling component in peer-reviewed coastal/urban compound flood literature (`Eilander et al., 2023`; `Leijnse et al., 2021`; `van Ormondt et al., 2025`; `Schubert & Sanders, 2012`; `Bates et al., 2021`; `Nederhoff et al., 2021`; `Agonafir et al., 2022`; `Lerma et al., 2025`), explicitly separate experimental heuristics (e.g., Tier 1b tide-locked outfall attenuation), and gate every change behind a controlled one-factor-at-a-time ablation against the frozen v0.2 baseline (`docs/reports/sf_v02_vs_v04_ablation.md`).
- **Why:** Prevents ad-hoc tuning or complexity creep (e.g., adopting `10 m` quadtree or Tier 1a/1b unconditionally when uniform `20 m` + Tier 0 scores better on SF citywide) and keeps experimental extensions clearly labeled until proven on target watersheds.

## 8. NOAA MRMS 1 km Gridded QPE for Historical Hindcasts
- **Decision:** Adopt hourly **NOAA MRMS `MultiSensor_QPE_01H_Pass2`** (`1 km` gauge-corrected radar QPE via `netamprfile`) as the default rainfall forcing for historical AR hindcasts (`rain_source: "mrms"`), while keeping Open-Meteo single-point rainfall as the apples-to-apples v0.2 control and NOAA Atlas 14 hyetographs for design storms.
- **Why:** Single-point Open-Meteo smears localized convective AR rain bands and orographic gradients uniformly across SF. In our 4-AR ensemble ablation (`1,535` DataSF 311 points), MRMS QPE improved ensemble-mean `25 m` capture from `27.03%` to `36.95%` (`+9.92 pp`), `25 m` lift from `0.819x` to `1.251x`, and `25 m ROC-AUC` from `0.5629` to `0.5888` (`50 m` capture `38.91% -> 57.30%`), rescuing moderate events like Jan 9, 2023 (`0.0% -> 34.7%` at `25 m`).
