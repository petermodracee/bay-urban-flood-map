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
