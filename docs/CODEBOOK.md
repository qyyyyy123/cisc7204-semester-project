# Codebook - Xiamen shared-bicycle tidal imbalance project

This codebook explains (A) the raw variables, (B) the derived measures used in the analysis,
(C) the pipeline modules, and (D) every result artifact. It is meant to be read together with
`DATA_DESCRIPTION.md` and `figures/FINAL_REPORT.md`.

---

## A. Raw data dictionary

Source: `data/Shared bicycle data.zip` (Zenodo 10.5281/zenodo.17415891, CC BY-NC 4.0), extracted under
`raw/`. Time zone UTC+8, coordinates WGS-84, distance in metres, duration in seconds.

> **Important container quirk.** Although the files in `raw/` end in `.csv`, every one of them is
> actually an Office Open XML (xlsx) workbook. `xm_core.py` detects the container by magic bytes
> and reads it with `openpyxl`; do not force a plain-text CSV reader.

### A1. `raw/Order data/order data.csv` - 441,350 lock events

| Variable | Type / unit | Meaning |
|---|---|---|
| `BICYCLE_ID` | string | Bicycle identifier. Written only on the first row of each bicycle block; forward-filled before pairing. |
| `LATITUDE`, `LONGITUDE` | float, degrees | WGS-84 position of the lock event. |
| `LOCK_STATUS` | integer 0/1 | 0 = unlocked (pickup), 1 = locked (return). |
| `UPDATE_TIME1` | datetime | Date of the event (2020-12-21 ... 2020-12-25). |
| `UPDATE_TIME2` | time | Clock time of the event (06:00:00 - 09:59:59). |
| `ORDER DURATION` | seconds | Pre-computed trip duration; used only as a parser cross-check. |

### A2. `raw/Electronic fence data/STATION.csv` - 10,522 rows / 10,517 unique fences

| Variable | Type / unit | Meaning |
|---|---|---|
| `FENCE_ID` | string | Unique fence identifier (duplicated in five rows of the source file). |
| `FENCE_LOC` | 5-vertex ring | Closed polygon in `[longitude, latitude]` order describing the parking bay. |

### A3. `raw/Trajectory data/Trajectory data_YYYYMMDD.csv` - 2,849,243 GPS fixes in five files

| Variable | Type / unit | Meaning |
|---|---|---|
| `BICYCLE_ID` | string | Bicycle identifier; the files contain no trip id. |
| `LOCATING_TIME` | time | Clock time; the date comes from the file name. |
| `LATITUDE`, `LONGITUDE` | float, degrees | WGS-84 GPS position. |

### A4. `raw/Weather data/Weather data.csv` - 144 hourly rows

| Variable | Unit | Meaning |
|---|---|---|
| `TIME` | datetime | Observation hour |
| `TEMP`, `DWPT` | °C | Air temperature, dew point |
| `RHUM` | % | Relative humidity |
| `PRCP`, `SNOW` | mm | Precipitation, snowfall (**entirely empty in this archive**) |
| `WDIR` | degrees | Wind direction |
| `WSPD`, `WPGT` | m/s | Wind speed, gust |
| `PRES` | hPa | Pressure |
| `TSUN` | h | Sunshine duration |
| `COCO` | code | Weather condition code (visibility column is empty) |

Weather is used as a **control variable only; not causal**: five weekdays with a single rainy day
confound weather with day of week.

### A5. Non-pipeline files in `raw/Algorithm data/`

MATLAB scripts (`BP.m`, `Clustering.m`, `Clustring_2.m`, `zdbh.m`, `Forecasting.m`, `Topsis.m`,
`bp_yuce.m`, `lstm_yuce.m`), `.fig` figures and small `statistical data.xlsx` / `data_dd.xlsx` /
`data_stations.txt` exports are model artefacts of the published study, not raw inputs. They are
retained for provenance and excluded from the pipeline (classification role `other` in
`figures/data_inventory.md`). The two Python scripts under `raw/Data cleaning algorithm/` are the
original authors' cleaning code. `raw/README.md` is the data dictionary shipped with the dataset.

---

## B. Derived measures and their definitions

