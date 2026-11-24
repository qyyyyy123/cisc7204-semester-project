#!/usr/bin/env bash
# ============================================================
# 启动 FastAPI 后端（REST + WebSocket），并把前端挂在 http://127.0.0.1:8000
#
# 用法：bash scripts/run_api.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONIOENCODING="utf-8"
PY="D:/AppData/Local/Programs/miniconda3/envs/bike_env/python.exe"

mkdir -p logs
echo "== FastAPI 启动中: http://127.0.0.1:8000  (日志 logs/api.log) =="
"$PY" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 2>&1 | tee logs/api.log
