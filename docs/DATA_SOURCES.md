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
| **SF Building Footprints (`ynuv-fyni`, v0.2+)** | City & County of San Francisco / DataSF (`data.sf.gov`, dataset `ynuv-fyni`) | **Open Data Commons PDDL** / DataSF Terms of Use | Official SF LiDAR-derived building footprints used for citywide terrain enforcement (`v0.2`–`v0.6.1`). |
| **SFPUC 100-Year Storm Flood Risk Map & Flood Elevations** | City & County of San Francisco / DataSF (`data.sf.gov`, datasets `jzu3-4yxp` & `wppz-u2hi`, Dec 2024) | **Open Data Commons PDDL** / DataSF Terms of Use | Credit *San Francisco Public Utilities Commission (SFPUC) via DataSF*. Informational validation layer only; `wppz-u2hi` elevations are in SF City Datum (converted to NAVD88 via nominal `+11.35 ft` offset, SF Public Works CCSF-VD13). |
| **SF 311 Flood & Sewer Cases (5-AR Ensemble)** | City & County of San Francisco / DataSF (`data.sf.gov`, dataset `vw6y-z8j6`) | **Open Data Commons PDDL** / DataSF Terms of Use | Credit *San Francisco 311 via DataSF*. Used solely for historical Atmospheric River hindcast validation (`2021-10-24`, `2022-12-31`, `2023-01-09`, `2024-02-04`, `2024-11-22`). |
| **SF Bay Shore Inventory (2016 Elevation Segments & BHM 2020 Update)** | San Francisco Estuary Institute (SFEI) (`sfei.org/data/sf-bay-shore-inventory-gis-data`) | **GNU GPL v3.0** (free data per `sfei.org/data/sf-bay-shore-inventory-gis-data`) | **Manual download required** (portal blocks automated bots with HTTP 403). Place extracted archive under `data/raw/sf_citywide/`. Cite *San Francisco Estuary Institute (SFEI), SF Bay Shore Inventory (GNU GPL v3.0)*. |
| **NOAA CO-OPS Water Levels (`9414290`, `9414750`, `9414863`, `9414523`, NAVD88)** | NOAA Tides & Currents (`api.tidesandcurrents.noaa.gov`) | **Public Domain** (U.S. Government work) | Credit *NOAA Center for Operational Oceanographic Products and Services (CO-OPS)*. Multi-gauge anchor stations for SF Bay tidal gradients. |
| **NOAA Atlas 14 Vol. 6 v2 IDF Depths & Temporal Distributions** | NOAA NWS Hydrometeorological Design Studies Center | **Public Domain** (U.S. Government work) | Coincident stress-scenario depths (`10yr`, `100yr`) for `(37.745, -122.415)` and CA Region 8 24-hr temporal distribution (`ca_8_24h_temporal.csv`) for hyetograph sensitivity. |
| **NOAA MRMS `MultiSensor_QPE_01H_Pass2` (1 km Gridded QPE)** | NOAA NSSL via AWS S3 (`noaa-mrms-pds`) / Iowa Environmental Mesonet (`mtarchive.geol.iastate.edu`) | **Public Domain** (U.S. Government work) | Hourly gauge-corrected radar QPE for historical AR hindcasts (`2021-10-24`, `2022-12-31`, `2023-01-09`, `2024-02-04`, `2024-11-22`). |
| **Historical Hourly Precipitation (Open-Meteo Control)** | Open-Meteo Historical Weather Archive (`archive-api.open-meteo.com`, ERA5-Land / Seamless) | **CC BY 4.0** (Creative Commons Attribution 4.0) | **Attribution required:** Credit *Weather data by Open-Meteo.com (CC BY 4.0)*. |

## 2. Multi-Source Validation Datasets (v0.5+)

