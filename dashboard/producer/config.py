# -*- coding: utf-8 -*-
"""
producer/config.py — 共享单车历史订单回放配置

数据来源：Shared bicycle data/Shared bicycle data/Order data/order data.csv
（实际是 xlsx，pandas 需要 engine="openpyxl"）

原始字段：
    BICYCLE_ID / LATITUDE / LONGITUDE / LOCK_STATUS / UPDATE_TIME1 / UPDATE_TIME2 / ORDER DURATION
    LOCK_STATUS: 0 = 借车（开锁）, 1 = 还车（关锁）
    UPDATE_TIME1: 日期（2020-12-21 ~ 2020-12-25）
    UPDATE_TIME2: 时刻（06:00:00 ~ 09:59:59，已是早高峰）
"""

import os

# ── 路径 ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The merged archive keeps a single copy of the data in ../raw (see README.md).
DATA_ROOT = os.path.normpath(os.path.join(BASE_DIR, "..", "raw"))

ORDER_FILE = os.path.join(DATA_ROOT, "Order data", "order data.csv")
FENCE_FILE = os.path.join(DATA_ROOT, "Electronic fence data", "STATION.csv")
WEATHER_FILE = os.path.join(DATA_ROOT, "Weather data", "Weather data.csv")

# ── Kafka ───────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
KAFKA_TOPIC = "bike_events"

# ── 回放控制 ────────────────────────────────────────────
# 回放倍速：1 秒现实时间 = PLAYBACK_SPEED 秒数据时间
#   300x → 一天 4 小时约需 48 秒，5 天共约 4 分钟
PLAYBACK_SPEED = 300.0

# 数据中的长时间空档（跨天）不按倍速等待，只做短暂间隔
MAX_GAP_SECONDS = 600.0
GAP_PAUSE_SECONDS = 0.3

# ── 数据过滤 ────────────────────────────────────────────
# 只回放这些日期（None = 全部）
DATE_FILTER = None           # 例：["2020-12-21", "2020-12-22"]
# 只回放这些小时（None = 全部）
HOUR_FILTER = None           # 例：[7, 8]
# 最多发送多少条事件（None = 全部，调试时可设 20000）
MAX_EVENTS = None

# 订单行程时长过滤（秒）：借还配对后计算
DURATION_MIN = 60
DURATION_MAX = 3600

# ── 发送控制 ────────────────────────────────────────────
SEND_LOG_EVERY = 20000       # 每多少条打印一次进度
