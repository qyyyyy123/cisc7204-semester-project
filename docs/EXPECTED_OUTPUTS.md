# Expected outputs

Numbers a correct run must reproduce. They are read from the committed artifacts, so this file is
also the contract the README is checked against.

## Data volume and quality — `figures/data_quality_metrics.json`

| Quantity | Expected |
|---|---|
| Order events | 441,350 |
| Fence rows / unique ids | 10,522 / 10,517 |
| GPS fixes | 2,849,243 |
| Weather rows (hourly) | 144 |
| Trips in [60, 3600] s | 212,680 |
| Endpoint match rate (50 m snap) | 67.07 % |
| Endpoint match rate (containment only) | 1.90 % |
| Endpoint match rate (200 m snap) | 88.72 % |
| Unmatched endpoints | 140,087 |

## Reproduction checks — `figures/validation.json` (pipeline validation, **not our finding**)

| Check | Published | Recomputed | Verdict |
|---|---|---|---|
| Trips in [60, 3600] s | 212,630 | 212,680 (+0.02 %) | PASS |
| Cross-day demand Pearson r | > 0.90 | 0.9873 (min 0.9627) | PASS |
| Morning peak hour | 08:00–09:00 | 08:00–09:00 (45.1 % of trips) | PASS |
| Rainy-day drop (2020-12-23) | ≈ −60 % | −61.86 % | PASS |

## RQ1 — tidal structure

| Quantity | Expected | Source |
|---|---|---|
| Net-source fences (index > +0.2) | 1,075 | `fence_tidal_summary.csv` |
| Net-sink fences (index < −0.2) | 1,049 | `fence_tidal_summary.csv` |
| Fences on the city map (flow ≥ 20) | 2,905 | `morans_i.json` |
| Moran's I (500 m band) | 0.1229 | `morans_i.json` |
| z-score / permutation p | 37.7 / 0.001 | `morans_i.json` |
| Neighbour pairs | 89,614 | `morans_i.json` |
| Slot with peak net-sink share | 08:00–08:30 (39.1 %) | `FINAL_REPORT.md` §2.3 |

## RQ2 — detour factor ω (core contribution)

| Quantity | Expected | Source |
|---|---|---|
| Valid ω values | 29,668 (from 49,326 episodes) | `omega_overall.json` |
| Mean ω | 1.4934 | `omega_overall.json` |
| 95 % bootstrap CI of the mean | [1.4868, 1.5000] | `omega_overall.json` |
| Median ω | 1.2994 | `omega_overall.json` |
| 95th percentile | 2.816 | `omega_overall.json` |
| Share of rides below 1.529 | 73.46 % | `omega_overall.json` |
| Zone test (Kruskal–Wallis) | p = 1.47e-12 | `omega_group_tests.json` |
| Hour test (Kruskal–Wallis) | p = 4.09e-04 | `omega_group_tests.json` |
| OD-window cross-check | n = 2,456, mean 1.377 | `omega_overall.json` |

## RQ3 — communities

| Method | Communities | Modularity | Silhouette | Mean abs. net-flow imbalance |
|---|---|---|---|---|
| Louvain on symmetrised OD graph | 7 | 0.7160 | 0.2977 | 0.0225 |
| Paper-style affinity + K-Means++ | 7 | 0.6419 | 0.3001 | 0.0871 |

Source: `community_comparison.csv`. Graph: 2,761 fence nodes, 37,488 undirected edges.

## RQ4 — prediction

| Split | n | MAE | RMSE | R² | Pearson r |
|---|---|---|---|---|---|
| train | 1,400 | 2.484 | 3.604 | 0.784 | 0.887 |
| validation (5 folds, pooled out-of-fold) | 1,738 | **3.730** | 5.834 | **0.430** | **0.670** |
| test (held-out future day 2020-12-25) | 338 | 2.734 | 4.138 | 0.704 | 0.849 |

Source: `model_metrics.json`. Fold MAE spread: 4.093 ± 1.330.

> **Why the figure says MAE 3.970 and this table says 3.730.** They are different populations, not a
> contradiction: `model_metrics.json` pools **all five** leave-one-day-out folds (n = 1,738), while the
> left panel of `fig_pred_scatter.png` shows the four folds that exclude the held-out future test day
> (n = 1,400). The figure's title states the training-day population; this table states the pooled
> five-fold population. Both are reported so that neither number can be quoted out of context.

Top permutation importances (increase in MAE when the feature is shuffled): `hist_mean` 3.118,
`lag1_same_day` 0.973, `hour` 0.147, `halfhour` 0.137, `lag_prev_day` 0.090, `community` 0.052.

## Figures that must exist

`fig_tidal_map.png`, `fig_tidal_evolution.png`, `fig_tidal_box.png`, `fig_omega_dist.png`,
`fig_omega_by_zone.png`, `fig_omega_by_hour.png`, `fig_community_map.png`, `fig_pred_scatter.png`,
`fig_feature_importance.png`, `fig_unmatched_endpoints.png`, `fig_validation.png` — 11 in total,
all dpi ≥ 150, all titles and axis labels in English with units.
