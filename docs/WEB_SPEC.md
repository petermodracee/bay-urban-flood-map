# WEB_SPEC.md — MapLibre Frontend Specification

## 1. Tech Stack (`web/`)
- **Build Tool:** Vite (vanilla JS or TypeScript; keep dependencies minimal).
- **Mapping:** `maplibre-gl` + `@geomatico/maplibre-cog-protocol` for serverless COG streaming, client-side color functions (`setColorFunction`), and pixel value queries (`locationValues`).
- **Vector Overlays:** `pmtiles` protocol for static vector layers.
- **Hosting Target:** Static files only (Cloudflare Pages / GitHub Pages + R2/S3 for COGs). Hosted R2 objects live under immutable version prefixes (`<version>/<filename>`, `Cache-Control: public, max-age=31536000, immutable`) fronted by an unversioned `latest.json` pointer (`max-age=300`). `web/src/dataUrls.js` resolves the manifest via `latest.json` (or a build-time `VITE_DATA_VERSION` pin to `<version>/manifest.json`, with `404` fallback to unversioned `manifest.json`) and loads versioned asset paths against `VITE_DATA_BASE_URL`. In local dev/preview, `web/vite.config.js` serves `data/web_outputs/` at `/data/` (transparently falling back from `/data/<version>/<file>` to flat `data/web_outputs/<file>`) with HTTP `Range` request support.

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

## 5. v0.6 Viewer Upgrades
- **Docked Sidebar & Navigation:** Collapsible `320px` left sidebar panel (`scrollbar-gutter: stable`) with live `manifest.json` metadata (`version`, `drainage_tier`, `rain_source`), grouped Coincident Stress Scenarios vs. Historical AR storm selector, shareable URL hash state (`#storm=&slr=&metric=&lat=&lng=&z=&3d=`), quick-jump hotspot chips, and a bounded SF Photon address/intersection search.
- **3D Terrain (`hybrid-dem://`):** A `3D` toggle beneath the top-right compass dial switches `maxPitch` (`0°` locked in 2D, `70°` in 3D), pitches to `55°`, and streams our `4 m` `EPSG:3857` LiDAR DEM COG (`dem_cog`, `raw >= 0 m`) composited pixel-for-pixel over AWS Terrarium outside SF (`hybrid-dem://`), paired with DEM hillshade, `10 m` topographic contours (`topo_contours`), and directional-lit `fill-extrusion` buildings. Middle-mouse drag rotates bearing and tilts pitch (when `3D` is enabled).
- **Highways & Elevated Bridges (2.5D, off by default):** Optional `transportation` vector overlay rendering at-grade highways and elevated bridges (`brunnel == "bridge"`) with a soft drop shadow and deck casing above the flood COG, plus an inline sub-legend.
- **Validation Overlays (plain-English UI labels, off by default):** SFPUC 100-Yr Flood Risk, per-storm 311 Reports, per-storm Traffic & Rescues, NOAA Tide Gauge (`NAVD88`), and FEMA NFIP Claims (44 positive-claim census tracts with claim-count choropleth legend). Shared singleton `Popup` uses `closeOnClick: false` and a `±6 px` click hit-box for single-click inspection.
- **About & Methodology Pages (`web/about.html` & `web/methodology.html`):** Two separate static Vite entrypoints sharing `web/src/methodology.css` and Public Sans typography, linked from the sidebar and disclaimer modal — a 6th-grade plain-English About explainer (`about.html`, with inline SVG workflow diagram and v0.6 validation highlights) and a graduate-level technical Methodology research paper (`methodology.html`, with governing equations, v0.2–v0.6 ablation/calibration tables with metric footnotes, AI/use disclosure, data sources, and unified references). All three HTML entrypoints include `schema.org` JSON-LD, Open Graph/Twitter metadata, canonical links, and static crawl/brand assets in `web/public/` (`favicon.svg`, `og-preview.jpg`, `robots.txt`, `sitemap.xml`). **Maintenance invariant:** Both `web/about.html` and `web/methodology.html` (version number, date byline, validation numbers, scenario coverage, and methodology/limitation notes) must be updated with every version change or milestone release.
