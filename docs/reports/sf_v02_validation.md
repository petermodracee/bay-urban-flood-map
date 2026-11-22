# San Francisco Citywide v0.2 Dual Validation Report

## Model Configuration
- **Region:** `sf_citywide`
- **Uniform Drainage Capacity:** `5.0 mm/hr`
- **Paved Manning's n:** `0.030`
- **Depth Threshold (SFPUC):** `0.1524 m` (`6 in`)

## 1. Citywide SFPUC 100-Year Storm Validation (`sf_citywide_100yr_slr0ft`)

| Scope | Hit Rate | False Alarm Ratio | CSI | Land Cells | Obs Flood Cells | Mod Flood Cells |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Citywide Land (>= 0.5 m)** | 0.3884 | 0.7083 | 0.1999 | — | — | — |
| Mission | 0.2596 | 0.7115 | 0.1583 | 1,300,000 | 51,328 | 46,173 |
| Alemany | 0.4435 | 0.1309 | 0.4157 | 1,170,000 | 123,492 | 63,010 |
| SOMA / Mission Bay | 0.4632 | 0.3849 | 0.3591 | 1,500,260 | 234,117 | 176,318 |
| Marina / Crissy Field | 0.3124 | 0.9024 | 0.0804 | 1,072,092 | 11,256 | 36,016 |
| Sunset / Lake Merced | 0.3191 | 0.8260 | 0.1269 | 6,326,422 | 114,498 | 209,993 |
| Bayview / Islais Creek | 0.4155 | 0.5386 | 0.2798 | 2,371,471 | 253,711 | 228,496 |
| Visitacion Valley | 0.0760 | 0.9776 | 0.0176 | 1,190,387 | 7,736 | 26,265 |

## 2. Dec 31, 2022 Atmospheric River Hindcast vs. SF 311 Reports (3-Way Comparison)

- **311 Flood Reports Evaluated:** `424` of `424` points on valid land (`dropped=0`)
- **Random Dry Control Points:** `2000`

| Buffer | AR >= 0.05 m Hit Rate | AR 0.05 m Ctrl Rate | AR 0.05 m Lift | AR >= 0.1524 m Hit Rate | AR 0.1524 m Lift | SFPUC Hit Rate | SFPUC Lift | AR ROC-AUC |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 m | 17.2% (73) | 4.8% | 3.59x | 12.7% (54) | 3.92x | 21.7% (92) | 9.23x | 0.5748 |
| 25 m | 67.2% (285) | 39.4% | 1.71x | 53.3% (226) | 1.85x | 27.1% (115) | 7.04x | 0.6715 |
| 50 m | 83.5% (354) | 66.3% | 1.26x | 74.1% (314) | 1.38x | 29.9% (127) | 5.30x | 0.6596 |

## 3. 311 Subtype Breakdown (`50 m` buffer)

| Service Subtype | Count | AR >= 0.05 m (25 m) | AR >= 0.05 m (50 m) | AR >= 0.1524 m (50 m) | SFPUC (50 m) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Flooding | 356 | 70.8% | 85.1% | 76.4% | 33.1% |
| Sewage_back_up | 51 | 47.1% | 76.5% | 60.8% | 13.7% |
| other | 15 | 53.3% | 73.3% | 66.7% | 13.3% |
| Odor | 1 | 100.0% | 100.0% | 100.0% | 0.0% |
| Water_leak | 1 | 0.0% | 0.0% | 0.0% | 0.0% |

## 4. Top SF Neighborhoods by Dec 31, 2022 311 Volume (`50 m` buffer)

