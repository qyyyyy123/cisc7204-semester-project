# Xiamen shared-bicycle tidal imbalance - final report

**CISC7204 semester project, Class B - Team 02**

Generated: 2026-11-04 15:28 (Asia/Shanghai)  
Dataset: `Shared bicycle data.zip` (Zenodo 10.5281/zenodo.17415891, CC BY-NC 4.0)  
Code: `xm_core.py`, `run_pipeline.py`, `dash_app.py`, `Xiamen_SharedBicycle_Analysis.ipynb`

All text, figure labels, table headers and code comments in this project are in English.
Figures use DejaVu Sans and contain no CJK glyphs.

---

## 0. Executive summary

| Item | Result |
|---|---|
| Data parsed | 441,350 order events, 10,522 fence rows (10,517 unique polygons), 2,849,243 GPS fixes, 144 hourly weather rows |
| Trips reconstructed | **212,680** trips with duration in [60, 3600] s (+0.02% vs the published 212,630) |
| Endpoint-to-fence match rate | 67.07% (containment + 50 m snap) |
| RQ1 | 1,075 net-source and 1,049 net-sink fences; Moran's I = 0.123 (p = 0.001); the morning tilt reverses around 08:00-08:30 |
| RQ2 | omega = **1.493** mean / **1.299** median over 29,668 rides; zone p = 1.47e-12, hour p = 4.09e-04 -> not constant |
| RQ3 | Louvain: 7 communities, modularity 0.716, balance 0.0225 vs baseline modularity 0.642, balance 0.0871 |
| RQ4 | Leave-one-day-out MAE 3.730, R2 0.430, r 0.670; held-out future day MAE 2.734, R2 0.704 |

---

## 1. Data and pipeline

### 1.1 What the archive actually contains

Every file with a `.csv` extension in the archive is in fact an Office Open XML workbook
(the byte stream starts with `PK\x03\x04`). The loader therefore sniffs the container instead
of trusting the extension, and the repository `README.md` (which ships inside the archive and
functions as the data dictionary) is used to confirm field meanings. Four tables are used (the
trajectories ship as five daily files):

| Role | File | Rows | Notes |
|---|---|---|---|
| Order | `raw/Order data/order data.csv` | 441,350 | one row per lock event, `BICYCLE_ID` written only on the first row of each bicycle block |
| Fence | `raw/Electronic fence data/STATION.csv` | 10,522 raw rows / 10,517 unique | `FENCE_LOC` is a closed five-vertex ring in `[longitude, latitude]` order |
| Trajectory | `raw/Trajectory data/Trajectory data_2020122*.csv` | 2,849,243 | clock times only; the date comes from the file name |
| Weather | `raw/Weather data/Weather data.csv` | 144 | hourly; precipitation and visibility columns are empty |

The MATLAB scripts and the `.xlsx` cluster export under `Algorithm data/` are model artefacts of
the published study, not raw data, and are excluded from the pipeline.

### 1.2 Module map

| Stage | Function in `xm_core.py` | Output |
|---|---|---|
| M0 | `find_dataset_zip`, `extract_dataset`, `discover_data_files`, `classify_table` | `figures/data_inventory.md` |
| M1 | `write_quality_report` | `figures/data_quality_report.md`, `data_quality_metrics.json` |
| M2 | `build_od`, `write_od` | `figures/od_trips.csv` |
| M3 | `FenceIndex`, `assign_points_to_fence` | `o_fence_id`, `d_fence_id`, `fence_match_rate.json` |
| M4 | `prep_weather`, `link_weather` | weather columns in the panel |
| M5 | `panel_fence_halfhour`, `tidal_table` | `figures/panel_fence_halfhour.csv` |
| M6 | `validate_pipeline` | `figures/validation.json` |
| M7 | `fence_summary`, `fence_slot_summary`, `morans_i` | maps, `tidal_top_fences.csv`, `morans_i.json` |
| M8 | `compute_omega`, `ols_hc1`, `omega_group_tests` | `omega_overall.json`, `omega_by_zone.csv` |
| M9 | `build_fence_network`, `louvain_partition`, `paper_style_partition` | `community_comparison.csv` |
| M10 | `build_prediction_frame`, `run_prediction` | `model_metrics.json` |
| M11 | `make_folium_map`, `dash_app.py` | `folium_tidal_map.html`, dashboard |

