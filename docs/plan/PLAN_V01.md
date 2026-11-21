# PLAN_V01.md — v0.1 San Francisco Benchmark Vertical Slice (Complete)

Goal: Run the full pipeline end-to-end on a ~15 km² San Francisco test slice (e.g., Mission / Alemany corridor), validate modeled pooling against the SFPUC 100-Year Storm Flood Risk Map, and render the resulting COG in a local MapLibre viewer.

---

## Phase 0: Environment & Raw Data
- [x] Create `conda-forge` environment from `environment.yml` and verify `hydromt_sfincs` imports cleanly.
- [x] Pull `deltares/sfincs-cpu` Docker image and confirm container execution against a local volume mount.
- [x] Define the exact ~15 km² SF test bounding box (`EPSG:26910`) and NOAA Atlas 14 design-storm inputs in `configs/sf_benchmark.yml`.
- [x] Download raw inputs into `data/raw/` (git-ignored):
  - [x] USGS 3DEP 1m LiDAR DEM (`NAVD88`) covering the test bbox.
  - [x] USGS Annual NLCD land cover / imperviousness and USDA HSG soil layer.
  - [x] OSM roads, rail, and building footprints for the test bbox.
  - [x] DataSF 100-Year Storm Flood Risk Zone polygons (`data.sf.gov`).

## Phase 1: Terrain & Infiltration Prep (`pipeline/01_prep_terrain.py`)
- [x] Clip and reproject all raw inputs to `EPSG:26910` (meters).
- [x] Burn culverts/underpasses into the 1m DEM at freeway/rail crossings (verify US-101 / I-280 / Alemany corridors do not act as artificial dams).
- [x] Apply building footprint handling (elevation bump or high Manning's n) so runoff routes along street corridors.
- [x] Generate Curve Number infiltration and surface roughness grids in `data/processed/`.

## Phase 2: Baseline SFINCS Run (`pipeline/02_build_sfincs.py`)
- [x] Build SFINCS model with `hydromt_sfincs` (10m–20m computational grid + 1m–2m subgrid terrain tables).
- [x] Configure historical 100-year rainfall forcing and an initial uniform urban drainage subtraction rate (e.g., `15 mm/hr`).
- [x] Execute solver via `deltares/sfincs-cpu` Docker container and confirm `sfincs_map.nc` outputs in `data/runs/`.

## Phase 3: SFPUC Benchmark Validation (`pipeline/03_validate_sf.py`)
- [x] Downscale SFINCS max water level (`zsmax`) to the 1m–2m hydro-enforced DEM to produce max flood depth (`m`).
- [x] Threshold modeled depth at `>= 0.1524 m` (6 inches) and score against DataSF polygons (Hit Rate, False Alarm Ratio, CSI).
- [x] Run a small parameter sweep across drainage subtraction rate and paved Manning's n; write best scores to `validation_summary.json`.
- [x] Visually inspect top false positives to catch any unburned underpasses or terrain artifacts.

## Phase 4: Web-Ready COG Export (`pipeline/04_export_cog.py`)
- [x] Mask dry/trace cells (`depth < 0.05 m`) to NoData (`-9999.0`).
- [x] Reproject to `EPSG:3857` and export deflated Cloud Optimized GeoTIFF with internal overviews (`sf_100yr_slr0ft_depth_3857.tif`).
- [x] Verify COG compliance with `gdalinfo` (`EPSG:3857`, `LAYOUT=COG`, overviews present).
- [x] Export simplified `sf_sfpuc_100yr_benchmark.geojson` and `manifest.json` to `data/web_outputs/`.

## Phase 5: Thin Web Viewer (`web/`)
- [x] Wire Vite + `maplibre-gl` + `@geomatico/maplibre-cog-protocol` to load `manifest.json` and render the local COG.
- [x] Insert flood-depth layer below basemap road labels (`beforeId`).
- [x] Implement core v0.1 controls: opacity slider, SFPUC benchmark polygon toggle, and click-to-query pixel depth popup (in/ft + m).
- [x] Add experimental-model disclaimer banner and verify clean production build (`npm run build`).

---

## Exit Criteria (Ready for v0.2 MS4 Compound Pilot)
- End-to-end pipeline runs reproducibly from `configs/sf_benchmark.yml`.
- Modeled 6-inch flood bowls align with SFPUC's major Mission/Alemany ponding corridors without artificial highway-dam lakes.
- Local MapLibre viewer streams the `EPSG:3857` COG smoothly with accurate click-to-query depths.
