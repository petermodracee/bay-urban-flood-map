# PLAN_V03.md — Public Static Hosting & Serverless COG Deployment

**Goal:** Host `data/web_outputs/` (`EPSG:3857` COGs, GeoJSON overlays, `manifest.json`) on object storage with HTTP `Range` + CORS support and deploy `web/` as a zero-backend static site.

## Phase 1: Web Viewer Base-URL Configuration (`web/`)
- [x] Add `VITE_DATA_BASE_URL` support in `web/` (defaulting to `/data` for local Vite dev/preview so local workflows keep working with zero setup).
- [x] Resolve `manifest.json`, GeoJSON overlay URLs, and COG URLs cleanly against `VITE_DATA_BASE_URL` (supporting both relative `/data/...` paths and absolute `https://...` bucket URLs).
- [x] Add/update Node unit tests (`node --test src/*.test.js`) covering local `/data` vs. remote `VITE_DATA_BASE_URL` resolution.

## Phase 2: Object Storage & HTTP Range Configuration (Cloudflare R2 / S3)
- [x] Configure bucket CORS policy allowing `GET` and `HEAD` from the deployed frontend origin(s), exposing `Accept-Ranges`, `Content-Range`, `Content-Length`, and `ETag` headers for `maplibre-cog-protocol`.
- [x] Add a lightweight upload/sync script (or documented `rclone` / `aws s3 sync` command) to push `data/web_outputs/*.tif`, `*.geojson`, and `manifest.json` with proper `Content-Type` and `Cache-Control` headers.
- [x] Verify HTTP `206 Partial Content` Range requests directly against a hosted COG (`curl -I` and `curl -r 0-16383`).

## Phase 3: Static Frontend Deployment & Smoke Verification
- [x] Deploy `web/` (`npm run build`) to Cloudflare Pages or GitHub Pages with `VITE_DATA_BASE_URL` pointed at the hosted COG bucket.
- [x] Smoke-test the live URL in-browser: confirm `manifest.json` loads all 15 SF scenarios, depth/delta COGs stream tiles via `206` Range requests, click-to-query depth popup works, and SFPUC + 311 overlays toggle cleanly.

## Exit Criteria
- Public static URL loads all v0.2 San Francisco COGs without any tile server (`GeoServer`/`TiTiler`/`PostGIS`), passing `node --test`, `npm run lint`, and `npm run build`.