Total pipeline runtime in the last full run: **0.9 min** (with the parse cache warm; a cold run re-parses the workbooks once and takes several minutes longer).

---

## 2. RQ1 - How is the net inflow/outflow distributed, and how does the tidal pattern evolve?

### 2.1 Distribution

Across the five mornings, 2,905 fences have at least 20 movements and are mapped. 1,075 are net sources (tidal index > +0.2), 1,049 are net sinks (< -0.2) and 781 are balanced. The distribution is strongly polarised: the interquartile range of the tidal index spans -0.391 to 0.400.

| Extreme | Fence | Pickups | Returns | Net flow | Tidal index |
|---|---|---|---|---|---|
| Largest net sink | `WANGHAILU0_R_1` | 29 | 537 | -508 | -0.898 |
| Largest net source | `HUBINZHONGLU0_L_1` | 744 | 37 | +707 | 0.905 |

The fence identifiers encode the street name in pinyin, which gives a coarse description of the
two poles: the strongest sinks sit on the commercial/office corridors in the east of the mapped
area (`GUANRILU`, `WANGHAILU`, software-park side), while the strongest sources are residential
blocks in the south-west (`QIANPUDONGLU`, `LVLINGLU`, `JINSHANGLU`). This is a description of the
coordinates in `figures/tidal_top_fences.csv`, not an administrative classification.

### 2.2 Spatial clustering

Moran's I of the fence-level tidal index with a 500 m binary distance band is **0.1229** against an expectation of -0.0003 under spatial randomness (z = 37.7, permutation p = 0.001, 999 permutations, 89,614 neighbour pairs). Positive and highly significant: sources and sinks come in neighbourhoods, not as isolated points.

### 2.3 Evolution over the eight 30-minute slots

| Slot | Fences with >= 5 movements | Mean tidal index | Share net source | Share net sink | Pickups | Returns |
|---|---|---|---|---|---|---|
| 06:00-06:30 | 207 | +0.050 | 47.8% | 38.2% | 900 | 744 |
| 06:30-07:00 | 569 | +0.071 | 46.0% | 35.3% | 2,713 | 2,202 |
| 07:00-07:30 | 1,267 | +0.037 | 42.8% | 36.5% | 6,882 | 5,840 |
| 07:30-08:00 | 2,699 | -0.007 | 40.3% | 38.1% | 18,468 | 16,946 |
| 08:00-08:30 | 3,034 | -0.021 | 37.5% | 39.1% | 22,275 | 21,321 |
| 08:30-09:00 | 2,422 | +0.002 | 37.9% | 36.5% | 16,574 | 15,814 |
| 09:00-09:30 | 1,254 | +0.048 | 38.8% | 32.1% | 6,525 | 5,830 |
| 09:30-10:00 | 556 | +0.057 | 39.9% | 31.5% | 2,395 | 2,014 |

The share of fences that are net sources falls from 47.8% at 06:00-06:30 to 37.5% at 08:00-08:30, the slot in which the share of net sinks peaks (39.1%) and in which the mean tidal index of the slot reaches its minimum (-0.021, against +0.050 in the first slot). In the busy-fence panel (cells with >= 10 movements) the median tidal index is highest in the early slots (0.50 at 06:00-06:30, 0.81 at 06:30-07:00) and settles around 0.17-0.18 from 08:00 onwards.

**Answer (RQ1).** The morning commute is a two-sided tide: a large number of small residential
fences emit bicycles from 06:00 onwards, and a smaller number of large office/metro-adjacent
fences absorb them, with the arrival peak at 08:00-08:30. Net outflow is spatially clustered
(Moran's I highly significant) but the clusters are street-scale rather than district-scale, so a
rebalancing plan must work at fence granularity, not at district granularity.

*Figures:* `fig_tidal_map.png`, `fig_tidal_evolution.png`, `fig_tidal_box.png`.

---

## 3. RQ2 - The measured detour factor omega (core contribution)

### 3.1 Method

omega = visited path length / straight-line distance, measured on GPS geometry:

1. The trajectory files carry no trip identifier and cover only part of each bicycle's day (rule
   A8), so a ride is reconstructed as a **continuous tracking episode**: a maximal run of fixes of
   the same bicycle with no gap above 600 s.