| Neighborhood | 311 Calls | AR >= 0.05 m (25 m) | AR >= 0.05 m (50 m) | AR >= 0.1524 m (50 m) | SFPUC (50 m) |
| --- | ---: | ---: | ---: | ---: | ---: |
| South of Market | 67 | 97.0% | 100.0% | 97.0% | 73.1% |
| Mission | 27 | 88.9% | 96.3% | 96.3% | 70.4% |
| Bernal Heights | 26 | 42.3% | 69.2% | 53.8% | 34.6% |
| Central Waterfront | 14 | 92.9% | 100.0% | 100.0% | 35.7% |
| Civic Center | 12 | 100.0% | 100.0% | 91.7% | 50.0% |
| Western Addition | 11 | 100.0% | 100.0% | 90.9% | 18.2% |
| Lower Pacific Heights | 10 | 100.0% | 100.0% | 80.0% | 60.0% |
| Pacific Heights | 10 | 40.0% | 90.0% | 70.0% | 0.0% |
| Noe Valley | 9 | 44.4% | 66.7% | 66.7% | 0.0% |
| Potrero Hill | 9 | 33.3% | 66.7% | 66.7% | 0.0% |
| Seacliff | 9 | 0.0% | 100.0% | 100.0% | 0.0% |
| Glen Park | 8 | 50.0% | 87.5% | 87.5% | 0.0% |

## 5. Diagnostic Clusters

### Top Land False-Positive Clusters (`sf_citywide_100yr_slr0ft` vs. SFPUC)

| Area (`m^2`) | Centroid Easting (`m`) | Centroid Northing (`m`) |
| ---: | ---: | ---: |
| 168,000 | 543374.4 | 4178068.0 |
| 95,600 | 543639.9 | 4176383.4 |
| 78,400 | 552478.2 | 4173377.6 |
| 73,200 | 547845.1 | 4184305.2 |
| 69,200 | 543244.0 | 4177796.8 |
| 62,000 | 554595.9 | 4175295.2 |
| 59,200 | 552989.3 | 4183310.7 |
| 52,000 | 550882.8 | 4179544.0 |

### Top Missed 311 Clusters (`AR < 0.05 m` at `50 m`)

| Neighborhood | Missed / Total | Caught by SFPUC | Sample Addresses |
| --- | ---: | ---: | --- |
| Bernal Heights | 8 / 26 | 1 | Intersection of COLERIDGE ST and HEYMAN AVE, 66 FAIR AVE, SAN FRANCISCO, CA, 94110, 1 LUNDYS LN, SAN FRANCISCO, CA, 94110 |
| Miraloma Park | 4 / 6 | 0 | 1257 BOSWORTH ST, SAN FRANCISCO, CA, 94131, 238 JUANITA WAY, SAN FRANCISCO, CA, 94127, 236 JUANITA WAY, SAN FRANCISCO, CA, 94127 |
| Visitacion Valley | 4 / 5 | 0 | 16 ARLETA AVE, SAN FRANCISCO, CA, 94134, 87 ALPHA ST, SAN FRANCISCO, CA, 94134, 1 TUCKER AVE, SAN FRANCISCO, CA, 94134 |
| Dolores Heights | 3 / 5 | 0 | 3770 CESAR CHAVEZ ST, SAN FRANCISCO, CA, 94110, 177 LIBERTY ST, SAN FRANCISCO, CA, 94110, 454 LIBERTY ST, SAN FRANCISCO, CA, 94114 |
| Midtown Terrace | 3 / 4 | 0 | 26 OLYMPIA WAY, SAN FRANCISCO, CA, 94131 |
| Noe Valley | 3 / 9 | 0 | Intersection of DIAMOND ST and DUNCAN ST, 4254 CESAR CHAVEZ ST, SAN FRANCISCO, CA, 94131, 4230 CESAR CHAVEZ ST, SAN FRANCISCO, CA, 94131 |
| Potrero Hill | 3 / 9 | 0 | Intersection of 25TH ST and WISCONSIN ST, 1637 19TH ST, SAN FRANCISCO, CA, 94107, 310 MISSOURI ST, SAN FRANCISCO, CA, 94107 |
| Upper Market | 3 / 3 | 0 | 4300 24TH ST, SAN FRANCISCO, CA, 94114, Intersection of DOUGLASS ST and ELIZABETH ST, 3721 MARKET ST, SAN FRANCISCO, CA, 94131 |
| Alamo Square | 2 / 3 | 0 | 1001 FULTON ST, SAN FRANCISCO, CA, 94117, Intersection of FULTON ST and STEINER ST |
| Ashbury Heights | 2 / 2 | 0 | 63 CARMEL ST, SAN FRANCISCO, CA, 94117, 832 CLAYTON ST, SAN FRANCISCO, CA, 94117 |
