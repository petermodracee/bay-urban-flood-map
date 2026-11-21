# MODEL_SPEC.md — Hydrodynamic Modeling & Validation

## 1. Milestones
- **v0.1 (SF Benchmark):** 2D rain-on-grid SFINCS model on a ~15 km² San Francisco slice (e.g., Mission/Alemany corridor), validated against the SFPUC 100-Year Storm Flood Risk Map.
- **v0.2 (MS4 Compound Pilot):** Calibrated pipeline on a flatland, bay-margin MS4 watershed (East or South Bay) forced with a historical atmospheric river (MRMS radar rain + observed NOAA tides), validated against 311 flood reports, CHP/Caltrans closures, and OneRain gauges.
- **v1.0 (Regional):** Batch-run across ~100–150 Bay Area sub-basins and mosaic into regional COGs.

## 2. Core Data Sources
- **Terrain:** USGS 3DEP 1m LiDAR DEM (`NAVD88`, UTM Zone 10N).
- **Shoreline Structures:** SFEI SF Bay Shore Inventory (levees, berms, flood-routing structures).
- **Hydro-Enforcement Vectors:** OpenStreetMap roads/rail/buildings + USGS NHD and county creek channels.
- **Land Cover & Soils:** USGS Annual NLCD (imperviousness/land cover) + USDA Hydrologic Soil Groups (HSG) for Curve Number infiltration.
- **SF Benchmark Target:** SFPUC 100-Year Storm Flood Risk Zone polygon layer via `data.sf.gov` (GeoJSON/Shapefile) or SFPUC ArcGIS REST FeatureServer.

## 3. Scenario Matrix (2024 CA OPC Guidance)
Simulate fixed water-level increments and map them to 2024 CA OPC scenarios (SF tide gauge, 2000 baseline) in metadata/UI:

| Scenario Key | Water Level Increment | 2024 CA OPC Mapping |
| :--- | :--- | :--- |
| `slr0ft` | `0.00 m` (`0.0 ft`) | Baseline Today |
| `slr1ft` | `0.30 m` (`+1.0 ft`) | 2050 Intermediate (0.8 ft) / Intermediate-High (1.0 ft) |
| `slr3ft` | `0.91 m` (`+3.0 ft`) | 2100 Intermediate (3.1 ft) |
| `slr5ft` | `1.52 m` (`+5.0 ft`) | 2100 Intermediate-High (4.8 ft) |

- **Rainfall Events:** Cross each water level with two historical storms: `10yr` (chronic drainage failure) and `100yr` (extreme downpour / SFPUC benchmark).
- **Tidal Tailwater:** Use a dynamic 24–48 hr tidal hydrograph peaking at MHHW + typical winter storm surge, synchronized with peak rainfall. Do not use a static flat bathtub water level.
- **Derived Delta:** Compute `(SLR + Storm) - (Baseline + Storm)` to isolate inland "drainage lock" flooding caused by elevated Bay tailwater.

## 4. Pipeline Stages (`pipeline/`)
All scripts must be idempotent and driven by YAML configs in `configs/`.

1. **`01_prep_terrain.py`**
   - Burn culverts/bridges into the 1m DEM at OSM road/rail × creek intersections to prevent artificial dams.
   - Handle building footprints (elevation bump or high Manning's n) so runoff routes along streets.
   - Generate Curve Number infiltration and roughness grids.
2. **`02_build_sfincs.py`**
   - Build model with `hydromt_sfincs` using a 10m–20m computational grid and 1m–2m subgrid terrain tables (`setup_subgrid`).
   - Approximate underground storm pipes via a uniform urban drainage subtraction rate (mm/hr); use SFINCS internal structures (`culvert_simple`, `check_valve`, `pump`) only for major trunk bottlenecks.
   - Execute via `deltares/sfincs-cpu` Docker image.
3. **`03_validate_sf.py`**
   - Threshold downscaled max flood depth at `>= 0.1524 m` (6 inches) and score against DataSF extent polygons using **Hit Rate (HR)**, **False Alarm Ratio (FAR)**, and **Critical Success Index (CSI)**.
   - If comparing water-surface elevations against SFPUC elevation records, apply the `SFCD -> NAVD88` vertical datum offset *only* at comparison time; never modify the base DEM datum.
4. **`04_export_cog.py`**
   - Downscale SFINCS max water level (`zsmax`) onto the 1m–2m hydro-enforced DEM, mask dry cells, reproject to `EPSG:3857`, and write COGs per `docs/DATA_CONTRACTS.md`.

## 5. Known Domain Traps & Open Placeholders
- **SFPUC Polygons Are Parcel-Snapped:** DataSF's 100-year flood polygons align to property boundaries and reflect manual owner appeals. Evaluate validation against general bowl geometry and street corridors—do not overfit parameters chasing a 1.0 IoU on parcel edges.
- **Pumped Polders (v0.2+):** Low-lying bay-margin areas protected by pump stations (e.g., Foster City, parts of Alameda) sit below high tide and will appear as deep inundation unless explicitly masked, flagged, or modeled with SFINCS `pump` structures.
- **Open Placeholders in `configs/sf_benchmark.yml` (resolve in Phase 0):**
  - Exact `EPSG:26910` bounding box for the ~15 km² SF Mission/Alemany slice.
  - Exact NOAA Atlas 14 precipitation depths and temporal storm distribution (hyetograph) for the `10yr` and `100yr` design storms.
