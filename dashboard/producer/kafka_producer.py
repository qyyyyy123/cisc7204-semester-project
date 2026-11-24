# -*- coding: utf-8 -*-
"""
producer/kafka_producer.py — 历史订单 → Kafka 实时事件回放

流程：
    1. 读取 order data.csv（xlsx，441350 行事件 = 220675 笔订单）
    2. 组合 UPDATE_TIME1(日期) + UPDATE_TIME2(时刻) 得到事件时间戳
    3. BICYCLE_ID 向前填充（同一辆车的记录连续存放）
    4. 借车(0)/还车(1) 按车辆配对成订单，过滤异常时长
    5. 每笔订单拆成 2 条事件（借车事件 + 还车事件），全局按时间排序
    6. 按 PLAYBACK_SPEED 回放推送到 Kafka topic bike_events

消息格式（每条一个 JSON）：
{
  "bicycle_id": "BICYCLE_1",
  "latitude": 24.509607,
  "longitude": 118.155216,
  "lock_status": 0,          # 0=借车 1=还车
  "update_time": "2020-12-22 08:00:44",
  "date": "2020-12-22",
  "hour": 8,
  "duration": 853,           # 订单时长（秒），借车/还车事件共享
  "order_id": 12345          # 同一笔订单的两条事件 order_id 相同
}
"""

import sys
import io
import os
import re
import time
import json
import argparse

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg

from kafka import KafkaProducer
from kafka.errors import KafkaError

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")


def log(msg):
    print(msg, flush=True)


# ============================================================
# 1. 读取并整理订单事件
# ============================================================
def load_events(limit_rows=None):
    """读取原始订单文件 → 返回按时间排序的事件列表"""
    log(f"[FILE] 读取订单数据 ...\n       {cfg.ORDER_FILE}")

    t0 = time.time()
    df = pd.read_excel(cfg.ORDER_FILE, engine="openpyxl", nrows=limit_rows)
    log(f"[OK  ] 原始行数 {len(df):,} 耗时 {time.time() - t0:.1f}s, 列={list(df.columns)}")

    # 同一辆车的行车记录连续存放，向前填充车号
    df["BICYCLE_ID"] = df["BICYCLE_ID"].ffill()

    # 有效坐标 + 有效锁状态
    df = df.dropna(subset=["LATITUDE", "LONGITUDE", "LOCK_STATUS", "BICYCLE_ID"])
    df["LOCK_STATUS"] = df["LOCK_STATUS"].astype(int)

    # UPDATE_TIME1(日期) + UPDATE_TIME2(时刻) → 完整时间戳
    date_part = pd.to_datetime(df["UPDATE_TIME1"]).dt.strftime("%Y-%m-%d")
    time_part = df["UPDATE_TIME2"].astype(str).str.extract(TIME_RE.pattern, expand=False)
    bad_time = time_part.isna().any(axis=1)
    if bad_time.any():
        log(f"[WARN] 丢弃 {int(bad_time.sum())} 行无效时刻")
        df = df[~bad_time]
        date_part = date_part[~bad_time]
        time_part = time_part[~bad_time]
    clock = time_part[0] + ":" + time_part[1] + ":" + time_part[2]
    df["UPDATE_TIME"] = pd.to_datetime(date_part + " " + clock)

    # 过滤日期 / 小时
    if cfg.DATE_FILTER:
        df = df[df["UPDATE_TIME"].dt.strftime("%Y-%m-%d").isin(cfg.DATE_FILTER)]
    if cfg.HOUR_FILTER:
        df = df[df["UPDATE_TIME"].dt.hour.isin(cfg.HOUR_FILTER)]

    df = df.sort_values("UPDATE_TIME", kind="mergesort").reset_index(drop=True)
    log(f"[OK  ] 有效事件 {len(df):,} 条, 时间 {df['UPDATE_TIME'].min()} ~ {df['UPDATE_TIME'].max()}")

    if limit_rows:
        df = df.head(limit_rows)

    return pair_and_build(df)


