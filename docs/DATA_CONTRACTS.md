# DATA_CONTRACTS.md — Pipeline-to-Web Interfaces

This file defines the exact formats shared between `pipeline/` (Python) and `web/` (JavaScript).

## 1. Directory Layout (`data/`, git-ignored)

```text
data/
├── raw/            # Unmodified downloads (USGS 3DEP, NLCD, DataSF)
├── processed/      # Hydro-enforced DEMs, Curve Number grids (EPSG:26910)
├── runs/           # SFINCS model directories & NetCDF outputs
└── web_outputs/    # Final EPSG:3857 COGs, PMTiles, and manifest.json
```

## 2. Raster Output Contract (COGs)

Every flood raster exported by `04_export_cog.py` for the web frontend must meet these exact specs:

- **Filename Pattern:** `{region}_{storm}_{slr}_{metric}_3857.tif`
  - Examples:
    - `sf_100yr_slr0ft_depth_3857.tif`
    - `sf_100yr_slr3ft_delta_3857.tif`
- **CRS:** `EPSG:3857` (Web Mercator) — required by `@geomatico/maplibre-cog-protocol`.
- **Data Type:** `Float32` (single band).
- **Units:** Meters (`m`).
- **NoData Value:** `-9999.0` (or `NaN`). Mask all dry/trace cells where `depth < 0.05` meters (approx. 2 inches) to NoData so transparent areas render cleanly over basemaps.
- **Format & Compression:** Cloud Optimized GeoTIFF (`-of COG`), `COMPRESS=DEFLATE`, `PREDICTOR=3`, `OVERVIEWS=IGNORE_EXISTING`, `BLOCKSIZE=256`.

## 3. Vector Overlay Contract

- **Format:** Static `PMTiles` (`.pmtiles`) in `EPSG:4326` (WGS84) for regional overlays, or lightweight GeoJSON (`< 2 MB`) for SF benchmark polygons and event-matched validation overlays.
- **Required Benchmark Layer (Tier B, v0.1+):** `sf_sfpuc_100yr_benchmark.geojson` (simplified to 1m tolerance if needed for browser performance).
- **Per-Storm 311 Flood Reports (Tier D, v0.2+ / v0.6+):** `sf_311_{YYYYMMDD}_floods.geojson` (`EPSG:4326` points with `service_request_id`, `requested_datetime`, `service_subtype`, `service_details`, `address`, `neighborhood`, and `storm_id`; `sf_311_20221231_floods.geojson` is retained for backwards compatibility).
- **Per-Storm Roadway Closures & Emergency Dispatches (Tier C, v0.6+):** `sf_tier_c_{YYYYMMDD}_closures.geojson` (`EPSG:4326` points with `incident_id`, `source`, `incident_type`, `requested_datetime`, `address`, and `storm_id`).
- **Coastal / Stream Gauge Peak WSE Pins (Tier A, v0.6+):** `sf_tier_a_gauges.geojson` (`EPSG:4326` points with `station_id`, `name`, `provider`, `storm_id`, `observed_peak_navd88_m`, `modeled_peak_navd88_m`, `bias_m`, `peak_timing_error_min`, and `datum` = `"NAVD88"`).

## 4. Validation Summary Contract (`validation_summary.json`)

Output by `03_validate_sf.py` for parameter sweeps:

```json
{
  "region": "sf_mission_alemany",
  "drainage_rate_mm_hr": 15.0,
  "manning_n_paved": 0.02,
  "depth_threshold_m": 0.1524,
  "metrics": {
    "hit_rate": 0.0,
    "false_alarm_ratio": 0.0,
    "csi": 0.0
  }
}
```

## 5. Scenario Manifest (`data/web_outputs/manifest.json`)

The web frontend reads this file to populate dropdowns and resolve COG URLs without hardcoding filenames:

```json
{
  "version": "0.1",
  "scenarios": [
    {
      "id": "sf_100yr_slr0ft_depth",
      "label": "100-Yr Storm — Today (0 ft SLR)",
      "storm": "100yr",
      "slr_ft": 0,
      "metric": "depth",
      "url": "/data/sf_100yr_slr0ft_depth_3857.tif"
    }
  ]
}
```

