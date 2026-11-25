# SF Bay Urban Flood Map v0.6.1 — Phase 2 Rescore Report

Generated from frozen v0.6 outputs (`pipeline/03_rescore_v061.py`, no new SFINCS runs).

## 1. Groundsource (Item 14)

- **Sub-basins:** 41 | **Observed flooded:** 37 | **Modeled flooded:** 41
- **Confusion Matrix:** TP=37, FP=4, FN=0, TN=0
- **Presence Hit Rate:** 1.0000 | **Specificity:** 0.0000 | **Accuracy:** 0.9024 (base rate = 37/41 = 0.9024)
- **Conclusion:** The model flags all 41 sub-basins as flooded, showing zero discrimination on negative sub-basins.

## 2. Tier B — SFPUC 100-Yr Benchmark (Item 15)

Deep-and-contiguous filter: `>= 0.1524 m` (6 in) over connected area `>= 5574 m^2` (Half of ~120,000 sq ft (~275 x 436 ft) average SF city block = 60,000 sq ft (5,574 m2)).

| SLR Scenario | Raw HR / FAR / CSI | Filtered (5,574 m²) HR / FAR / CSI | Median Depth Inside vs Outside |
| --- | --- | --- | --- |
| +0ft | 0.3901 / 0.7188 / 0.1953 | 0.3582 / 0.5961 / 0.2344 | 0.000 m vs 0.000 m |
| +1ft | 0.3945 / 0.7299 / 0.1909 | 0.3628 / 0.6238 / 0.2265 | 0.000 m vs 0.000 m |
| +3ft | 0.4060 / 0.7643 / 0.1753 | 0.3755 / 0.7008 / 0.1998 | 0.000 m vs 0.000 m |
| +5ft | 0.4217 / 0.8017 / 0.1559 | 0.3922 / 0.7702 / 0.1695 | 0.000 m vs 0.000 m |

### SFPUC 100-Year Storm Flood Elevations (`wppz-u2hi`, 1450 polygons)

- Datum conversion: NAVD88 (ft) = SFCD (ft) + 11.35 (SF Public Works CCSF-VD13 nominal) | Bias sign convention: model minus SFPUC benchmark
- **Raw rounded-up (`contourmax`, biased high by 0–0.3048 m) — all zone cells (687,985):** SFPUC implied depth mean 0.1621 m vs. modeled mean 0.1996 m; depth RMSE = 0.3054 m, MAE = 0.0951 m, bias = +0.0375 m, Spearman rho = 0.8461
- **Raw rounded-up (`contourmax`) — mutually wet cells >= 0.05 m (186,710):** depth Pearson r = 0.7393, Spearman rho = 0.7679, RMSE = 0.4115 m, MAE = 0.2211 m, bias = +0.1308 m
- **Approx. rounding-corrected (`contourmax - 0.1524 m` / band midpoint) — all zone cells (687,985):** SFPUC implied depth mean 0.1163 m vs. modeled mean 0.1996 m; depth RMSE = 0.3247 m, MAE = 0.1164 m, bias = +0.0833 m, Spearman rho = 0.7956
- **Approx. rounding-corrected — mutually wet cells >= 0.05 m (149,768):** depth Pearson r = 0.7361, Spearman rho = 0.7626, RMSE = 0.4842 m, MAE = 0.3216 m, bias = +0.2711 m

## 3. Tier D (311 Reports) — Valid-Land vs Right-of-Way Controls (Item 16)

Format: `Cap / Ctrl Rate / Lift / AUC`

