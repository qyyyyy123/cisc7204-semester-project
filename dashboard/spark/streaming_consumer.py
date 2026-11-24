# -*- coding: utf-8 -*-
"""
spark/streaming_consumer.py — Spark Structured Streaming 消费端

职责：
    1. 从 Kafka topic bike_events 读取实时事件流（借车 0 / 还车 1）
    2. 加载电子围栏 STATION.csv（10522 个多边形）做点-面匹配
    3. 30 分钟滚动窗口 + 30 秒微批聚合：borrow_count / return_count / net_flow
    4. 按小时对齐天气数据（温度 / 降水 / 能见度 / 天气类型）
    5. 幂等 UPSERT 写入 MySQL bike_agg_results，并汇总 bike_weather_effect

实现说明：
    围栏匹配用「Spark 按 5 位小数网格聚合 → driver 端网格→围栏查表」两级结构：
    真实 venues 坐标只落在围栏内的比例约 2%（围栏是 10m 级小矩形），
    因此匹配策略为：点在多边形内优先，否则取 300m 内最近围栏。
    网格查表结果会 memo 缓存，整轮 44 万事件仅付出几秒的匹配开销。
"""

import os
import re
import sys
import io
import time
import argparse

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import findspark

findspark.init()

from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType, TimestampType
from pyspark.sql.functions import col, from_json, window, sum as _sum, avg as _avg, count, when, lit
import mysql.connector


# ============================================================
# 配置
# ============================================================
KAFKA_BROKER = "localhost:9092"
KAFKA_TOPIC = "bike_events"