2. Within an episode the fixes are sorted in time, the path is the sum of great-circle hops and the
   straight-line reference is the chord between the first and the last fix.
3. Filters (verbatim from the specification): >= 5 fixes, straight distance >= 200 m, omega in [1.0, 4.0].

Filter funnel: 49,326 episodes -> 29,668 valid omega values. The median episode lasts 5.8 min, close to the median OD trip duration of 7.0 min, so an episode behaves like a ride.

### 3.2 Result

| Statistic | Value |
|---|---|
| n (rides) | 29,668 |
| Mean omega | **1.4934** |
| 95% bootstrap CI of the mean | [1.4868, 1.5000] |
| Median omega | **1.2994** |
| 95% bootstrap CI of the median | [1.2961, 1.3023] |
| Standard deviation | 0.5551 |
| Quartiles | 1.162 / 1.299 / 1.561 |
| 95th percentile | 2.816 |
| Share of rides below 1.529 | 73.5% |
| Median path length | 1,144 m |
| Median straight distance | 807 m |
| Cross-check inside OD trip windows | n = 2,456, mean 1.377, median 1.221 |

The bootstrap resamples rides 1,000 times (seed 42). The two independent reconstructions agree
closely, which is the strongest evidence that omega is measured, not assumed.

### 3.3 Is omega constant?

| Grouping | Group | n | Mean omega | Median omega |
|---|---|---|---|---|
| Fence tidal role | balanced | 5,306 | 1.494 | 1.302 |
| Fence tidal role | sink | 4,415 | 1.478 | 1.302 |
| Fence tidal role | source | 8,711 | 1.559 | 1.332 |
| Fence tidal role | unassigned | 11,236 | 1.449 | 1.280 |
| Hour | 06:00 | 2,435 | 1.589 | 1.325 |
| Hour | 07:00 | 9,288 | 1.485 | 1.295 |
| Hour | 08:00 | 13,274 | 1.481 | 1.301 |
| Hour | 09:00 | 4,671 | 1.495 | 1.298 |

* **By fence tidal role:** Kruskal-Wallis H = 54.5, **p = 1.47e-12** (Welch ANOVA p = 1.27e-16).
* **By hour:** Kruskal-Wallis H = 18.2, **p = 4.09e-04** (Welch ANOVA p = 1.04e-12).
* **By OD community:** Kruskal-Wallis H = 46.1, p = 2.77e-08.

HC1 heteroskedasticity-robust OLS with zone and hour dummies (implemented from scratch, no
`statsmodels` dependency) confirms that the zone contrast survives mutual adjustment:

| Term | Coefficient | HC1 std. error | t | p | 95% CI |
|---|---|---|---|---|---|
| zone_balanced | 0.3076 | 0.0063 | 48.60 | < 1e-16 | [0.2952, 0.3200] |
| zone_sink | 0.2907 | 0.0065 | 44.43 | < 1e-16 | [0.2779, 0.3035] |
| zone_source | 0.3710 | 0.0057 | 64.61 | < 1e-16 | [0.3598, 0.3823] |

**Answer (RQ2).** omega is **not** a constant. Its measured mean is 1.493 (median 1.299), close to the 1.529 value that the published scheduling model borrows from Ballou (2002), but the distribution is wide (1.16-1.56 interquartile range, 95th percentile 2.82) and 73.5% of rides are below that constant. The ratio differs significantly across fence roles and across hours, and the zone effect survives a robust regression that also controls for the hour. Rides that start in a residential net-source area detour more than rides that start in a sink or a balanced area. Using a single borrowed constant therefore misprices most trips: it overstates the distance cost of the majority of short residential rides and understates the cost of the long tail. A zone- and hour-specific omega, or the empirical distribution itself, is the defensible input to a scheduling model.

*Figures:* `fig_omega_dist.png`, `fig_omega_by_zone.png`, `fig_omega_by_hour.png`. Data: `omega_overall.json`, `omega_by_zone.csv`, `omega_group_tests.json`, `omega_trips.csv`.

---

## 4. RQ3 - OD-flow communities vs the published affinity clustering

The OD matrix is symmetrised (`W = OD + OD.T`), fences with fewer than 20 movements are dropped, leaving **2,761 fence nodes and 37,488 undirected edges**.