| Measure | Definition | Where computed |
|---|---|---|
| Trip (OD) | Consecutive `LOCK_STATUS` 0 -> 1 events per bicycle, sorted in time, duration in [60, 3600] s. 212,680 trips. | M2 `build_od` |
| Endpoint-to-fence assignment | Point-in-polygon first; if no containment, snap to the nearest fence centroid within 50 m. Rate 67.07%; a 0-200 m sensitivity table is stored. | M3 `FenceIndex`, `assign_points_to_fence` |
| Tidal index | `(pickups - returns) / (pickups + returns)` in [-1, +1]. +1 = pure morning source (bikes leave), -1 = pure sink (bikes arrive). | M5 `panel_fence_halfhour`, `tidal_table` |
| Fence role | `source` if tidal index > +0.2, `sink` if < -0.2, otherwise `balanced`. Map uses five-morning aggregates with `total_flow >= 20`. | M7 `fence_summary` |
| Moran's I | Spatial autocorrelation of the fence tidal index with a 500 m binary distance band, 999 permutations. | M7 `morans_i` |
| Detour factor omega | GPS path length / straight-line distance between first and last fix of a continuous tracking episode (max gap 600 s); filters: >= 5 fixes, chord >= 200 m, omega in [1, 4]. 29,668 valid rides. | M8 `compute_omega` |
| HC1 robust OLS | Heteroskedasticity-consistent least squares implemented from scratch (no `statsmodels`), with zone and hour dummies. | M8 `ols_hc1` |
| Community | Louvain partition of the symmetrised OD graph `W = OD + OD.T`, fences with < 20 movements dropped; communities below 2% of nodes merged into the strongest neighbour. | M9 `louvain_partition` |
| Affinity baseline | Reproduced paper-style `S^gamma * K(d) * M` affinity, row-normalised and clustered with K-Means++ to seven groups. | M9 `paper_style_partition` |
| Demand model | `HistGradientBoostingRegressor(max_iter=300, lr=0.06, leaves=31, min_samples_leaf=20, l2=1.0)`; target = pickups per fence x 30-minute cell; leave-one-day-out validation. | M10 `run_prediction` |
| Feature importance | Increase in MAE when a feature is shuffled on the held-out day. | M10 `run_prediction` |

Thresholds and deliberate deviations from the specification (D1-D14) are documented in section 7
of `figures/FINAL_REPORT.md`.

---

## C. Module map (pipeline stages)

| Stage | Function(s) in `xm_core.py` | Main output |
|---|---|---|
| M0 | `find_dataset_zip`, `extract_dataset`, `discover_data_files`, `classify_table` | `figures/data_inventory.md` |
| M1 | `write_quality_report` | `figures/data_quality_report.md`, `data_quality_metrics.json` |
| M2 | `build_od`, `write_od` | `figures/od_trips.csv` |
| M3 | `FenceIndex`, `assign_points_to_fence` | fence ids in the trip table, `fence_match_rate.json` |
| M4 | `prep_weather`, `link_weather` | weather columns in the panel |
| M5 | `panel_fence_halfhour`, `tidal_table` | `figures/panel_fence_halfhour.csv`, `fence_slot_summary.csv` |
| M6 | `validate_pipeline` | `figures/validation.json` |
| M7 | `fence_summary`, `fence_slot_summary`, `morans_i` | `fence_tidal_summary.csv`, `tidal_top_fences.csv`, `morans_i.json`, tidal maps |
| M8 | `compute_omega`, `ols_hc1`, `omega_group_tests` | `omega_overall.json`, `omega_by_zone.csv`, `omega_group_tests.json`, `omega_trips.csv` |
| M9 | `build_fence_network`, `louvain_partition`, `paper_style_partition` | `community_comparison.csv`, `community_results.json`, `fence_communities.csv`, community map |
| M10 | `build_prediction_frame`, `run_prediction` | `model_metrics.json`, prediction and importance figures |
| M11 | `make_folium_map`, `dash_app.py` | `folium_tidal_map.html`, Plotly-Dash dashboard |
| Live | `dashboard/` | Kafka replay -> Spark aggregation -> MySQL -> FastAPI/WebSocket -> Leaflet + ECharts screen |

Run `python run_pipeline.py` for all stages, or `python run_pipeline.py --skip-to M8` to resume
from a stage using the artifacts already on disk.

---

## D. Artifact index (`figures/`)

**Reports / inventory:** `FINAL_REPORT.md` (full report), `data_inventory.md`,
`data_quality_report.md`, `pipeline_log.json`.

**Derived tables:** `od_trips.csv` (reconstructed trips with fence ids),
`panel_fence_halfhour.csv` (fence x half-hour x day panel), `dashboard_fences.csv`,
`fence_tidal_summary.csv`, `fence_slot_summary.csv`, `fence_communities.csv`,
`fence_match_rate.json`,
`omega_by_zone.csv`, `omega_trips.csv`, `community_comparison.csv`,
`community_results.json`, `tidal_top_fences.csv`, `dashboard_fences.csv`.

**Statistics:** `data_quality_metrics.json`, `validation.json`, `morans_i.json`,
`omega_overall.json`, `omega_group_tests.json`, `model_metrics.json`.

**Figures (PNG):** `fig_tidal_map.png`, `fig_tidal_evolution.png`, `fig_tidal_box.png`,
`fig_omega_dist.png`, `fig_omega_by_zone.png`, `fig_omega_by_hour.png`,
`fig_community_map.png`, `fig_pred_scatter.png`, `fig_feature_importance.png`,
`fig_unmatched_endpoints.png`, `fig_validation.png`.

**Interactive:** `folium_tidal_map.html` (one circle marker per fence with popup),
`dash_app.py` (port 8050), `dashboard/` (live streaming screen, port 8000).

For exact file names and sizes, see `DATA_DESCRIPTION.md`.
