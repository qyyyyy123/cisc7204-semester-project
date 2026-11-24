#!/usr/bin/env bash
# ============================================================
# 启动 Spark Structured Streaming 消费端
#
# 说明：Git Bash 下 spark-submit.cmd 无法直接调用（报 "bad array subscript"），
#       这里改为直接用 java 启动 SparkSubmit，效果等价。
#       在 PowerShell / CMD 里也可以直接：
#           spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.9 spark/streaming_consumer.py
#
# 用法：bash scripts/run_consumer.sh [--reset]
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

export JAVA_HOME="D:/AppData/Local/Programs/Eclipse Adoptium/jdk-17.0.20.101-hotspot"
export SPARK_HOME="D:/AppData/Local/Programs/spark-3.5.9-bin-hadoop3"
export HADOOP_HOME="D:/AppData/Local/Programs/hadoop"
export PYSPARK_PYTHON="D:/AppData/Local/Programs/miniconda3/envs/bike_env/python.exe"
export PYSPARK_DRIVER_PYTHON="$PYSPARK_PYTHON"
export PYTHONIOENCODING="utf-8"
# Windows 下必须指定本地IP，否则 driver 会广播成局域网IP导致回连失败
export SPARK_LOCAL_IP=127.0.0.1

JAVA="$JAVA_HOME/bin/java"
CP="$SPARK_HOME/conf\;$SPARK_HOME/jars/*"

mkdir -p logs

echo "== Spark 消费端启动中，日志: logs/spark.log =="
"$JAVA" \
  -cp "$CP" \
  -Xmx2g \
  -XX:+IgnoreUnrecognizedVMOptions \
  --add-opens=java.base/java.lang=ALL-UNNAMED \
  --add-opens=java.base/java.lang.invoke=ALL-UNNAMED \
  --add-opens=java.base/java.lang.reflect=ALL-UNNAMED \
  --add-opens=java.base/java.io=ALL-UNNAMED \
  --add-opens=java.base/java.net=ALL-UNNAMED \
  --add-opens=java.base/java.nio=ALL-UNNAMED \
  --add-opens=java.base/java.util=ALL-UNNAMED \
  --add-opens=java.base/java.util.concurrent=ALL-UNNAMED \
  --add-opens=java.base/sun.nio.ch=ALL-UNNAMED \
  --add-opens=java.base/sun.security.action=ALL-UNNAMED \
  -Dfile.encoding=UTF-8 \
  org.apache.spark.deploy.SparkSubmit \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.9 \
  --conf spark.ui.enabled=false \
  --conf spark.driver.memory=2g \
  spark/streaming_consumer.py "$@" 2>&1 | tee logs/spark.log