| Method | Communities | Modularity | Silhouette (location + demand) | Mean abs. net-flow balance | Max abs. balance |
|---|---|---|---|---|---|
| Louvain on symmetrised OD flow graph (ours) | 7 | 0.716 | 0.298 | 0.0225 | 0.102 |
| paper-style affinity S^g*K(d)*M + K-Means++ (baseline) | 7 | 0.642 | 0.300 | 0.0871 | 0.275 |

Louvain (with a resolution scan restricted to 3-8 communities and a merge step for communities below 2% of nodes) finds **7 communities with modularity 0.716**, comfortably above the 0.3 acceptance level. The reproduced paper-style affinity (`S^gamma * K(d) * M`, gamma = 1, sigma = 1.5 km, magnitude factor from the flow volume) partitioned with K-Means++ at the same granularity reaches modularity 0.642.

The decisive operational difference is **net-flow balance**: the mean absolute within-community imbalance is 0.0225 for the flow-based partition against 0.0871 for the affinity partition, a 3.9x gap. That is expected by construction - the affinity encodes demand similarity and geography but ignores flow direction - and it is exactly the property a rebalancing plan needs.

**Answer (RQ3).** A flow-based community structure is a better basis for rebalancing than the
published distance + demand-correlation affinity: at the same number of groups it is more modular (0.716 vs 0.642) and about 3.9 times better net-flow balanced, while the affinity partition is statistically tied on silhouette (0.300 vs 0.298). The two methods answer different questions and should be used together: affinity for demand
profiling, flow for operations.

*Figures:* `fig_community_map.png`. Data: `community_comparison.csv`, `community_results.json`, `fence_communities.csv`.

---

## 5. RQ4 - Fence-level short-term demand prediction

Model: `HistGradientBoostingRegressor(max_iter=300, lr=0.06, leaves=31, min_samples_leaf=20, l2=1.0)`.
Target: n_pickup per fence x 30-minute cell. Panel: 1,738 cells over 405 fences.

Validation scheme: leave-one-day-out (5 folds, grouped by date; no random K-fold -> no temporal leakage). The 'validation' block below holds the pooled out-of-fold metrics.

| Split | n | MAE | RMSE | R2 | Pearson r |
|---|---|---|---|---|---|
| train | 1,400 | 2.484 | 3.604 | 0.784 | 0.887 |
| validation | 1,738 | 3.730 | 5.834 | 0.430 | 0.670 |
| test | 338 | 2.734 | 4.138 | 0.704 | 0.849 |

Per-fold results (leave-one-day-out):

| Held-out day | n train | n test | MAE | RMSE | R2 | r |
|---|---|---|---|---|---|---|
| 2020-12-21 | 1,311 | 427 | 4.388 | 6.375 | 0.228 | 0.499 |
| 2020-12-22 | 1,255 | 483 | 3.543 | 5.567 | 0.543 | 0.749 |
| 2020-12-23 | 1,654 | 84 | 6.229 | 9.456 | -1.470 | 0.814 |
| 2020-12-24 | 1,332 | 406 | 3.571 | 5.771 | 0.476 | 0.761 |
| 2020-12-25 | 1,400 | 338 | 2.734 | 4.138 | 0.704 | 0.849 |

Permutation importance on the held-out day (increase in MAE when the feature is shuffled):

| Feature | MAE increase | std |
|---|---|---|
| hist_mean | 3.118 | 0.129 |
| lag1_same_day | 0.973 | 0.048 |
| hour | 0.147 | 0.054 |
| halfhour | 0.137 | 0.048 |
| lag_prev_day | 0.090 | 0.045 |
| community | 0.052 | 0.025 |

**Answer (RQ4).** Backward-looking demand history dominates: the expanding mean of the same fence (`hist_mean`, +3.12 MAE when shuffled) and the previous slot of the same day (`lag1_same_day`, +0.97) carry most of the signal, followed by the time-of-day features and the community label; the weather controls contribute almost nothing, which is expected given five days and a single rainy day.

Under leave-one-day-out validation the model reaches MAE 3.730 and R2 0.430 (r = 0.670). The spread across folds is large (MAE 4.093 +- 1.330): the fold holding out the rainy day 2020-12-23 is the hardest (R2 -1.47), partly because that morning contributes only 84 qualifying fence-slot cells, so the model has few same-fence lags to lean on.