def pair_and_build(df):
    """借车/还车按车号配对 → 事件流（同一订单拆借、还两条）"""
    log("[STEP] 借还配对 ...")

    events = []
    pending = {}          # bicycle_id -> 借车行信息
    ok_orders = 0
    bad_duration = 0
    unmatched_lock = 0
    unmatched_borrow = 0

    ts_list = df["UPDATE_TIME"].values
    ids = df["BICYCLE_ID"].values
    lats = df["LATITUDE"].values
    lngs = df["LONGITUDE"].values
    locks = df["LOCK_STATUS"].values

    for i in range(len(df)):
        bid = ids[i]
        ts = pd.Timestamp(ts_list[i])
        row = {
            "ts": ts,
            "lat": float(lats[i]),
            "lng": float(lngs[i]),
        }

        if locks[i] == 0:
            pending[bid] = row
        elif locks[i] == 1:
            start = pending.pop(bid, None)
            if start is None:
                unmatched_lock += 1
                continue
            duration = (ts - start["ts"]).total_seconds()
            if duration < cfg.DURATION_MIN or duration > cfg.DURATION_MAX:
                bad_duration += 1
                continue

            ok_orders += 1
            order_id = ok_orders
            dur = int(duration)

            events.append({
                "bicycle_id": str(bid),
                "latitude": round(start["lat"], 6),
                "longitude": round(start["lng"], 6),
                "lock_status": 0,
                "update_time": start["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                "date": start["ts"].strftime("%Y-%m-%d"),
                "hour": int(start["ts"].hour),
                "duration": dur,
                "order_id": order_id,
                "_ts": start["ts"],
            })
            events.append({
                "bicycle_id": str(bid),
                "latitude": round(row["lat"], 6),
                "longitude": round(row["lng"], 6),
                "lock_status": 1,
                "update_time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "date": ts.strftime("%Y-%m-%d"),
                "hour": int(ts.hour),
                "duration": dur,
                "order_id": order_id,
                "_ts": ts,
            })

        if (i + 1) % 100000 == 0:
            log(f"       处理 {i + 1:,} 行, 有效订单 {ok_orders:,}")

    unmatched_borrow = len(pending)
    events.sort(key=lambda e: (e["_ts"], e["order_id"]))
    for e in events:
        e.pop("_ts", None)

    log(f"[OK  ] 有效订单 {ok_orders:,} 笔 → 事件 {len(events):,} 条")
    log(f"       时长越界丢弃 {bad_duration:,} 笔, 无主还车 {unmatched_lock:,} 条, 未闭合借车 {unmatched_borrow:,} 条")
    if events:
        log(f"       时间范围 {events[0]['update_time']} ~ {events[-1]['update_time']}")
    return events


# ============================================================
# 2. Kafka 回放
# ============================================================
def create_producer():
    try:
        p = KafkaProducer(
            bootstrap_servers=cfg.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks=1,
            linger_ms=50,
            batch_size=128 * 1024,
            compression_type="gzip",
            request_timeout_ms=60000,
        )
        return p
    except KafkaError as e:
        log(f"[ERR ] 连不上 Kafka: {cfg.KAFKA_BOOTSTRAP_SERVERS}\n       {e}")
        log("       请确认 docker compose up -d 已启动 kafka")
        raise


def playback(producer, events, speed=None, dry_run=False):
    if not events:
        log("[ERR ] 没有事件可回放")
        return

    from datetime import datetime
    speed = speed or cfg.PLAYBACK_SPEED
    if dry_run:
        speed = max(speed, 1e6)   # 干跑不等候，只验证数据流程

    total = (datetime.strptime(events[-1]["update_time"], "%Y-%m-%d %H:%M:%S")
             - datetime.strptime(events[0]["update_time"], "%Y-%m-%d %H:%M:%S")).total_seconds()
    log(f"\n[SEND] 回放 {len(events):,} 条事件 → topic `{cfg.KAFKA_TOPIC}` ({speed:.0f}x)")
    log(f"       数据跨度 {total / 3600:.1f} 小时, 预计耗时 ≈ {total / speed:.0f}s + 跨天停顿")

    sent = 0
    prev = None
    t0 = time.time()
    # 节拍控制：累计「压缩后的数据时间」再算目标墙钟时间，
    # 避免每条事件都调用 sleep（Windows sleep 最小粒度会把回放拖慢十几倍）
    wall_start = time.perf_counter()
    comp_elapsed = 0.0

    for ev in events:
        curr = datetime.strptime(ev["update_time"], "%Y-%m-%d %H:%M:%S")
        if prev is not None and not dry_run:
            delta = (curr - prev).total_seconds()
            if delta > cfg.MAX_GAP_SECONDS:
                delta = cfg.GAP_PAUSE_SECONDS * speed   # 跨天空档折叠成短暂停顿
            comp_elapsed += max(delta, 0.0)
        prev = curr

        if not dry_run:
            wait = (wall_start + comp_elapsed / speed) - time.perf_counter()
            if wait > 0.005:
                time.sleep(wait)
            producer.send(cfg.KAFKA_TOPIC, key=ev["bicycle_id"], value=ev)

        sent += 1
        if sent % cfg.SEND_LOG_EVERY == 0:
            el = time.time() - t0
            log(f"       [{datetime.now():%H:%M:%S}] {sent:,}/{len(events):,} "
                f"({sent * 100 // len(events)}%) 用时 {el:.0f}s")

    if not dry_run:
        producer.flush()
    log(f"[DONE] 完成 {sent:,} 条, 耗时 {time.time() - t0:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="共享单车订单 Kafka 回放生产者")
    parser.add_argument("--speed", type=float, default=None, help=f"回放倍速，默认 {cfg.PLAYBACK_SPEED}")
    parser.add_argument("--limit", type=int, default=None, help="只回放前 N 条事件（调试用）")
    parser.add_argument("--rows", type=int, default=None, help="只读原始文件前 N 行（调试用）")
    parser.add_argument("--dry-run", action="store_true", help="只跑数据流程，不真正发送")
    parser.add_argument("--date", default=None, help="只回放指定日期，逗号分隔，如 2020-12-21,2020-12-22")
    parser.add_argument("--hour", default=None, help="只回放指定小时，逗号分隔，如 7,8")
    args = parser.parse_args()

    if args.date:
        cfg.DATE_FILTER = [d.strip() for d in args.date.split(",")]
    if args.hour:
        cfg.HOUR_FILTER = [int(h) for h in args.hour.split(",")]

    print("=" * 64)
    print("  历史订单 → Kafka 事件回放")
    print("=" * 64)

    events = load_events(limit_rows=args.rows)
    if args.limit:
        events = events[:args.limit]

    if args.dry_run:
        playback(None, events, speed=args.speed, dry_run=True)
        return

    producer = create_producer()
    try:
        playback(producer, events, speed=args.speed)
    except KeyboardInterrupt:
        log("\n[STOP] 用户中断")
    finally:
        producer.close()


if __name__ == "__main__":
    main()
