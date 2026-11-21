# AGENTS.md — bay-urban-flood-map

## Project Overview
`bay-urban-flood-map` is an open-source urban stormwater and compound sea level rise flood model for the SF Bay Area. It fills the gap between FEMA floodplains and shoreline SLR viewers by modeling how heavy rainfall pools in impervious urban bowls—and how rising Bay tides lock up gravity-fed storm drains.

The repo has two decoupled halves connected only by static files in `data/web_outputs/`:
- **`pipeline/` (Python + Docker):** Uses `HydroMT-SFINCS` (2D rain-on-grid with 1m–2m subgrid terrain) to simulate storm + SLR scenarios and export `EPSG:3857` Cloud Optimized GeoTIFFs (COGs).
- **`web/` (Vite + MapLibre GL JS):** A serverless static web viewer that streams those COGs directly in the browser with client-side color ramps and click-to-query flood depths.

**Current Focus (v0.1):** Building and validating an end-to-end vertical slice on a ~15 km² San Francisco test area against the SFPUC 100-Year Storm Flood Risk Map before expanding to Bay-margin MS4 watersheds.

## Hard Invariants
- **Python Environment:** Use `conda-forge` strictly (`environment.yml`). Do not mix `pip` geospatial binaries with conda packages.
- **Compute Target:** Simulations run locally via the `deltares/sfincs-cpu` Docker image on a standard workstation (not a cloud GPU cluster). Keep grid resolutions and batch scripts sized accordingly.
- **Units & Datums:** All modeling and raster math happen in **metric (`meters`)**, **CRS `EPSG:26910`** (UTM 10N), and **vertical datum `NAVD88`**. Convert to imperial (`inches`/`feet`) only in UI display strings and scenario labels.
- **Data Hygiene:** Never commit raw or generated geospatial files (`*.tif`, `*.nc`, `*.pmtiles`, `*.shp`, `*.gpkg`, `data/*`).
- **Serverless Architecture:** Never introduce backend tile servers (no GeoServer, PostGIS, or TiTiler). All web map assets must be static `EPSG:3857` COGs or PMTiles.
- **Explicit Non-Goals (v0.1):** No 1D municipal storm pipe network modeling; no climate-adjusted future rainfall intensity.

## Context Routing (Read Before Editing)
- **Project rationale & tradeoffs:** Read `docs/DECISIONS.md`.
- **Current milestone checklist:** Read `docs/PLAN.md`.
- **Editing `pipeline/` or `configs/`:** Read `docs/MODEL_SPEC.md` and `docs/DATA_CONTRACTS.md`.
- **Editing `web/`:** Read `docs/WEB_SPEC.md` and `docs/DATA_CONTRACTS.md`.
- **Modifying file names, CRS, NoData rules, or JSON outputs:** Update `docs/DATA_CONTRACTS.md` first.

## Code Quality
- **Linters:** Run `conda run -n bay-urban-flood-map ruff check --fix && conda run -n bay-urban-flood-map ruff format` (Python) and `cd web && npm run lint` (JS) before committing.
- **Modularity:** Separate pure computation (datum math, masking, scoring) from file I/O and CLI entrypoints so core logic stays unit-testable.
- **Geospatial Safety:** Use `pathlib.Path` (no hardcoded absolute paths), read all parameters from `configs/`, and explicitly assert CRS (`EPSG:26910` / `EPSG:3857`) at pipeline boundaries.
- **Units in Code:** Use Python type hints and suffix physical variables with units (`depth_m`, `drainage_mm_hr`, `slr_ft`) to prevent metric/imperial mix-ups.

## Verification Commands
- **Python pipeline:** `pytest` and `gdalinfo <output_cog.tif>` (verify `EPSG:3857`, `LAYOUT=COG`, and internal overviews).
- **Web frontend:** `cd web && npm run build`.

## Git Workflow
- **Branching:** Do not commit directly to `main`. Create a short-lived branch per task (e.g., `feat/prep-terrain`, `fix/cog-nodata`, `docs/update-spec`).
- **Safe Staging:** Never run `git add .` or `git add -A`. Run `git status` first and stage specific files by path to guarantee no large rasters or temporary outputs are included.
- **Atomic Commits:** Keep commits small and scoped to a single concern or `docs/PLAN.md` checklist item (avoid bundling unrelated config, pipeline, web, and doc edits in one commit), using Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`).
- **Attribution:** Keep commit messages clean (no AI/agent footers); when a PR is AI-assisted, include a brief note and concrete verification results in the PR description.
- **Verify Before Committing:** Run the relevant verification command (`pytest` or `npm run build`) before creating a commit.
- **Remote Safety:** Never force-push (`--force`) or rewrite shared history. Ask for confirmation before pushing to remote or opening a pull request.
- All CI checks must pass before merge (if/when configured).

## Boundaries (Ask First)
Ask for confirmation before:
- Adding or upgrading dependencies in `environment.yml` or `web/package.json`.
- Downloading large external datasets (> 100 MB) into `data/raw/`.
- Changing shared interfaces in `docs/DATA_CONTRACTS.md`.

## Dev & Test Conventions
- **Python execution:** Run via `conda run -n bay-urban-flood-map python <script>`.
- **Fast tests only:** Keep `pytest` fast and offline using tiny synthetic fixtures; never trigger full SFINCS Docker runs or live downloads inside unit tests.
- **Progress tracking:** When completing a milestone, check off the corresponding item in `docs/PLAN.md` in the same commit.