When the last day is held out as a genuine future test set the model reaches MAE 2.734, R2 0.704 and r = 0.849.

**Not comparable to the published BPNN.** That model reports a correlation of 0.979 with 152 training
and 8 test observations under a random split. Our sample is the entire filtered panel, our validation
is grouped by date, and eight randomly chosen test points are not a held-out morning. The defensible
statement is the one above: r = 0.849 on an unseen morning.

*Figures:* `fig_pred_scatter.png`, `fig_feature_importance.png`. Data: `model_metrics.json`.

---

## 6. Reproduction check (pipeline validation, not our finding)

The four statistics below belong to the published study. They are recomputed only to prove that the
parsing, pairing and time handling of this pipeline behave like the reference implementation.

| Check | Published | Recomputed | Tolerance | Verdict |
|---|---|---|---|---|
| trips with duration in [60, 3600] s | 212630 | 212680 | +-5% | PASS |
| cross-day demand curve Pearson r (mean over day pairs) | > 0.90 | 0.9873 | > 0.90 | PASS |
| morning peak hour | 08:00-09:00 | 08:00-09:00 | peak inside 08:00-09:00 | PASS |
| rainy day (2020-12-23) total-trip drop vs other days | about -60% | 61.86 | direction only (must fall) | PASS |

Daily totals in [60, 3600] s: {'2020-12-21': 49417, '2020-12-22': 52194, '2020-12-23': 18514, '2020-12-24': 47575, '2020-12-25': 44980}. Hourly totals: {'6': 17046, '7': 69224, '8': 95831, '9': 30579}.
Cross-day Pearson r: mean 0.9873, minimum 0.9627 over the ten day pairs.

All four checks pass, including the direction of the rainy-day drop (-61.9%, published 'about 60%').

---

## 7. Deviations from the specification (and why)

