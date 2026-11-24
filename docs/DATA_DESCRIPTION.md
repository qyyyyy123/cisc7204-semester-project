# Data sets included in this submission - short description

All data come from the Zenodo record **10.5281/zenodo.17415891** (CC BY-NC 4.0), covering five
consecutive mornings in Xiamen, 2020-12-21 to 2020-12-25, 06:00-10:00 local time (UTC+8).
The archive ships two copies of the same raw data for different purposes; neither is a substitute
for the other: `data/Shared bicycle data.zip` is the untouched original, and `raw/` is the extracted
tree that the code reads directly.

## 1. Original data archive

| File | Size | Description |
|---|---|---|
| `data/Shared bicycle data.zip` | ~101 MB | The complete original Zenodo data set: orders, electronic fences, five daily GPS trajectory files, hourly weather, plus the published study's MATLAB clustering/prediction scripts and exports. `run_pipeline.py` can re-extract it into `raw/` at any time. |

## 2. Extracted full data sets (`raw/`)

| Path | Rows | Description |
|---|---|---|
| `raw/README.md` | - | Data dictionary shipped with the data set (units, variable definitions, folder conventions). |
| `raw/Order data/order data.csv` | 441,350 | One row per lock/unlock event: bicycle id, position, `LOCK_STATUS` (0 = pickup, 1 = return), date, clock time and pre-computed order duration. Despite the `.csv` suffix the file is an xlsx workbook. |
| `raw/Electronic fence data/STATION.csv` | 10,522 rows / 10,517 unique | Electronic fence polygons: `FENCE_ID` plus a closed five-vertex ring in `[longitude, latitude]` order. |
| `raw/Trajectory data/Trajectory data_20201221.csv` ... `_20201225.csv` | 542,386 / 586,562 / 213,733 / 932,469 / 574,093 | GPS fixes per bicycle and timestamp for each of the five days; no trip identifier. Used to measure the detour factor omega. |
| `raw/Weather data/Weather data.csv` | 144 | Hourly temperature, dew point, humidity, precipitation, snow, wind, pressure, sunshine and condition code. Precipitation and visibility columns are empty in this release. |
| `raw/Algorithm data/` | - | MATLAB scripts, `.fig` figures and small exports from the published study; provenance only, not pipeline inputs. |
| `raw/Data cleaning algorithm/` | 2 scripts | The original authors' order-id sorting and order-data cleaning scripts. |

## 3. Derived data products (`figures/`)

These are not additional raw sources; they are the reproducible outputs of the pipeline and are
included so the notebook and dashboards can run without re-parsing the ~2.8 million GPS fixes.

| Group | Files | Description |
|---|---|---|
| Inventory & quality | `data_inventory.md`, `data_quality_report.md`, `data_quality_metrics.json`, `pipeline_log.json` | Discovered tables and their classification, missing-value and consistency checks, stage timings. |
| Trips and panel | `od_trips.csv` (31 MB), `panel_fence_halfhour.csv`, `dashboard_fences.csv`, `fence_match_rate.json` | 212,680 reconstructed trips with fence endpoints; the fence x half-hour x day panel used by RQ1/RQ4; matching rates and snap-radius sensitivity. |
| Tidal structure (RQ1) | `fence_tidal_summary.csv`, `fence_slot_summary.csv`, `tidal_top_fences.csv`, `morans_i.json` | Five-morning fence aggregates, per-slot aggregates, top sinks/sources, spatial autocorrelation. |
| Detour factor (RQ2) | `omega_overall.json`, `omega_by_zone.csv`, `omega_trips.csv` (7 MB), `omega_group_tests.json` | Headline estimate with bootstrap CIs, group summaries, ride-level values, Kruskal-Wallis / Welch / HC1 OLS tests. |
| Communities (RQ3) | `community_comparison.csv`, `community_results.json`, `fence_communities.csv` | Louvain vs affinity baseline metrics and fence-level assignments. |
| Prediction (RQ4) | `model_metrics.json` | MAE/RMSE/R2/r per split and per fold, plus permutation importance. |
| Validation | `validation.json` | Four published-statistic reproduction checks (all PASS). |
| Figures | `fig_*.png` (11 files) | Maps, distributions, small multiples, community comparison, prediction diagnostics and validation curves. |
| Interactive | `folium_tidal_map.html` (3.6 MB), `FINAL_REPORT.md`, `data_inventory.md` and the reports above | Interactive fence map and the written interpretation of all results. |

Field-by-field definitions for both raw and derived data are in `CODEBOOK.md`; a per-figure
interpretation and the five-question visual-design review are in `Xiamen_SharedBicycle_Analysis.ipynb`.