| Event (Pts) | 0 m (Valid Land) | 0 m (Right-of-Way) | 25 m (Valid Land) | 25 m (Right-of-Way) | 50 m (Right-of-Way) |
| --- | --- | --- | --- | --- | --- |
| Oct 24, 2021 Atmospheric River (567) | 4.06% / 2.40% / 1.690x / 0.5129 | 4.06% / 3.90% / 1.040x / 0.5025 | 53.62% / 33.25% / 1.612x / 0.6123 | 53.62% / 37.90% / 1.415x / 0.5833 | 79.01% / 69.40% / 1.139x / 0.5472 |
| Dec 31, 2022 Atmospheric River (424) | 7.08% / 2.05% / 3.451x / 0.5286 | 7.08% / 3.15% / 2.246x / 0.5221 | 58.73% / 30.90% / 1.901x / 0.6607 | 58.73% / 35.75% / 1.643x / 0.6350 | 77.59% / 66.90% / 1.160x / 0.5946 |
| Jan 9, 2023 Atmospheric River (403) | 0.99% / 0.90% / 1.103x / 0.5066 | 0.99% / 0.65% / 1.527x / 0.5061 | 34.74% / 22.45% / 1.547x / 0.6256 | 34.74% / 22.30% / 1.558x / 0.6053 | 70.47% / 56.55% / 1.246x / 0.5809 |
| Feb 4, 2024 Atmospheric River (141) | 0.00% / 0.35% / 0.000x / 0.4983 | 0.00% / 0.00% / n/a / 0.5000 | 0.71% / 2.65% / 0.268x / 0.4980 | 0.71% / 1.90% / 0.373x / 0.5057 | 2.13% / 4.15% / 0.513x / 0.4901 |
| Nov 22, 2024 Atmospheric River (536) | 11.19% / 2.85% / 3.928x / 0.5547 | 11.19% / 4.35% / 2.573x / 0.5436 | 57.09% / 32.65% / 1.749x / 0.6419 | 57.09% / 37.45% / 1.524x / 0.6125 | 82.65% / 68.00% / 1.215x / 0.5807 |
| **5-AR Unweighted Mean** | 4.66% / 1.71% / 2.034x / 0.5202 | 4.66% / 2.41% / 1.846x / 0.5149 | 40.98% / 24.38% / 1.415x / 0.6077 | 40.98% / 27.06% / 1.303x / 0.5884 | 62.37% / 53.00% / 1.055x / 0.5587 |
| **5-AR Pooled (2,071 pts)** | 5.65% / 2.01% / 2.806x / 0.5247 | 5.65% / 2.96% / 1.905x / 0.5177 | 48.29% / 28.43% / 1.698x / 0.6247 | 48.29% / 31.86% / 1.516x / 0.6004 | 72.77% / 61.58% / 1.182x / 0.5682 |

### Tier D Income Terciles (Right-of-Way Controls, Pooled 5-AR)

| Income Tercile (Tracts) | Income Range (USD) | 311 Pts | 0 m Cap / Ctrl / Lift / AUC | 25 m Cap / Ctrl / Lift / AUC |
| --- | --- | --- | --- | --- |
| Low Income (78) | \$12,908–\$122,222 | 534 | 2.06% / 1.27% / 1.619x / 0.5130 | 48.50% / 29.58% / 1.640x / 0.6253 |
| Middle Income (78) | \$122,422–\$175,524 | 662 | 5.44% / 3.33% / 1.631x / 0.5131 | 48.64% / 29.10% / 1.672x / 0.6178 |
| High Income (78) | \$176,000–\$250,001 | 788 | 6.09% / 2.06% / 2.951x / 0.5239 | 44.92% / 22.37% / 2.008x / 0.6337 |

## 4. Tier C (Emergency Dispatches) — Valid-Land vs Right-of-Way Controls

| Summary (310 pts) | 0 m | 25 m | 50 m |
| --- | --- | --- | --- |
| Unweighted Mean (Valid Land) | 6.87% / 1.71% / 2.844x / 0.5270 | 35.78% / 24.38% / 1.249x / 0.5791 | 63.04% / 47.07% / 1.207x / 0.6043 |
| Unweighted Mean (Right-of-Way) | 6.87% / 2.41% / 2.302x / 0.5218 | 35.78% / 27.06% / 1.157x / 0.5595 | 63.04% / 53.00% / 1.106x / 0.5725 |
| Pooled (Right-of-Way) | 6.45% / 2.25% / 2.872x / 0.5202 | 32.90% / 24.44% / 1.346x / 0.5567 | 55.48% / 46.86% / 1.184x / 0.5639 |

## 5. Tier E — NFIP Claims (Item 17)

- **Tracts:** 259 | **Zero-claim tracts:** 215 (83.01%)
- **Exposed buildings vs claims:** Spearman rho = 0.0956 (p = 0.1248); Kendall tau-b = 0.0923 (p = 0.1205)
- **Flooded land area vs claims:** Spearman rho = 0.2289 (p = 0.0002); Kendall tau-b = 0.1823 (p = 0.0003)
- **Binary presence (>=1 claim vs 0 claims) on flooded land area:** AUC = 0.6765 (p = 0.0001)
