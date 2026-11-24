# Live tidal dashboard (Kafka -> Spark -> MySQL -> FastAPI -> Leaflet/ECharts)

This component replays the real historical orders in `../raw/Order data/order data.csv` as a Kafka
stream and visualises the resulting fence-level net flow on a Leaflet map with an ECharts time
series and a weather panel. The data are never invented: the "live" stream is a controlled replay
of the five mornings in the data set. The detailed original notes (architecture diagram, full API
list and troubleshooting) are in `README_full_notes.md`.

## What changed when the two project folders were merged

* The duplicate `Shared bicycle data/` copy that originally lived beside this dashboard was
  removed from the submission. `producer/config.py`, `spark/streaming_consumer.py` and
  `api/main.py` now read the single shared data tree at `../raw/` (relative to this folder).
* No algorithm or visual behaviour was changed; only the data root was redirected.

## Requirements

* Docker Desktop with WSL2 (Zookeeper, Kafka, MySQL 8, Redis)
* Java 17 and Spark 3.5.9 pre-built for Hadoop 3; WinUtils on Windows
* Python 3.10 environment:

```bash
conda create -n bike_env python=3.10
conda activate bike_env
python -m pip install pyspark==3.5.9 pandas kafka-python redis findspark shapely \
    openpyxl mysql-connector-python chardet fastapi uvicorn websockets
```

## Test run (four terminals)

Before the first run, edit the machine-specific `JAVA_HOME`, `SPARK_HOME`, `HADOOP_HOME` and
`PY` variables in `scripts/run_consumer.sh`, `scripts/run_producer.sh` and `scripts/run_api.sh`
to point at the local installations.

Then create the local credentials file (once):

```bash
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and choose the two passwords for the throwaway MySQL container
```

`docker-compose.yml`, `api/main.py` and `spark/streaming_consumer.py` all read the same `.env`.
No password is stored in the repository, and `docker compose` refuses to start if `.env` is
missing rather than silently falling back to a default.

```bash
# 1. Infrastructure (MySQL schema is applied from mysql/init/01_schema.sql on first start)
docker compose up -d
docker ps

# 2. Spark Structured Streaming consumer - start first
bash scripts/run_consumer.sh              # --reset recomputes from the beginning

# 3. Kafka producer - replay historical orders
bash scripts/run_producer.sh --date 2020-12-22
# bash scripts/run_producer.sh             # all five days
# bash scripts/run_producer.sh --dry-run   # validate without sending

# 4. FastAPI backend serving the frontend
bash scripts/run_api.sh
```

Open **http://127.0.0.1:8000** and press play. Main endpoints: `/api/fences`, `/api/windows`,
`/api/agg?window=...`, `/api/agg/latest`, `/api/series`, `/api/weather?window=`, `/api/state`,
`/api/health`, and the WebSocket `ws://localhost:8000/ws/tidal`.

If `bike_db.bike_agg_results` already contains data from a previous run, terminals 1 and 4 alone
are enough:

```bash
docker compose -f docker-compose.yml up -d
bash scripts/run_api.sh
```

Front-end libraries (Leaflet, ECharts) are vendored under `frontend/vendor/`; map tiles require
internet access. See `README_full_notes.md` for the complete Chinese-language reference.
