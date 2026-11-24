# -*- coding: utf-8 -*-
"""
api/main.py — FastAPI 后端：REST 查询 + WebSocket 实时推送 + Redis 缓存

对外接口：
    GET  /api/fences          围栏列表（含中心点坐标）
    GET  /api/windows         已有数据的时间窗口列表
    GET  /api/agg?window=...  指定窗口的围栏级净流入数据
    GET  /api/agg/latest      最新窗口数据
    GET  /api/series          全城潮汐时序（E(t) 潮汐指数）
    GET  /api/weather?window= 当前窗口天气 + 天气效应对比
    GET  /api/state           流任务进度
    WS   /ws/tidal            窗口数据更新时推送给前端

启动：
    uvicorn api.main:app --reload --port 8000
"""

import os
import re
import io
import sys
import json
import time
import asyncio
import threading
from datetime import datetime, timedelta

import pandas as pd
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import mysql.connector
from mysql.connector import pooling

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The merged archive keeps a single copy of the data in ../raw (see README.md).
DATA_ROOT = os.path.normpath(os.path.join(BASE_DIR, "..", "raw"))
FENCE_FILE = os.path.join(DATA_ROOT, "Electronic fence data", "STATION.csv")
WEATHER_FILE = os.path.join(DATA_ROOT, "Weather data", "Weather data.csv")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

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


MYSQL_CONF = dict(
    host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
    port=int(os.environ.get("MYSQL_PORT", "3306")),
    user=os.environ.get("MYSQL_USER", "bike"),
    password=_env("MYSQL_PASSWORD"),
    database=os.environ.get("MYSQL_DATABASE", "bike_db"),
    charset="utf8mb4",
    collation="utf8mb4_general_ci",
)
REDIS_CONF = dict(host="127.0.0.1", port=6379, db=0, decode_responses=True)
CACHE_TTL = 15          # 秒