Everything below is a deliberate, documented departure from `Xiamen_Project_Agent_Spec.md`, either
because the data or the environment made the prescribed route impossible. No threshold was moved to
make a result look better: the two thresholds that differ from the letter of the specification (the
50 m snapping radius and the map's `total_flow >= 20` floor) are justified in place and accompanied by a
sensitivity table and an explicit statement on the figure.

| # | Specification said | What the data/environment required | Effect on results |
|---|---|---|---|
| D1 | The archive holds CSV files | Every `.csv` is an Office Open XML workbook; the reader sniffs the magic bytes and uses `openpyxl` | None on results; parsing is correct and validated by the 100% agreement with `ORDER DURATION` |
| D2 | `FENCE_LOC` may be a string or a sequence of corner points | It is a closed five-vertex ring in `[longitude, latitude]` order; `FENCE_ID` has 10,522 rows and 10,517 unique ids | A4: the paper's abstract (14,071) and its Methods (10,522) disagree; the observed value is 10,522 rows / 10,517 unique |
| D3 | Match endpoints with point-in-polygon only | The fences are parking bays (median area a few tens of m^2) and GPS noise is 10-30 m, so containment alone matches 1.90% of endpoints (A6) | Added a second tier that snaps to the nearest fence centroid within 50 m; match rate becomes 67.07%. Sensitivity 0/25/50/75/100/150/200 m is stored in `fence_match_rate.json` |
| D4 | `BICYCLE_ID` is a normal column | It is written only on the first row of each bicycle block (a merged cell) | Forward-filled inside each block before pairing; without this no trip can be built |
| D5 | Order table has `UPDATE_TIME` (date) and `TIME` (clock) | The table has `UPDATE_TIME1` (date) and `UPDATE_TIME2` (clock), plus a pre-computed `ORDER DURATION` | The A1 ladder resolves to `%Y-%m-%d %H:%M:%S` after joining the two columns; the pre-computed duration is used only as a parser cross-check |
| D6 | Weather has temperature, precipitation, wind, humidity, visibility | `PRCP` and the visibility column are 100% empty in the archive (A7) | The panel keeps the columns for schema compliance but they are NaN; they are dropped from the model features. Rain is identified from the trip-volume drop, not from the precipitation column |
| D7 | Trajectories can be grouped into trips | The files carry no trip id and cover only part of each bicycle's day; only 2.2% of OD trips have >= 5 fixes inside their time window (A8) | omega is estimated on continuous tracking episodes (29,668 rides) and cross-checked on the OD windows (2,456 rides); both estimates are reported and agree |
| D8 | `total_flow >= 10` per fence-slot cell | Kept exactly as prescribed, but 212,680 trips over 10,517 fences and 40 cells leave about one movement per cell (A12) | The panel retains 405 fences / 1,738 cells. This is reported as a data-sparsity finding, not worked around |
| D9 | M7 map over all city fences | The panel threshold would leave 405 fences on the map | The map aggregates all five mornings and uses `total_flow >= 20` for that aggregate only (2,905 fences); the evolution panels use `>= 5` per slot. Both floors are stated on the figure |
| D10 | `seaborn`, `statsmodels`, `networkx` optional | `seaborn` and `statsmodels` are not installable here; `networkx`, `scikit-learn` and `folium` had to go into a workspace-local `.pylibs` because site-packages is read-only (A10) | All plots use pure matplotlib; the HC1-robust OLS is implemented from scratch (`ols_hc1`); Louvain uses `networkx` when importable |
| D11 | Any environment | The execution sandbox forbids named pipes, which breaks joblib's threading backend inside scikit-learn's histogram binning | `LOKY_MAX_CPU_COUNT=1` is set before importing scikit-learn; the model fits sequentially and the metrics are unaffected |
| D12 | Paper-style affinity clustered with K-Means++ | Spectral clustering on the raw affinity collapsed into one giant component plus singletons (recorded in `community_results.json`) | The reported baseline is K-Means++ on the principal components of the row-normalised affinity; both variants are documented |
| D13 | Louvain communities | A raw Louvain scan produced a one-fence community | Communities below 2% of nodes (and below 10 nodes) are merged into their strongest neighbour before the partition is scored |
| D14 | omega grouped by functional zone | A ride usually starts mid-block, so exact containment tags only 1.6% of episodes | Episodes are tagged with the *nearest* fence (median distance 29.7 m, no radius cap, area label only); this is a descriptive tag, not a flow assignment |

No figure or helper failed in the last full run (the A11 exception path was never triggered).

---

## 8. Data-quality findings worth flagging

* **Fence table size.** 10,522 raw rows, 10,517 parsed and de-duplicated identifiers (5 duplicated ids in the file). The published paper quotes 14,071 in its abstract and 10,522 in its Methods; the archive supports the latter. This is a reproducibility problem in the published work that we found while checking the data (A4).
* **Endpoint match rate.** 67.07% with a 50 m snap; 140,087 endpoints remain unmatched and 52.0% of trips have at least one unmatched end (A6). The unmatched points are not GPS noise: their median distance to the nearest registered fence is 146 m (25th-75th percentile 85-248 m), far beyond the few metres of urban GPS error. They trace streets on which no bay is registered, so the fence registry covers only part of the operating area or riders park outside the bays (see `fig_unmatched_endpoints.png`).
* **Weather completeness.** Temperature, wind and humidity are complete; precipitation and visibility are entirely missing (A7). Any weather statement is limited to those three variables.
* **Trajectory coverage.** 2,849,243 fixes over 49,326 continuous segments; the files track about 14 k bicycles, far fewer than the 53 k bicycles in the order table, so the trajectory sample is not representative of all riders.

---

## 9. Limitations and required declarations

### 9.1 Weather is a control variable only; not causal

The archive covers five consecutive weekdays (2020-12-21 to 2020-12-25), exactly one of which is
rainy. Weather is therefore perfectly confounded with the day of week: no weather coefficient in this
project can be read as a causal effect, and the weather variables are described everywhere as
**control variables only; not causal**.

### 9.2 The reproduction checks are not our findings

The four statistics in section 6 (212,630 trips in [60, 3600] s, cross-day r > 0.90, the 08:00-09:00
peak, and the roughly 60% rainy-day drop) are published results. They are reproduced here as a
*reproduction check for pipeline validation, not our finding*.

### 9.3 Observational limits

Single city, five mornings, morning peak only (06:00-10:00), no rider identifiers, no counterfactual.
Every association reported here is descriptive; causal claims about weather, land use or policy are
outside what this dataset can support.

### 9.4 Methodological limits specific to this implementation

* The fence panel is sparse: the prescribed `total_flow >= 10` rule retains 1,738 cells over 405 fences, so RQ4 is estimated on a small but honest sample.
* omega is measured on tracking episodes, not on confirmed trips; the OD-window cross-check bounds the
  size of the resulting bias but cannot eliminate it.
* The 50 m snapping radius is a modelling choice. The sensitivity table shows that the match rate
  moves from 1.90% (containment only) to 88.72% (200 m), so any statement that depends on the exact
  rate should be read with that range in mind.
* Communities are descriptive flow groupings, not administrative districts, and their boundaries shift
  with the resolution parameter and with the minimum-flow floor.

---

## 10. Artifact index

### Code

| File | Purpose |
|---|---|
| `xm_core.py` | All modules M0-M11, written from scratch |
| `run_pipeline.py` | One-command entry point, `--skip-to M8` resume support, per-stage timing and memory |
| `dash_app.py` | Plotly-Dash dashboard on port 8050 |
| `Xiamen_SharedBicycle_Analysis.ipynb` | Narrative notebook, M1-M11 in order |
| `tools/make_notebook.py`, `tools/make_report.py` | Regenerate the notebook and this report from the artifacts |

### Data artifacts

| File | Contents |
|---|---|
| `data_inventory.md` | discovered files and their classification |
| `data_quality_report.md` | full M1 quality report |
| `od_trips.csv` | reconstructed trips with fence ids |
| `fence_match_rate.json` | match rates and snap-radius sensitivity |
| `panel_fence_halfhour.csv` | the fence x half-hour panel |
| `validation.json` | the four reproduction checks |
| `morans_i.json` | spatial autocorrelation of the tidal index |
| `fence_tidal_summary.csv` | fence-level morning aggregate |
| `fence_slot_summary.csv` | fence x slot aggregate |
| `tidal_top_fences.csv` | top-20 net sinks and sources |
| `omega_overall.json` | the headline omega estimate with bootstrap CIs |
| `omega_by_zone.csv` | omega by fence role, hour and community |
| `omega_group_tests.json` | Kruskal-Wallis, Welch ANOVA and HC1 OLS |
| `omega_trips.csv` | every ride used in the omega estimate |
| `community_comparison.csv` | our partition vs the paper-style baseline |
| `community_results.json` | community metadata, sizes and balance details |
| `fence_communities.csv` | fence-level community assignment |
| `model_metrics.json` | MAE / RMSE / R2 / r per split, per fold and per feature |
| `pipeline_log.json` | stage timings, memory, failures and skipped figures |

### Figures

| Figure | Contents |
|---|---|
| `fig_tidal_map.png` | city-wide fence-level tidal index |
| `fig_tidal_evolution.png` | eight 30-minute slots, 2x4 panels |
| `fig_tidal_box.png` | tidal-index distribution by slot |
| `fig_omega_dist.png` | omega distribution with the borrowed 1.529 reference |
| `fig_omega_by_zone.png` | omega by fence tidal role |
| `fig_omega_by_hour.png` | omega by hour |
| `fig_community_map.png` | Louvain vs paper-style communities |
| `fig_pred_scatter.png` | predicted vs actual pickups |
| `fig_feature_importance.png` | permutation importance |
| `fig_unmatched_endpoints.png` | spatial distribution of unmatched endpoints |
| `fig_validation.png` | daily volumes and cross-day demand curves |

### Interactive

* `folium_tidal_map.html` (3.58 MB, well under the 20 MB budget): leaflet map with one circle marker per fence, radius proportional to total flow, popup with role, pickups, returns, net flow and tidal index.
* `python dash_app.py` serves the dashboard on http://127.0.0.1:8050 with a 30-minute slot slider, a
  fence-role filter, the tidal map, the morning demand curve and a ranking of the strongest sources and
  sinks.

---

## 11. How to reproduce

```bash
# full run, M0 -> M11
python run_pipeline.py

# resume from a stage, reusing the artifacts already on disk
python run_pipeline.py --skip-to M8

# regenerate the notebook and this report
python tools/make_notebook.py
python tools/make_report.py

# dashboard
python dash_app.py
```

Dependencies are pinned in `requirements.txt` (`pip install -r requirements.txt`). The pipeline
writes every artifact into `figures/` and never touches anything outside the project directory;
`raw/` and the input archive are git-ignored, so a clean checkout plus the archive is enough.

