# DATA_SOURCES.md — Upstream Data Sources & Licensing

This registry tracks all external geospatial datasets, climate/tide feeds, basemaps, and solver software used by `bay-urban-flood-map`, along with their source URLs, licenses, and attribution requirements.

> **Note:** Raw and derived datasets live under `data/` and are **git-ignored** (`*.tif`, `*.nc`, `*.gpkg`). Contributors reproduce them locally via `pipeline/00_fetch_data.py` (or manual placement where noted).

## 1. Geospatial & Hydrodynamic Inputs

| Dataset | Provider / Source | License / Terms | Attribution & Usage Notes |
| :--- | :--- | :--- | :--- |
| **USGS 3DEP 1 m LiDAR DEM (`CA_SanFrancisco_B23`)** | USGS The National Map (`tnmaccess.nationalmap.gov`, `/vsicurl/` AWS S3) | **Public Domain** (U.S. Government work) | Credit *U.S. Geological Survey 3D Elevation Program (3DEP)* in documentation/metadata. |
| **Annual NLCD 2021 (Land Cover & Imperviousness)** | MRLC / USGS (`mrlc.gov/geoserver/mrlc_download/wcs`) | **Public Domain** (U.S. Government work) | Credit *USGS / Multi-Resolution Land Characteristics (MRLC) Consortium*. |
| **USDA SSURGO Hydrologic Soil Groups (`CA689`)** | USDA NRCS Soil Data Access (`sdmdataaccess.sc.egov.usda.gov`) | **Public Domain** (U.S. Government work) | Credit *USDA Natural Resources Conservation Service (NRCS) SSURGO*. |
| **OpenStreetMap (Roads, Rail, v0.1 Buildings)** | OpenStreetMap via Overpass API | **ODbL 1.0** (Open Database License) | **Attribution required** in `web/` UI (`© OpenStreetMap contributors`). Derivative model outputs remain openly licensed under ODbL terms where applicable. |
| **SF Building Footprints (`ynuv-fyni`, v0.2)** | City & County of San Francisco / DataSF (`data.sfgov.org`, dataset `ynuv-fyni`) | **Open Data Commons PDDL** / DataSF Terms of Use | Official SF LiDAR-derived building footprints used for citywide v0.2 terrain enforcement. |
| **SFPUC 100-Year Storm Flood Risk Map** | City & County of San Francisco / DataSF (`data.sfgov.org`, dataset `jzu3-4yxp`) | **Open Data Commons PDDL** / DataSF Terms of Use | Credit *San Francisco Public Utilities Commission (SFPUC) via DataSF*. Informational validation layer only; not for parcel-level regulatory determinations. |
| **SF 311 Flood & Sewer Cases (`2022-12-31` AR)** | City & County of San Francisco / DataSF (`data.sfgov.org`, dataset `vw6y-z8j6`) | **Open Data Commons PDDL** / DataSF Terms of Use | Credit *San Francisco 311 via DataSF*. Used solely for historical storm hindcast validation. |
| **SF Bay Shore Inventory (2016 Elevation Segments & BHM 2020 Update)** | San Francisco Estuary Institute (SFEI) (`sfei.org/data/sf-bay-shore-inventory-gis-data`) | **GNU GPL v3.0** (free data per `sfei.org/data/sf-bay-shore-inventory-gis-data`) | **Manual download required** (portal blocks automated bots with HTTP 403). Place extracted archive under `data/raw/sf_citywide/`. Cite *San Francisco Estuary Institute (SFEI), SF Bay Shore Inventory (GNU GPL v3.0)*. |
| **NOAA CO-OPS Water Levels (`9414290`, NAVD88)** | NOAA Tides & Currents (`api.tidesandcurrents.noaa.gov`) | **Public Domain** (U.S. Government work) | Credit *NOAA Center for Operational Oceanographic Products and Services (CO-OPS)*. |
| **NOAA Atlas 14 Vol. 6 v2 IDF Depths** | NOAA NWS Hydrometeorological Design Studies Center | **Public Domain** (U.S. Government work) | Design storm depths (`10yr`, `100yr`) for `(37.745, -122.415)`. |
| **Historical Hourly Precipitation (`2022-12-31` AR)** | Open-Meteo Historical Weather Archive (`archive-api.open-meteo.com`, ERA5-Land / Seamless) | **CC BY 4.0** (Creative Commons Attribution 4.0) | **Attribution required:** Credit *Weather data by Open-Meteo.com (CC BY 4.0)*. |

## 2. Web Basemap & Solver Dependencies

| Component | Source | License | Notes |
| :--- | :--- | :--- | :--- |
| **Deltares SFINCS (`deltares/sfincs-cpu`)** | Deltares (`download.deltares.nl/sfincs`) | **Deltares SFINCS FreeWare Software License Agreement** | Executed locally inside Docker container; pre-compiled binary freeware terms apply to local simulation runs. |
| **OpenFreeMap Positron Basemap** | OpenFreeMap (`tiles.openfreemap.org`) | **MIT / BSD** style + **ODbL 1.0** (`© OpenStreetMap contributors`, `© OpenMapTiles`) | Rendered dynamically in MapLibre GL JS; attribution displayed by basemap style. |
| **MapLibre GL JS & `@geomatico/maplibre-cog-protocol`** | npm packages (`web/package.json`) | **BSD-3-Clause** / **MIT** | Bundled into static frontend. |