def _load_dotenv(path):
    """Load KEY=VALUE lines from dashboard/.env if present (no extra dependency)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or str(value).strip() == "":
        raise SystemExit(
            f"{name} is not set. Copy dashboard/.env.example to dashboard/.env and fill it in."
        )
    return value


MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "bike")
MYSQL_PWD = _env("MYSQL_PASSWORD")
MYSQL_DB = os.environ.get("MYSQL_DATABASE", "bike_db")
MYSQL_TABLE_AGG = "bike_agg_results"
MYSQL_TABLE_WEATHER = "bike_weather_effect"
MYSQL_TABLE_STATE = "bike_stream_state"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The merged archive keeps a single copy of the data in ../raw (see README.md).
DATA_ROOT = os.path.normpath(os.path.join(BASE_DIR, "..", "raw"))
FENCE_FILE = os.path.join(DATA_ROOT, "Electronic fence data", "STATION.csv")
WEATHER_FILE = os.path.join(DATA_ROOT, "Weather data", "Weather data.csv")

CHECKPOINT_DIR = os.path.join(BASE_DIR, "checkpoint", "agg")

WINDOW_DURATION = "30 minutes"
# 历史回放场景下不能用小水位线：Kafka 多分区并行推进会让某个分区瞬间读到最后一整天的数据，
# 水位线随之跃迁到 12-25 09:49，其余分区里 12-21 的事件会被判定为"迟到"而丢弃（实测丢失约 5 万条）。
# 这里把延迟放大到远大于数据跨度，等价于关闭迟到丢弃，同时保留窗口语义。
WATERMARK_DELAY = "365 days"
TRIGGER_INTERVAL = "30 seconds"
MAX_OFFSETS_PER_TRIGGER = "60000"   # 批均匀推进，避免单分区读爆
GRID_PRECISION = 5                  # 网格精度（5 位小数 ≈ 1.1m）
# 围栏是 10m 级小矩形，落到围栏内的点只有 2%，绝大多数靠"最近围栏"兜底：
# 300m 覆盖约 94%，500m 覆盖约 98.5%（实测），这里取 500m 并如实统计未命中事件数。
MAX_MATCH_DIST_M = 500.0
MAX_SEARCH_RINGS = 13               # 13 × 50m ≈ 650m 搜索半径
WEATHER_REFRESH_BATCHES = 2

# COCO 天气代码 → 中文 + 估算能见度(km)
COCO_MAP = {
    1: ("晴", 15.0), 2: ("少云", 12.0), 3: ("多云", 10.0), 4: ("阴", 8.0),
    5: ("雾", 1.0), 6: ("冻雾", 0.8), 7: ("小雨", 4.0), 8: ("中雨", 3.0),
    9: ("大雨", 2.0), 10: ("冻雨", 2.0), 11: ("强冻雨", 1.5), 12: ("雨夹雪", 3.0),
    13: ("强雨夹雪", 2.0), 14: ("小雪", 3.0), 15: ("中雪", 2.0), 16: ("大雪", 1.0),
    17: ("阵雨", 3.5), 18: ("强阵雨", 2.5), 19: ("雷阵雨", 3.0), 20: ("强雷阵雨", 2.0),
}

M_PER_DEG_LAT = 111000.0
M_PER_DEG_LNG = 102000.0     # 厦门 ≈ 24.5°N


def log(msg):
    print(msg, flush=True)


# ============================================================
# 1. 电子围栏：多边形 + 空间网格索引
# ============================================================
class FenceIndex:
    """围栏多边形索引：contains 优先，其次最近围栏（限距）"""

    CELL = 0.0005      # 网格边长 ≈ 50m

    def __init__(self, fence_file):
        log(f"[FILE] 加载围栏: {fence_file}")
        df = pd.read_excel(fence_file, engine="openpyxl")
        log(f"[OK  ] 围栏 {len(df):,} 行, 列={list(df.columns)}")

        from shapely.geometry import Polygon
        num_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

        self.ids = []
        self.polys = []
        self.cent = []
        for fid, loc in zip(df["FENCE_ID"], df["FENCE_LOC"]):
            vals = [float(x) for x in num_re.findall(str(loc))]
            pts = list(zip(vals[0::2], vals[1::2]))
            if len(pts) < 3:
                continue
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            c = poly.centroid
            self.ids.append(str(fid))
            self.polys.append(poly)
            self.cent.append((c.x, c.y))

        self.n = len(self.ids)
        self._bbox_index = {}
        self._cent_index = {}
        for i, poly in enumerate(self.polys):
            minx, miny, maxx, maxy = poly.bounds
            for gx in range(int(minx // self.CELL), int(maxx // self.CELL) + 1):
                for gy in range(int(miny // self.CELL), int(maxy // self.CELL) + 1):
                    self._bbox_index.setdefault((gx, gy), []).append(i)
            cx, cy = self.cent[i]
            self._cent_index.setdefault((int(cx // self.CELL), int(cy // self.CELL)), []).append(i)

        self._cache = {}
        self.stats = {"contains": 0, "nearest": 0, "miss": 0}
        log(f"[OK  ] 构建 {self.n:,} 个围栏多边形, 网格 {len(self._cent_index):,} 个")

    def match(self, lng, lat):
        """返回 fence_id（未命中返回 None）"""
        key = (lng, lat)
        hit = self._cache.get(key)
        if hit is not None:
            return hit if hit != "MISS" else None

        result, tag = self._match_raw(lng, lat)
        self.stats[tag] += 1
        self._cache[key] = result if result else "MISS"
        return result

    def _match_raw(self, lng, lat):
        from shapely.geometry import Point
        gx, gy = int(lng // self.CELL), int(lat // self.CELL)

        # ① 点在多边形内
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self._bbox_index.get((gx + dx, gy + dy), ()):
                    if self.polys[i].contains(Point(lng, lat)):
                        return self.ids[i], "contains"

        # ② 最近围栏（限距 MAX_MATCH_DIST_M）
        best_id, best_d = None, MAX_MATCH_DIST_M ** 2
        for r in range(MAX_SEARCH_RINGS):
            found = False
            for dx in range(-r, r + 1):
                if abs(dx) != r and r > 0:
                    continue
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    for i in self._cent_index.get((gx + dx, gy + dy), ()):
                        found = True
                        cx, cy = self.cent[i]
                        d = ((cx - lng) * M_PER_DEG_LNG) ** 2 + ((cy - lat) * M_PER_DEG_LAT) ** 2
                        if d < best_d:
                            best_d, best_id = d, self.ids[i]
            if best_id is not None:
                return best_id, "nearest"
            if not found and r > 3:
                break
        return None, "miss"


# ============================================================
# 2. 天气数据
# ============================================================
def load_weather(weather_file):
    log(f"[FILE] 加载天气: {weather_file}")
    df = pd.read_csv(weather_file)
    df["TIME"] = pd.to_datetime(df["TIME"])
    table = {}
    for _, r in df.iterrows():
        coco = r.get("COCO")
        try:
            coco_int = int(coco)
        except (TypeError, ValueError):
            coco_int = 0
        wtype, vis = COCO_MAP.get(coco_int, ("未知", 8.0))
        rain = r.get("PRCP")
        key = (r["TIME"].strftime("%Y-%m-%d"), int(r["TIME"].hour))
        table[key] = {
            "weather_type": wtype,
            "weather_temp": None if pd.isna(r.get("TEMP")) else float(r.get("TEMP")),
            "weather_rain": 0.0 if pd.isna(rain) else float(rain),
            "weather_visibility": vis,
            "weather_wind": None if pd.isna(r.get("WSPD")) else float(r.get("WSPD")),
            "weather_humidity": None if pd.isna(r.get("RHUM")) else float(r.get("RHUM")),
        }
    log(f"[OK  ] 天气 {len(table)} 个小时槽位, 类型分布: "
        f"{pd.Series([v['weather_type'] for v in table.values()]).value_counts().to_dict()}")
    return table


def weather_at(table, ts):
    """按 (日期, 小时) 取天气"""
    return table.get((ts.strftime("%Y-%m-%d"), int(ts.hour)))


# ============================================================
# 3. MySQL
# ============================================================
DDL_AGG = f"""
CREATE TABLE IF NOT EXISTS {MYSQL_TABLE_AGG} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fence_id VARCHAR(64) NOT NULL,
    window_start DATETIME NOT NULL,
    window_end DATETIME NOT NULL,
    net_flow DOUBLE DEFAULT 0,
    return_count INT DEFAULT 0,
    borrow_count INT DEFAULT 0,
    avg_duration DOUBLE DEFAULT NULL,
    weather_type VARCHAR(32) DEFAULT NULL,
    weather_temp DOUBLE DEFAULT NULL,
    weather_rain DOUBLE DEFAULT NULL,
    weather_visibility DOUBLE DEFAULT NULL,
    hour INT DEFAULT NULL,
    date DATE DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fence_window (fence_id, window_start),
    KEY idx_window (window_start),
    KEY idx_date_hour (date, hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_WEATHER = f"""
CREATE TABLE IF NOT EXISTS {MYSQL_TABLE_WEATHER} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    weather_type VARCHAR(32) NOT NULL,
    hour INT NOT NULL,
    avg_net_flow DOUBLE DEFAULT 0,
    total_borrow INT DEFAULT 0,
    total_return INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_weather_hour (weather_type, hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_STATE = f"""
CREATE TABLE IF NOT EXISTS {MYSQL_TABLE_STATE} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    state_key VARCHAR(64) NOT NULL,
    state_value VARCHAR(128),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_key (state_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def mysql_conn(use_db=True):
    params = dict(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PWD,
                  charset="utf8mb4", collation="utf8mb4_general_ci")
    if use_db:
        params["database"] = MYSQL_DB
    return mysql.connector.connect(**params)


def init_mysql(reset=False):
    cnx = mysql_conn(use_db=False)
    cur = cnx.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB} DEFAULT CHARACTER SET utf8mb4")
    cur.execute(f"USE {MYSQL_DB}")
    if reset:
        cur.execute(f"TRUNCATE TABLE {MYSQL_TABLE_WEATHER}")
        cur.execute(f"TRUNCATE TABLE {MYSQL_TABLE_AGG}")
        log("[WARN] 已清空结果表（--reset）")
    for ddl in (DDL_AGG, DDL_WEATHER, DDL_STATE):
        cur.execute(ddl)
    cnx.commit()
    cur.close()
    cnx.close()
    log("[OK  ] MySQL 结果表就绪")


def upsert_agg(cursor, rows):
    """幂等写入：同一窗口重复批次覆盖而非累加"""
    sql = f"""
        INSERT INTO {MYSQL_TABLE_AGG}
        (fence_id, window_start, window_end, net_flow, return_count, borrow_count,
         avg_duration, weather_type, weather_temp, weather_rain, weather_visibility, hour, date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            net_flow = VALUES(net_flow),
            return_count = VALUES(return_count),
            borrow_count = VALUES(borrow_count),
            avg_duration = VALUES(avg_duration),
            weather_type = VALUES(weather_type),
            weather_temp = VALUES(weather_temp),
            weather_rain = VALUES(weather_rain),
            weather_visibility = VALUES(weather_visibility),
            hour = VALUES(hour),
            date = VALUES(date)
    """
    cursor.executemany(sql, rows)


def refresh_weather_effect(cursor):
    """由结果表重算天气效应汇总"""
    cursor.execute(f"""
        SELECT weather_type, hour, AVG(net_flow), SUM(borrow_count), SUM(return_count)
        FROM {MYSQL_TABLE_AGG}
        WHERE weather_type IS NOT NULL
        GROUP BY weather_type, hour
    """)
    rows = cursor.fetchall()
    if not rows:
        return
    sql = f"""
        INSERT INTO {MYSQL_TABLE_WEATHER} (weather_type, hour, avg_net_flow, total_borrow, total_return)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            avg_net_flow = VALUES(avg_net_flow),
            total_borrow = VALUES(total_borrow),
            total_return = VALUES(total_return)
    """
    cursor.executemany(sql, [(w, h, float(f), int(b), int(r)) for w, h, f, b, r in rows])


def save_state(cursor, pairs):
    sql = f"""
        INSERT INTO {MYSQL_TABLE_STATE} (state_key, state_value) VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE state_value = VALUES(state_value)
    """
    cursor.executemany(sql, pairs)


# ============================================================
# 4. 主流程
# ============================================================
KAFKA_SCHEMA = StructType([
    StructField("bicycle_id", StringType()),
    StructField("latitude", DoubleType()),
    StructField("longitude", DoubleType()),
    StructField("lock_status", IntegerType()),
    StructField("update_time", StringType()),
    StructField("date", StringType()),
    StructField("hour", IntegerType()),
    StructField("duration", IntegerType()),
    StructField("order_id", IntegerType()),
])


def main():
    ap = argparse.ArgumentParser(description="共享单车潮汐 Spark Streaming 消费端")
    ap.add_argument("--reset", action="store_true", help="启动前清空 MySQL 结果表")
    ap.add_argument("--starting-offsets", default="earliest", help="Kafka 起始位点 earliest/latest")
    args = ap.parse_args()

    log("=" * 64)
    log("  Spark Structured Streaming - 共享单车潮汐分析")
    log("=" * 64)

    fence_index = FenceIndex(FENCE_FILE)
    weather_table = load_weather(WEATHER_FILE)
    init_mysql(reset=args.reset)

    spark = (SparkSession.builder
             .appName("BikeTidalAnalysis")
             .config("spark.sql.shuffle.partitions", "4")
             .config("spark.driver.memory", "2g")
             .config("spark.sql.streaming.stateStore.providerClass",
                     "org.apache.spark.sql.execution.streaming.state.HDFSBackedStateStoreProvider")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    log(f"[OK  ] SparkSession 就绪 (Spark {spark.version})")

    raw = (spark.readStream.format("kafka")
           .option("kafka.bootstrap.servers", KAFKA_BROKER)
           .option("subscribe", KAFKA_TOPIC)
           .option("startingOffsets", args.starting_offsets)
           .option("failOnDataLoss", "false")
           .option("maxOffsetsPerTrigger", MAX_OFFSETS_PER_TRIGGER)
           .load())

    events = raw.select(from_json(col("value").cast("string"), KAFKA_SCHEMA).alias("d")).select("d.*")

    valid = (events
             .filter(col("update_time").isNotNull())
             .filter(col("latitude").isNotNull() & col("longitude").isNotNull())
             .filter(col("duration").isNull() | col("duration").between(60, 3600))
             .withColumn("event_time", col("update_time").cast("timestamp")))

    from pyspark.sql.functions import round as _round

    # 按 5 位小数网格（≈1.1m）先把事件归并，围栏映射放到 driver 端做
    grid_view = (valid
                 .withColumn("grid_lat", _round(col("latitude"), GRID_PRECISION))
                 .withColumn("grid_lng", _round(col("longitude"), GRID_PRECISION)))

    agg = (grid_view
           .withWatermark("event_time", WATERMARK_DELAY)
           .groupBy(window(col("event_time"), WINDOW_DURATION), col("grid_lat"), col("grid_lng"))
           .agg(_sum(when(col("lock_status") == 0, 1).otherwise(0)).alias("borrow_count"),
                _sum(when(col("lock_status") == 1, 1).otherwise(0)).alias("return_count"),
                _avg(col("duration")).alias("avg_duration"),
                count(lit(1)).alias("sample_count"))
           .select(col("window.start").alias("window_start"),
                   col("window.end").alias("window_end"),
                   col("grid_lat"), col("grid_lng"),
                   col("borrow_count"), col("return_count"),
                   col("avg_duration"), col("sample_count")))

    counter = {"batches": 0, "events": 0, "rows": 0, "missed": 0}

    def process_batch(batch_df, batch_id):
        t0 = time.time()
        if batch_df.isEmpty():
            return

        pdf = batch_df.toPandas()
        if pdf.empty:
            return

        # grid → fence（带 memo 缓存）
        grid_map = {}
        for glat, glng in zip(pdf["grid_lat"], pdf["grid_lng"]):
            key = (glat, glng)
            if key not in grid_map:
                grid_map[key] = fence_index.match(float(glng), float(glat))
        pdf["fence_id"] = [grid_map[(glat, glng)] for glat, glng in zip(pdf["grid_lat"], pdf["grid_lng"])]

        missed = int(pdf["fence_id"].isna().sum())
        pdf_unmatched = pdf[pdf["fence_id"].isna()]
        missed_events = int(pdf_unmatched["sample_count"].sum()) if len(pdf_unmatched) else 0
        pdf = pdf[pdf["fence_id"].notna()]
        if pdf.empty:
            log(f"[BATCH {batch_id}] 全部 {missed} 个网格未匹配到围栏（{missed_events} 条事件）")
            return

        counter["missed"] += missed_events

        # 网格 → 围栏 汇总
        pdf["window_start"] = pd.to_datetime(pdf["window_start"])
        pdf["window_end"] = pd.to_datetime(pdf["window_end"])
        g = (pdf.groupby(["window_start", "window_end", "fence_id"], sort=False)
             .agg(borrow_count=("borrow_count", "sum"),
                  return_count=("return_count", "sum"),
                  sample_count=("sample_count", "sum"),
                  avg_duration=("avg_duration", "mean"))
             .reset_index())

        g["net_flow"] = g["return_count"] - g["borrow_count"]

        rows = []
        for r in g.itertuples(index=False):
            w = weather_at(weather_table, r.window_start) or {}
            rows.append((
                str(r.fence_id)[:64],
                r.window_start.strftime("%Y-%m-%d %H:%M:%S"),
                r.window_end.strftime("%Y-%m-%d %H:%M:%S"),
                float(r.net_flow), int(r.return_count), int(r.borrow_count),
                None if pd.isna(r.avg_duration) else float(r.avg_duration),
                w.get("weather_type"), w.get("weather_temp"), w.get("weather_rain"),
                w.get("weather_visibility"),
                int(r.window_start.hour), r.window_start.strftime("%Y-%m-%d"),
            ))

        try:
            cnx = mysql_conn()
            cur = cnx.cursor()
            upsert_agg(cur, rows)
            cnx.commit()
            counter["batches"] += 1
            counter["rows"] += len(rows)
            counter["events"] += int(g["sample_count"].sum())

            if counter["batches"] % WEATHER_REFRESH_BATCHES == 0:
                refresh_weather_effect(cur)
                cnx.commit()

            save_state(cur, [
                ("last_batch_id", str(batch_id)),
                ("last_window_start", str(g["window_start"].max())),
                ("total_events", str(counter["events"])),
                ("missed_events", str(counter["missed"])),
                ("fence_rows", str(counter["rows"])),
                ("updated_at_real", time.strftime("%Y-%m-%d %H:%M:%S")),
            ])
            cnx.commit()
            cur.close()
            cnx.close()
        except Exception as e:
            log(f"[ERR ] Batch {batch_id} 写 MySQL 失败: {e}")
            return

        log(f"[BATCH {batch_id}] 窗口 {g['window_start'].min()} ~ {g['window_start'].max()} | "
            f"围栏行 {len(rows)} | 事件 {int(g['sample_count'].sum())} | 未匹配 {missed_events} | "
            f"累计 {counter['events']:,} | {time.time() - t0:.2f}s")

    query = (agg.writeStream
             .foreachBatch(process_batch)
             .outputMode("update")
             .option("checkpointLocation", CHECKPOINT_DIR)
             .trigger(processingTime=TRIGGER_INTERVAL)
             .start())

    log(f"[START] 流任务已启动：{TRIGGER_INTERVAL}微批 / {WINDOW_DURATION}窗口")
    log(f"        Kafka {KAFKA_BROKER} topic=`{KAFKA_TOPIC}`  offsets={args.starting_offsets}")
    log("        等待数据 ... (确认 producer/kafka_producer.py 已启动)")
    log("[STOP ] Ctrl+C 停止")
    try:
        query.awaitTermination()
    except KeyboardInterrupt:
        log("\n[STOP] 正在停止流任务 ...")
        query.stop()
        try:
            cnx = mysql_conn()
            cur = cnx.cursor()
            refresh_weather_effect(cur)
            cnx.commit()
            cur.close()
            cnx.close()
        except Exception as e:
            log(f"[WARN] 收尾汇总失败: {e}")
        log(f"[STATS] 匹配命中: contains={fence_index.stats['contains']}, "
            f"nearest={fence_index.stats['nearest']}, miss={fence_index.stats['miss']}")
        log("[DONE] 已停止")


if __name__ == "__main__":
    main()
