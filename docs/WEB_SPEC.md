# WEB_SPEC.md — MapLibre Frontend Specification

## 1. Tech Stack (`web/`)
- **Build Tool:** Vite (vanilla JS or TypeScript; keep dependencies minimal).
- **Mapping:** `maplibre-gl` + `@geomatico/maplibre-cog-protocol` for serverless COG streaming, client-side color functions (`setColorFunction`), and pixel value queries (`locationValues`).
- **Vector Overlays:** `pmtiles` protocol for static vector layers.
- **Hosting Target:** Static files only (Cloudflare Pages / GitHub Pages + R2/S3 for COGs). In local dev/preview, `web/vite.config.js` serves `data/web_outputs/` at `/data/` with HTTP `Range` request support.

## 2. Visual Hierarchy & Layer Ordering
Street legibility is critical for urban flood maps. Use a free, no-API-key vector basemap (e.g., OpenFreeMap Positron) and always insert flood-depth COG layers **below** the basemap's road-label and building-outline layers (using MapLibre's `beforeId` parameter).

## 3. Color Ramps & Units
All COG pixels are `Float32` meters (`< 0.05 m` is NoData/transparent). Convert to **inches** (if `< 12 in`) or **feet** (if `>= 12 in`) in tooltips and legends.

- **Flood Depth (`depth`):** Sequential blue-to-indigo ramp:
  - `0.05 m – 0.15 m` (2–6 in): Light cyan (nuisance ponding)
  - `0.15 m – 0.30 m` (6–12 in): Medium blue (curb overtopping / SFPUC threshold)
  - `0.30 m – 0.91 m` (1–3 ft): Deep blue (impassable to passenger vehicles)
  - `> 0.91 m` (> 3 ft): Dark indigo/purple (severe inundation)
- **Drainage Lock Delta (`delta`):** Distinct warm ramp (amber → crimson) to visually separate *added SLR-driven flooding* from baseline pooling.

## 4. Core UI Controls (v0.1)
- **Scenario Switcher:** Driven by `manifest.json` (Storm intensity × SLR increment + Delta view).
- **Benchmark Overlay Toggle (v0.1):** Toggle SFPUC 100-Year Storm Flood Risk polygon outline on/off for visual comparison.
- **Opacity Slider:** Adjust flood raster transparency (`0% – 100%`, default `75%`).
- **Click-to-Query Popup:** Clicking any flooded pixel reads the raw COG value via `locationValues` and displays depth in both imperial and metric (e.g., `14 in (0.36 m)`).
- **Disclaimer Banner:** Clearly state that this is an experimental overland pooling & tidal-lock model (not a parcel-level pipe-capacity or FEMA regulatory map).
