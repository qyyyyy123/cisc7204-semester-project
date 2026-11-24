#!/usr/bin/env bash
# ============================================================
# 启动 Kafka 生产者（历史订单回放）
#
# 用法：
#   bash scripts/run_producer.sh                     # 全量 5 天，默认倍速
#   bash scripts/run_producer.sh --speed 1200        # 加快
#   bash scripts/run_producer.sh --date 2020-12-21   # 只回放一天
#   bash scripts/run_producer.sh --dry-run           # 只验证数据流程不发送
#
# 参数透传给 producer/kafka_producer.py
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONIOENCODING="utf-8"
PY="D:/AppData/Local/Programs/miniconda3/envs/bike_env/python.exe"

mkdir -p logs
echo "== Kafka 生产者启动中 (日志 logs/producer.log) =="
"$PY" producer/kafka_producer.py "$@" 2>&1 | tee logs/producer.log