app = FastAPI(title="共享单车潮汐分析 API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ============================================================
# 连接池
# ============================================================
db_pool = pooling.MySQLConnectionPool(pool_name="bike_pool", pool_size=6, **MYSQL_CONF)

try:
    import redis
    redis_client = redis.Redis(**REDIS_CONF)
    redis_client.ping()
    print("[OK] Redis 已连接")
except Exception as e:
    redis_client = None
    print(f"[WARN] Redis 不可用，走直查 MySQL: {e}")


def cache_get(key):
    if not redis_client:
        return None
    try:
        v = redis_client.get(key)
        return json.loads(v) if v else None
    except Exception:
        return None


def cache_set(key, value, ttl=CACHE_TTL):
    if not redis_client:
        return
    try:
        redis_client.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        pass


def db():
    return db_pool.get_connection()


def query(sql, params=None, fetch=True):
    cnx = db()
    cur = cnx.cursor(dictionary=True)
    try:
        cur.execute(sql, params or ())
        rows = cur.fetchall() if fetch else []
        return rows
    finally:
        cur.close()
        cnx.close()


# ============================================================
# 围栏字典（启动时加载一次）
# ============================================================
FENCE_COORD = {}


def load_fence_coord():
    from shapely.geometry import Polygon
    num_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
    df = pd.read_excel(FENCE_FILE, engine="openpyxl")
    for fid, loc in zip(df["FENCE_ID"], df["FENCE_LOC"]):
        vals = [float(x) for x in num_re.findall(str(loc))]
        pts = list(zip(vals[0::2], vals[1::2]))
        if len(pts) < 3:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        c = poly.centroid
        FENCE_COORD[str(fid)] = [round(c.y, 6), round(c.x, 6)]   # [lat, lng]
    print(f"[OK] 围栏中心点 {len(FENCE_COORD):,} 个")


load_fence_coord()


def fmt_dt(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, (datetime, pd.Timestamp)) else v


# ============================================================
# REST 接口
# ============================================================
@app.get("/api/fences")
def get_fences():
    """围栏列表（Leaflet 用）"""
    cached = cache_get("fences")
    if cached:
        return cached
    data = [{"fence_id": k, "lat": v[0], "lng": v[1]} for k, v in FENCE_COORD.items()]
    cache_set("fences", data, ttl=3600)
    return data


@app.get("/api/windows")
def get_windows():
    """有数据的时间窗口列表"""
    rows = query(f"""
        SELECT window_start, window_end, hour, date,
               COUNT(*) AS fence_num,
               SUM(borrow_count) AS borrow_total,
               SUM(return_count) AS return_total
        FROM bike_agg_results
        GROUP BY window_start, window_end, hour, date
        ORDER BY window_start
    """)
    out = []
    for r in rows:
        out.append({
            "window_start": fmt_dt(r["window_start"]),
            "window_end": fmt_dt(r["window_end"]),
            "hour": r["hour"],
            "date": str(r["date"]),
            "fence_num": r["fence_num"],
            "borrow_total": int(r["borrow_total"] or 0),
            "return_total": int(r["return_total"] or 0),
        })
    return out


@app.get("/api/agg")
def get_agg(window: str = Query(None, description="窗口起点 yyyy-MM-dd HH:mm:ss"), limit: int = 5000):
    """指定窗口的围栏级净流入数据"""
    if not window:
        rows = query("SELECT MAX(window_start) AS w FROM bike_agg_results")
        if not rows or not rows[0]["w"]:
            return JSONResponse([])
        window = fmt_dt(rows[0]["w"])

    ck = f"agg:{window}"
    cached = cache_get(ck)
    if cached:
        return cached

    rows = query("""
        SELECT fence_id, window_start, window_end, net_flow, return_count, borrow_count,
               avg_duration, weather_type, weather_temp, weather_rain, weather_visibility, hour
        FROM bike_agg_results
        WHERE window_start = %s
        ORDER BY net_flow ASC
        LIMIT %s
    """, (window, limit))

    out = []
    for r in rows:
        coord = FENCE_COORD.get(r["fence_id"])
        if not coord:
            continue
        out.append({
            "fence_id": r["fence_id"],
            "window_start": fmt_dt(r["window_start"]),
            "window_end": fmt_dt(r["window_end"]),
            "net_flow": int(r["net_flow"] or 0),
            "return_count": int(r["return_count"] or 0),
            "borrow_count": int(r["borrow_count"] or 0),
            "avg_duration": float(r["avg_duration"]) if r["avg_duration"] is not None else None,
            "weather_type": r["weather_type"],
            "weather_temp": r["weather_temp"],
            "weather_rain": r["weather_rain"],
            "weather_visibility": r["weather_visibility"],
            "hour": r["hour"],
            "latitude": coord[0],
            "longitude": coord[1],
        })
    cache_set(ck, out)
    return out


@app.get("/api/agg/latest")
def get_agg_latest():
    rows = query("SELECT MAX(window_start) AS w FROM bike_agg_results")
    if not rows or not rows[0]["w"]:
        return JSONResponse([])
    return get_agg(fmt_dt(rows[0]["w"]))


@app.get("/api/series")
def get_series():
    """全城潮汐时序：每个窗口的总借/总还/净流入 + 潮汐指数 E(t)"""
    rows = query("""
        SELECT window_start, hour, date,
               SUM(borrow_count) AS borrow_total,
               SUM(return_count) AS return_total,
               SUM(ABS(net_flow)) AS imbalance,
               AVG(ABS(net_flow)) AS avg_abs_net,
               COUNT(*) AS fence_num,
               MAX(weather_type) AS weather_type,
               MAX(weather_temp) AS weather_temp
        FROM bike_agg_results
        GROUP BY window_start, hour, date
        ORDER BY window_start
    """)
    out = []
    for r in rows:
        b = int(r["borrow_total"] or 0)
        rt = int(r["return_total"] or 0)
        imb = int(r["imbalance"] or 0)
        # 潮汐指数 E(t)：失衡流量占订单总量的比重(%)，越大说明潮汐越明显
        e = round(imb * 100.0 / max(b + rt, 1), 2)
        out.append({
            "window_start": fmt_dt(r["window_start"]),
            "hour": r["hour"],
            "date": str(r["date"]),
            "borrow_total": b,
            "return_total": rt,
            "net_flow": rt - b,
            "imbalance": imb,
            "e_index": e,
            "fence_num": r["fence_num"],
            "weather_type": r["weather_type"],
            "weather_temp": r["weather_temp"],
        })
    return out


@app.get("/api/weather")
def get_weather(window: str = Query(None)):
    """当前窗口天气 + 不同天气下的骑行量对比"""
    detail = None
    if window:
        rows = query("""
            SELECT weather_type, weather_temp, weather_rain, weather_visibility, hour, date
            FROM bike_agg_results WHERE window_start = %s LIMIT 1
        """, (window,))
        if rows:
            r = rows[0]
            detail = {
                "weather_type": r["weather_type"],
                "weather_temp": float(r["weather_temp"]) if r["weather_temp"] is not None else None,
                "weather_rain": float(r["weather_rain"]) if r["weather_rain"] is not None else 0.0,
                "weather_visibility": float(r["weather_visibility"]) if r["weather_visibility"] is not None else None,
                "hour": r["hour"],
                "date": str(r["date"]),
            }
    if detail is None:
        rows = query("""
            SELECT weather_type, weather_temp, weather_rain, weather_visibility, hour
            FROM bike_agg_results
            WHERE window_start = (SELECT MAX(window_start) FROM bike_agg_results) LIMIT 1
        """)
        if rows:
            r = rows[0]
            detail = {
                "weather_type": r["weather_type"],
                "weather_temp": float(r["weather_temp"]) if r["weather_temp"] is not None else None,
                "weather_rain": float(r["weather_rain"]) if r["weather_rain"] is not None else 0.0,
                "weather_visibility": float(r["weather_visibility"]) if r["weather_visibility"] is not None else None,
                "hour": r["hour"],
            }

    compare = query("""
        SELECT weather_type, hour, avg_net_flow, total_borrow, total_return
        FROM bike_weather_effect ORDER BY weather_type, hour
    """)
    cmp_list = [{
        "weather_type": r["weather_type"],
        "hour": r["hour"],
        "avg_net_flow": float(r["avg_net_flow"] or 0),
        "total_borrow": int(r["total_borrow"] or 0),
        "total_return": int(r["total_return"] or 0),
    } for r in compare]

    return {"current": detail, "compare": cmp_list}


@app.get("/api/state")
def get_state():
    rows = query("SELECT state_key, state_value FROM bike_stream_state")
    return {r["state_key"]: r["state_value"] for r in rows}


@app.get("/api/health")
def health():
    try:
        rows = query("SELECT COUNT(*) AS c FROM bike_agg_results")
        return {"status": "ok", "agg_rows": rows[0]["c"], "redis": bool(redis_client)}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


# ============================================================
# WebSocket：MySQL 出现新窗口时推送
# ============================================================
@app.websocket("/ws/tidal")
async def ws_tidal(ws: WebSocket):
    await ws.accept()
    print("[WS] 客户端已连接")
    last_sent = None
    try:
        while True:
            try:
                rows = query("SELECT MAX(window_start) AS w, COUNT(DISTINCT window_start) AS n FROM bike_agg_results")
                latest = fmt_dt(rows[0]["w"]) if rows and rows[0]["w"] else None
            except Exception as e:
                latest = None
                print(f"[WS] 查询失败: {e}")

            if latest and latest != last_sent:
                last_sent = latest
                await ws.send_json({
                    "type": "new_window",
                    "window_start": latest,
                    "window_total": rows[0]["n"] if rows else 0,
                })
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        print("[WS] 客户端断开")
    except Exception as e:
        print(f"[WS] 异常: {e}")


# 静态前端（放最后，避免覆盖 /api/*）
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