| Dataset | Provider / Source | License / Terms | Usage & Attribution Notes |
| :--- | :--- | :--- | :--- |
| **USGS NWIS & STN High-Water Marks** | USGS Water Data / Flood Event Viewer | **Public Domain** (U.S. Government work) | Credit *U.S. Geological Survey*. Convert any `NGVD29` records to `NAVD88` meters. |
| **Caltrans QuickMap / CHP Incident Logs / 511 SF Bay** | Caltrans / CHP / MTC 511 | **Public / CA Open Data** | Open agency feeds for highway & underpass flood closures (live-only; PeMS historical archive pending portal upgrade). |
| **SF DEM Dispatched Calls — Closed (`2zdj-bwza`)** | City & County of San Francisco / DataSF (`data.sf.gov/resource/2zdj-bwza.json`) | **Open Data Commons PDDL** / DataSF Terms of Use | Filtered to `call_type_final_desc = 'TRAFFIC HAZARD'` within AR hindcast windows (`intersection_point`, `cad_number`); includes wind/debris hazards alongside flooding. |
| **SFFD Fire Calls (`nuek-vuh3`)** | City & County of San Francisco / DataSF (`data.sf.gov/resource/nuek-vuh3.json`) | **Open Data Commons PDDL** / DataSF Terms of Use | Filtered to `call_type = 'Water Rescue'` within AR hindcast windows (`case_location`), deduplicated by `incident_number`. |
| **Google Research Groundsource** | Google Research (Zenodo DOI `10.5281/zenodo.18647054`, `groundsource_2026.parquet`) | **CC BY 4.0** | Credit *Google Research Groundsource (CC BY 4.0)*. Used as a storm-date discovery catalog only (zero specificity on SF sub-basins). |
| **OpenFEMA NFIP Redacted Claims (`v3/NfipClaims`)** | FEMA OpenFEMA API (`www.fema.gov/api/open/v3/NfipClaims`) | **Public Domain** (U.S. Government work) | `censusGeoid` aggregated to 11-digit census tracts via TIGERweb; use only for tract-level loss correlation (`v0.5+`). |
| **U.S. Census Bureau ACS 5-Year Estimates (`B19013`)** | U.S. Census Bureau (via Census Reporter API) | **Public Domain** (U.S. Government work) | Census-tract median household income used for Tier D (311) income-tercile socioeconomic reporting-bias stratification (`v0.6.1`). |
| **Copernicus Sentinel-1 SAR** | ESA / Copernicus Data Space | **Copernicus Open Access** | Credit *Copernicus Sentinel data*. Flat Bay-margin/floodplain extent only (`v0.5+`). |

## 3. Web Basemap & Solver Dependencies

| Component | Source | License | Notes |
| :--- | :--- | :--- | :--- |
| **Deltares SFINCS (`deltares/sfincs-cpu`)** | Deltares (`download.deltares.nl/sfincs`) | **Deltares SFINCS FreeWare Software License Agreement** | Executed locally inside Docker container; pre-compiled binary freeware terms apply to local simulation runs. |
| **OpenFreeMap Positron Basemap** | OpenFreeMap (`tiles.openfreemap.org`) | **MIT / BSD** style + **ODbL 1.0** (`© OpenStreetMap contributors`, `© OpenMapTiles`) | Rendered dynamically in MapLibre GL JS; attribution displayed by basemap style. |
| **AWS Terrain Tiles (Terrarium)** | Registry of Open Data on AWS (`s3.amazonaws.com/elevation-tiles-prod/terrarium`, Mapzen/Tilezen) | **Public / Open Data** (multi-source attribution per Tilezen/Mapzen) | Fallback 3D terrain tiles composited outside SF behind `sf_hydro_dem_3857.tif` via `hybrid-dem://`. |
| **Komoot Photon Geocoder** | Komoot (`photon.komoot.io`) | **Apache-2.0** software + **ODbL 1.0** (`© OpenStreetMap contributors`) | Bounded SF address/intersection search in `web/src/scenarioControls.js`. |
| **MapLibre GL JS & `@geomatico/maplibre-cog-protocol`** | npm packages (`web/package.json`) | **BSD-3-Clause** / **MIT** | Bundled into static frontend. |