- **v0.2+ Extensions:** Historical hindcasts use string storm IDs (e.g., `"storm": "ar_20221231"`), derived SLR delta rasters use `"metric": "delta"`, and entries may include optional `"opc_2024_pathway"` and `"opc_2024_horizon"` metadata strings for UI display.
- **v0.4+ Extensions:** Top-level manifest payload may include `"drainage_tier"` (`"tier0"`, `"tier1a"`, `"tier1b"`, or `"tier2"`) indicating the active storm-drain abstraction tier.
- **v0.6+ Extensions:**
  - Top-level `"version": "0.6"` and `"rain_source"` (e.g., `"mrms"` or `"open_meteo_archive"`).
  - Top-level `"overlays"` object for discovering validation GeoJSONs and dynamic feature counts without hardcoding filenames:
    ```json
    "overlays": {
      "tier_b_benchmark": {
        "url": "/data/sf_sfpuc_100yr_benchmark.geojson",
        "label": "SFPUC 100-Yr Flood Risk"
      },
      "tier_a_gauges": {
        "url": "/data/sf_tier_a_gauges.geojson",
        "label": "NOAA Tide Gauges (NAVD88)"
      },
      "tier_e_nfip": {
        "url": "/data/sf_tier_e_nfip_tracts.geojson",
        "label": "FEMA NFIP Claims (Census Tracts)",
        "count": 44
      },
      "topo_contours": {
        "url": "/data/sf_topo_contours.geojson",
        "label": "10 m Topographic Contours (NAVD88)",
        "count": 1266
      },
      "dem_cog": {
        "url": "/data/sf_hydro_dem_3857.tif",
        "label": "Hydro-Enforced LiDAR DEM (NAVD88)"
      },
      "tier_c_by_storm": {
        "ar_20221231": {
          "url": "/data/sf_tier_c_20221231_closures.geojson",
          "count": 135
        }
      },
      "tier_d_by_storm": {
        "ar_20221231": {
          "url": "/data/sf_311_20221231_floods.geojson",
          "count": 424
        }
      }
    }
    ```

## 6. Multi-Tier Ensemble Validation Contract (`validation_ensemble_metrics.json`)

Output by `03_validate_sf.py` (`validation.ensemble_metrics_file`) for multi-storm hindcast ensembles and multi-tier validation (`docs/VALIDATION_SPEC.md` §4.2). All v0.4 keys (`n_events`, `total_311_points`, `events[].{id, label, scenario, valid_land_points, by_buffer}`, `ensemble_mean.by_buffer`) are preserved unchanged, with additive v0.5+ metadata and tier fields:

```json
{
  "region": "sf_citywide",
  "drainage_tier": "tier0",
  "rain_source": "mrms",
  "n_events": 4,
  "total_311_points": 1535,
  "design_benchmark": {
    "source": "sfpuc_100yr",
    "depth_threshold_m": 0.1524,
    "hit_rate": 0.3887,
    "false_alarm_ratio": 0.7086,
    "csi": 0.1998
  },
  "events": [
    {
      "id": "ar_20221231",
      "label": "Dec 31, 2022 Atmospheric River",
      "scenario": "sf_citywide_ar_20221231",
      "valid_land_points": 424,
      "by_buffer": [],
      "tiers_evaluated": ["311"],
      "road_closures_validation": null,
      "gauge_hwm_validation": null
    }
  ],
  "ensemble_mean": {
    "by_buffer": [
      {
        "radius_m": 25.0,
        "ar_roc_auc": 0.5888,
        "thresholds": {
          "0.05m": {
            "capture_rate": 0.3695,
            "control_rate": 0.2377,
            "lift": 1.251
          }
        }
      }
    ]
  },
  "regional_crosschecks": {
    "groundsource_subbasin_match": null,
    "nfip_tract_spearman_rho": null
  }
}
```

- **Preserved v0.4 keys:** `n_events`, `total_311_points`, `events[].{id, label, scenario, valid_land_points, by_buffer}`, and `ensemble_mean.by_buffer`.
- **Additive v0.5+ top-level keys:** `region`, `drainage_tier`, `rain_source`, `design_benchmark` (`null` when no Tier B benchmark is configured), and `regional_crosschecks`.
- **Additive v0.5+ per-event keys:** `tiers_evaluated` (e.g., `["311"]`), `road_closures_validation` (`null` until Tier C is evaluated), and `gauge_hwm_validation` (`null` until Tier A is evaluated).
