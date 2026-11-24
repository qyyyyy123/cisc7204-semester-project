# Reproducibility checklist

This file records the exact route a reader (or a marker) can take from a clean machine to the
numbers quoted in the README, and what to check at each step. It has been executed on Windows 11 with
Python 3.14.7.

## Level 1 — read the results without downloading anything (≈ 10 s)

Everything the notebook reads is committed under `figures/`. No network, no raw data.

```bash
pip install -r requirements.txt
jupyter lab Xiamen_SharedBicycle_Analysis.ipynb
```

Run all cells top to bottom. **Pass criteria:** no cell raises, 11 figures display, and the numbers
match [`EXPECTED_OUTPUTS.md`](EXPECTED_OUTPUTS.md). If you only want to read, open
[`rendered/Xiamen_SharedBicycle_Analysis.html`](rendered/Xiamen_SharedBicycle_Analysis.html) — it is
the same notebook already executed, with outputs embedded.

## Level 2 — rerun the pipeline from the raw archive (cold ≈ 15 min, warm ≈ 1 min)

```bash
# 1. obtain and verify the archive (see ../data/DOWNLOAD.md)
Get-FileHash -Algorithm SHA256 "Shared bicycle data.zip"
#   -> 662356AE3B1CCE76E51DC4128BCC18AC5FF3C77F46982036BAE34BDD2ACB6656

# 2. run every module
python run_pipeline.py

# 3. check the log
type figures\pipeline_log.json
```

**Pass criteria:**

| Check | Expected |
|---|---|
| Stage log | `[M0/11]` … `[M11/11]`, every stage `"status": "ok"` |
| Trips | 212,680 in [60, 3600] s |
| Fence table | 10,522 rows / 10,517 unique ids |
| Endpoint match | 67.07 % at a 50 m snap radius |
| Reproduction checks | 4 / 4 passed (`validation.json` → `"all_passed": true`) |
| Figures | 11 `fig_*.png` regenerated, none skipped |
| Total runtime | ≈ 1 min with a warm parse cache; a cold run re-parses 12 workbooks once |

Intermediate stages can be re-run on their own — the pipeline writes every artifact to disk and can
resume:

```bash
python run_pipeline.py --skip-to M8
```

## Level 3 — the interactive layer

```bash
# Leaflet map: open in any browser, no server needed
start figures\folium_tidal_map.html

# Dash dashboard on http://127.0.0.1:8050
python dash_app.py
```

**Pass criteria:** the map renders 2,905 coloured fence markers with popups showing role, pickups,
returns, net flow and tidal index; the dashboard's slot slider re-renders the map and the ranking
without an error in the console.

## Determinism and expected drift

`RANDOM_SEED = 42` is set globally; the bootstrap (1,000 replicates), the Moran's I permutation test
(999 permutations), Louvain and K-Means++ are all seeded. Two runs on the same machine reproduce the
same figures. Small drift is nevertheless expected *across* machines for three reasons, all stated
in the report's §7:

* floating-point summation order in `numpy` differs with SIMD width, which moves the last digit of
  the ratio-based quantities (ω, tidal index);
* Louvain's node visitation order depends on dictionary iteration order;
* `HistGradientBoostingRegressor` binning depends on thread count — the pipeline pins it to one
  worker (`LOKY_MAX_CPU_COUNT=1`) precisely to remove this source of variation.

Anything beyond the last decimal place means a *substantive* difference, not drift; check
`figures/data_quality_metrics.json` first, then the parse-format record in
`figures/data_quality_report.md`.

## What was actually tested here

| Claim | How it was verified |
|---|---|
| `pip install -r requirements.txt` is sufficient | Imported `xm_core` in a clean interpreter after installing only these packages; all 11 stages ran |
| The notebook runs from the committed artifacts | All 25 code cells executed in order with `raw/` absent: 25/25 passed. Two defects were found and fixed in `tools/make_notebook.py` on the way — the M2 cell referenced an undefined `od` (it reads `trips`), and the weather cell assumed `raw/` was present and now degrades to the committed panel |
| The extraction step works from the documented archive | Ran `run_pipeline.py` from a clean checkout holding only `Shared bicycle data.zip` |
| The dashboard starts | `python dash_app.py`, served on port 8050 |
| The map opens | Opened `figures/folium_tidal_map.html` (3.6 MB, under the 20 MB budget) |

The notebook run was executed by importing the cells one by one into a plain interpreter
rather than through Jupyter, because `jupyter_core` sets an ACL on its runtime directory
that this build environment refuses; the code path a reader would run is the same.
