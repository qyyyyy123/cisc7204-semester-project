# How to obtain the input data

The analysis reads one third-party archive. It is **not** committed to this repository: it is 96 MB
and it is licensed **CC BY-NC 4.0**, so re-hosting it here would be both unnecessary and a licence
grey area.

## 1. Download

| Item | Value |
|---|---|
| Title | *Shared bicycle data* — Xiamen shared-bicycle order, fence, trajectory and weather records |
| Publisher | Zenodo |
| DOI | **10.5281/zenodo.17415891** |
| Landing page | <https://doi.org/10.5281/zenodo.17415891> |
| Licence | CC BY-NC 4.0 — attribution required, commercial use not permitted |
| File used here | `Shared bicycle data.zip` |
| Size | 100,992,679 bytes (96.3 MiB) |
| SHA-256 of the copy analysed | `662356ae3b1cce76e51dc4128bcc18ac5ff3c77f46982036bae34bdd2acb6656` |

Open the DOI, download the archive from the Zenodo record's *Files* panel, and keep the file name
unchanged.

## 2. Verify the download

```bash
# PowerShell
Get-FileHash -Algorithm SHA256 "Shared bicycle data.zip"

# macOS / Linux
sha256sum "Shared bicycle data.zip"
```

The hash must equal the value in the table above. A different hash means a different release of the
record was downloaded; the pipeline will still run, but the numbers in
[`EXPECTED_OUTPUTS.md`](../docs/EXPECTED_OUTPUTS.md) may drift.

## 3. Place it and extract

Put the archive in the **repository root**, next to `run_pipeline.py`:

```
cisc7204-semester-project/
├── Shared bicycle data.zip     <- here
├── run_pipeline.py
└── ...
```

`run_pipeline.py` locates the archive by name (case-insensitive, scanning two directory levels),
extracts it into `raw/` preserving the archive's own folder structure, and classifies each table by
column-name heuristics, cross-checked against the archive's `README.md`
(mirrored here as [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md)).

`raw/` is git-ignored. Nothing else needs to be downloaded.

## 4. What is inside the archive

| Folder | Contents | Rows used |
|---|---|---|
| `Order data/` | One row per lock event: `BICYCLE_ID`, coordinates, `LOCK_STATUS` (0 = unlocked, 1 = locked), `UPDATE_TIME1` (date), `UPDATE_TIME2` (clock) | 441,350 |
| `Electronic fence data/` | `FENCE_ID`, `FENCE_NAME`, `FENCE_LOC` (closed five-vertex ring) | 10,522 rows / 10,517 unique ids |
| `Trajectory data/` | Five daily files of GPS fixes per bicycle, 30 s – 2 min sampling | 2,849,243 |
| `Weather data/` | Hourly `TEMP`, `RHUM`, `WSPD`, `PRCP`, … | 144 rows |
| `Algorithm data/`, `Data cleaning algorithm/` | The published study's MATLAB/Python model artefacts — *not* raw data, excluded from the pipeline | — |

Two quirks that the loader handles explicitly and that are worth knowing before you open the files:

* **Every `.csv` in the archive is actually an Office Open XML workbook** (the byte stream starts
  with `PK\x03\x04`). `openpyxl` reads them; a plain `pd.read_csv` will fail.
* **`BICYCLE_ID` is written only on the first row of each bicycle block** (a merged cell in the
  original spreadsheet). It is forward-filled inside each block before trips can be paired.
