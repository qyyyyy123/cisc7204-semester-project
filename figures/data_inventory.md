# Data inventory

Automatic discovery and classification of every tabular file found under `raw/`.
Classification uses column-name heuristics (specification §2) cross-checked against
the repository `README.md` data dictionary shipped inside the archive.

| # | File | Container | Rows | Cols | Role | Basis |
|---|---|---|---|---|---|---|
| 1 | `raw/Algorithm data/BP prediction/statistical data.xlsx` | xlsx | - | 9 | other | no signature matched: 1, 2, 21, 22, 23, 24, 25, 3, 4 |
| 2 | `raw/Algorithm data/Clustering/data_Demand forecasting.txt` | csv | - | 1 | other | no signature matched: 34 |
| 3 | `raw/Algorithm data/Clustering/data_dd.xlsx` | xlsx | - | 2 | other | no signature matched: 118.1404, 24.52116 |
| 4 | `raw/Algorithm data/Clustering/data_stations.txt` | csv | - | 1 | other | no signature matched: 0	118.1033	24.4729	0	0	230	0 |
| 5 | `raw/Algorithm data/other prediction model/test.xls` |  | - | -1 | other | unreadable: File is not a zip file |
| 6 | `raw/Electronic fence data/STATION.csv` | xlsx | 10,522 | 2 | fence | FENCE_ID + FENCE_LOC / corner coordinates |
| 7 | `raw/Order data/order data.csv` | xlsx | 441,350 | 7 | order | LOCK_STATUS + BICYCLE_ID + coordinates + UPDATE_TIME* |
| 8 | `raw/Trajectory data/Trajectory data_20201221.csv` | xlsx | 542,386 | 4 | trajectory | BICYCLE_ID + timestamp + coordinates, no LOCK_STATUS |
| 9 | `raw/Trajectory data/Trajectory data_20201222.csv` | xlsx | 586,562 | 4 | trajectory | BICYCLE_ID + timestamp + coordinates, no LOCK_STATUS |
| 10 | `raw/Trajectory data/Trajectory data_20201223.csv` | xlsx | 213,733 | 4 | trajectory | BICYCLE_ID + timestamp + coordinates, no LOCK_STATUS |
| 11 | `raw/Trajectory data/Trajectory data_20201224.csv` | xlsx | 932,469 | 4 | trajectory | BICYCLE_ID + timestamp + coordinates, no LOCK_STATUS |
| 12 | `raw/Trajectory data/Trajectory data_20201225.csv` | xlsx | 574,093 | 4 | trajectory | BICYCLE_ID + timestamp + coordinates, no LOCK_STATUS |
| 13 | `raw/Weather data/Weather data.csv` | csv | 144 | 12 | weather | meteorological fields (TEMP/PRCP/WSPD/RHUM/...) |

## Column names per file

### `raw/Algorithm data/BP prediction/statistical data.xlsx` (other)

`1`, `2`, `3`, `4`, `21`, `22`, `24`, `25`, `23`

### `raw/Algorithm data/Clustering/data_Demand forecasting.txt` (other)

`34`

### `raw/Algorithm data/Clustering/data_dd.xlsx` (other)

`24.52116`, `118.1404`

### `raw/Algorithm data/Clustering/data_stations.txt` (other)

`0	118.1033	24.4729	0	0	230	0`

### `raw/Algorithm data/other prediction model/test.xls` (other)

``

### `raw/Electronic fence data/STATION.csv` (fence)

`FENCE_ID`, `FENCE_LOC`

### `raw/Order data/order data.csv` (order)

`BICYCLE_ID`, `LATITUDE`, `LONGITUDE`, `LOCK_STATUS`, `UPDATE_TIME1`, `UPDATE_TIME2`, `ORDER DURATION`

### `raw/Trajectory data/Trajectory data_20201221.csv` (trajectory)

`BICYCLE_ID`, `LOCATING_TIME`, `LATITUDE`, `LONGITUDE`

### `raw/Trajectory data/Trajectory data_20201222.csv` (trajectory)

`BICYCLE_ID`, `LOCATING_TIME`, `LATITUDE`, `LONGITUDE`

### `raw/Trajectory data/Trajectory data_20201223.csv` (trajectory)

`BICYCLE_ID`, `LOCATING_TIME`, `LATITUDE`, `LONGITUDE`

### `raw/Trajectory data/Trajectory data_20201224.csv` (trajectory)

`BICYCLE_ID`, `LOCATING_TIME`, `LATITUDE`, `LONGITUDE`

### `raw/Trajectory data/Trajectory data_20201225.csv` (trajectory)

`BICYCLE_ID`, `LOCATING_TIME`, `LATITUDE`, `LONGITUDE`

### `raw/Weather data/Weather data.csv` (weather)

`TIME`, `TEMP`, `DWPT`, `RHUM`, `PRCP`, `SNOW`, `WDIR`, `WSPD`, `WPGT`, `PRES`, `TSUN`, `COCO`

## Notes

* Every file with a `.csv` extension in this archive is in fact an Office Open XML
  workbook (its first bytes are `PK\x03\x04`). The reader therefore sniffs the
  container instead of trusting the extension.
* The archive also ships MATLAB scripts (`*.m`) and an `.xlsx` cluster export under
  `Algorithm data/`; those are model artefacts of the published study, not raw data,
  so they are excluded from the pipeline inputs (role `other`).
