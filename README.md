# bay-urban-flood-map

An experimental open-source urban stormwater and compound sea level rise (SLR) flood model and web viewer for the San Francisco Bay Area, built with **HydroMT-SFINCS** and **MapLibre GL + Cloud Optimized GeoTIFFs (COGs)**.

The project starts with a **San Francisco benchmark** validated against the **SFPUC 100-Year Storm Flood Risk Map** before scaling to Bay-margin MS4 watersheds.

## Benchmark Scenarios (`configs/sf_benchmark.yml`)

10-year and 100-year 24-hour design rain events crossed with 0, +1, +3, and +5 ft SLR increments mapped to the **2024 California Ocean Protection Council (OPC)** guidance:

| SLR Increment | 2024 CA OPC Mapping |
| :--- | :--- |
| **0 ft** | Baseline (Present Day) |
| **+1 ft** | Intermediate (~2050) / Intermediate-High (~2040) |
| **+3 ft** | Intermediate (~2100) / Intermediate-High (~2080) |
| **+5 ft** | Intermediate-High (~2100) |

## Project Structure

```text
├── configs/
│   └── sf_benchmark.yml      # Rain x SLR scenario matrix & domain config
├── pipeline/
│   ├── 01_prep_terrain.py    # DEM, roughness, & urban drainage prep
│   ├── 02_build_sfincs.py    # HydroMT-SFINCS model builder
│   ├── 03_validate_sf.py     # Validation against SFPUC 100-yr flood map
│   └── 04_export_cog.py      # Reproject & export EPSG:3857 COGs
├── web/                      # Vite + MapLibre GL + maplibre-cog-protocol viewer
├── data/                     # Local raw/processed data (git-ignored)
└── environment.yml           # Conda environment (conda-forge)
```

## Quick Start

### Python Modeling Pipeline

```bash
conda env create -f environment.yml
conda activate bay-urban-flood-map

python pipeline/01_prep_terrain.py --config configs/sf_benchmark.yml
python pipeline/02_build_sfincs.py --config configs/sf_benchmark.yml
python pipeline/03_validate_sf.py --config configs/sf_benchmark.yml
python pipeline/04_export_cog.py --config configs/sf_benchmark.yml
```

### Web Viewer

```bash
cd web
npm install
npm run dev
```

Place an EPSG:3857 Cloud Optimized GeoTIFF at `web/public/cogs/sf_100yr_slr0ft.tif` (or update `COG_URL` in `web/src/main.js`) to inspect flood depths with a custom color ramp and click-to-query pixel depth.
