# 厦门共享单车潮汐分析 — 动态数据可视化系统

一、项目最终要输出什么

一个实时动态大屏，模拟 2020年12月21–25日 早高峰 6:00–10:00 的共享单车潮汐演变过程：

1. 地图层：Leaflet 展示厦门电子围栏，颜色随净流入实时变化
   • 🔴 红色 = 淤积（还车 > 借车）

   • 🔵 蓝色 = 缺车（借车 > 还车）

   • 颜色深浅 = 失衡强度

2. 时间序列层：ECharts 折线图，展示选定区域/全城的潮汐指数 E(t) 随30分钟窗口的演变

3. 天气效应层：侧边面板展示当前小时的天气条件（温度/降水/能见度），以及不同天气下骑行量的对比柱状图

4. 动态回放控制：播放/暂停/调速/进度条，控制数据回放速度（如 1秒 = 实际5分钟）

核心指标：净流入 = 还车量 − 借车量（正值淤积，负值缺车）

二、系统架构


┌─────────────┐    ┌──────────────┐    ┌────────────────────┐    ┌────────────┐    ┌──────────────────┐
│  历史 CSV    │───▶│  Kafka 生产者 │───▶│  Spark Structured  │───▶│   MySQL    │───▶│  FastAPI + WS    │
│  (订单/围栏/  │    │  (数据回放)    │    │  Streaming 消费者   │    │  (结果表)   │    │  (实时推送)       │
│   天气)      │    │              │    │  (围栏匹配+聚合)    │    │            │    │                  │
└─────────────┘    └──────────────┘    └────────────────────┘    └────────────┘    └──────────────────┘
                                                                                            │
                                                                                            ▼
                                                                                   ┌──────────────────┐
                                                                                   │  前端大屏         │
                                                                                   │  ECharts +       │
                                                                                   │  Leaflet         │
                                                                                   └──────────────────┘


设计原则：不做假数据。所有可视化数据来自真实历史订单，通过 Kafka 回放模拟实时流。

三、数据说明

数据文件 内容 关键字段

Order data/order data.csv 220,675条订单事件 BICYCLE_ID, LATITUDE, LONGITUDE, LOCK_STATUS(0=借车/1=还车), UPDATE_TIME

Electronic fence data/STATION.csv 14,071个围栏 围栏ID, 4个角点坐标序列

Weather data/weather.csv 天气（按小时） 温度, 降水, 风速, 湿度, 能见度, 时间

核心逻辑：LOCK_STATUS=0 → 借车事件（从某围栏借出）；LOCK_STATUS=1 → 还车事件（还入某围栏）。分别统计后相减得净流入。

四、环境准备

4.1 软件依赖

组件 版本 用途

JDK 17 Spark 运行依赖

Spark 3.5.9 (pre-built for Hadoop 3) 流处理引擎

WinUtils 3.3.6 Windows 下 Hadoop 工具

Conda — Python 环境管理

Docker Desktop (WSL2) — 运行 Kafka + MySQL + Redis

4.2 Python 环境

conda create -n bike_env python=3.10
conda activate bike_env
pip install pyspark==3.5.9 pandas kafka-python redis findspark shapely openpyxl mysql-connector-python chardet fastapi uvicorn websockets


4.3 Docker 容器

docker compose up -d
# 确认 4 个容器运行：zookeeper, kafka, mysql, redis
docker ps


4.4 环境变量（每次新终端）

如果直接用 scripts/ 下的三个脚本启动，这一节可以跳过——脚本内部已按下面的路径配置好了。
只有当你想手动敲命令时才需要设置。

set PYSPARK_PYTHON=D:\AppData\Local\Programs\miniconda3\envs\bike_env\python.exe
set JAVA_HOME=D:\AppData\Local\Programs\Eclipse Adoptium\jdk-17.0.20.101-hotspot
set SPARK_HOME=D:\AppData\Local\Programs\spark-3.5.9-bin-hadoop3
set HADOOP_HOME=D:\AppData\Local\Programs\hadoop
set PATH=%PATH%;%SPARK_HOME%\bin;%HADOOP_HOME%\bin

注意 JAVA_HOME 和 HADOOP_HOME 的实际目录名随安装方式不同而变（Adoptium / Zulu / 自建 hadoop 目录），
按本机真实路径填。Git Bash 下环境变量写法是 export XXX="D:/..."，路径用正斜杠。


五、逐步操作

步骤 1：Kafka Topic 创建

docker exec -it kafka /usr/bin/kafka-topics --create ^
  --topic bike_events ^
  --bootstrap-server localhost:9092 ^
  --partitions 3 ^
  --replication-factor 1


步骤 2：MySQL 建库建表

CREATE DATABASE IF NOT EXISTS bike_db DEFAULT CHARACTER SET utf8mb4;
USE bike_db;

-- 围栏级净流入聚合结果（Spark 每批次写入）
CREATE TABLE IF NOT EXISTS bike_agg_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fence_id VARCHAR(64),
    window_start TIMESTAMP,
    window_end TIMESTAMP,
    net_flow DOUBLE,
    return_count INT,
    borrow_count INT,
    weather_temp DOUBLE,
    weather_rain DOUBLE,
    weather_visibility DOUBLE,
    hour INT,
    date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 天气效应汇总
CREATE TABLE IF NOT EXISTS bike_weather_effect (
    id INT AUTO_INCREMENT PRIMARY KEY,
    weather_type VARCHAR(32),
    hour INT,
    avg_net_flow DOUBLE,
    total_borrow INT,
    total_return INT
);


步骤 3：确认数据文件编码

python -c "
import chardet
files = [
    r'Shared bicycle data\Shared bicycle data\Electronic fence data\STATION.csv',
    r'Shared bicycle data\Shared bicycle data\Weather data\weather.csv',
    r'Shared bicycle data\Shared bicycle data\Order data\order data.csv'
]
for f in files:
    with open(f, 'rb') as fp:
        print(f.split('\\')[-1], '->', chardet.detect(fp.read(10000)))
"


根据输出结果，在后续脚本中对应设置 encoding 参数（utf-8-sig / gb18030 / utf-8）。

步骤 4：启动 Kafka 生产者（数据回放）

cd producer
python kafka_producer.py


生产者做的事：
1. 读取 order data.csv（22万条订单事件）
2. 按 UPDATE_TIME 排序
3. 逐条/批量发送 JSON 到 Kafka bike_events topic
4. 每条消息格式：
{
  "bicycle_id": "BICYCLE_1",
  "latitude": 24.509607,
  "longitude": 118.155216,
  "lock_status": 0,
  "update_time": "2020-12-22 08:00:44",
  "date": "2020-12-22",
  "hour": 8
}


验证发送：
docker exec -it kafka /usr/bin/kafka-console-consumer ^
  --topic bike_events --bootstrap-server localhost:9092 ^
  --from-beginning --max-messages 3


步骤 5：启动 Spark Structured Streaming 消费者

cd ..
spark-submit ^
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.9,com.mysql:mysql-connector-j:8.0.33 ^
  spark/streaming_consumer.py


消费者做的事：
1. 从 Kafka 读取 JSON 流
2. 加载 STATION.csv 围栏数据，解析每个围栏的4个角点坐标，构建多边形
3. 将每条订单事件匹配到对应围栏（点在多边形内判断）
4. 按 30秒微批 + 30分钟滚动窗口 聚合：
   • LOCK_STATUS=1 → return_count + 1

   • LOCK_STATUS=0 → borrow_count + 1

   • net_flow = return_count - borrow_count

5. 同时加载天气数据，按小时对齐到聚合结果
6. 写入 MySQL bike_agg_results 表

关键参数：
• 窗口：30分钟滚动（无滑动）

• 触发器：30秒微批

• 围栏匹配：Shapely Point.within(Polygon)

• 行程过滤：同一 BICYCLE_ID 相邻事件时间差 ∈ [60, 3600] 秒

步骤 6：验证 MySQL 有数据写入

SELECT fence_id, window_start, net_flow, return_count, borrow_count
FROM bike_agg_results
ORDER BY window_start
LIMIT 20;


步骤 7：启动 FastAPI 后端（WebSocket 推送）

uvicorn api.main:app --reload --port 8000


后端做的事：
1. 提供 REST API：GET /api/agg?date=2020-12-22&hour=8 查询 MySQL 聚合结果
2. WebSocket 端点：ws://localhost:8000/ws/tidal
   • 前端连接后，后端轮询 MySQL 最新数据，推送给前端

   • 推送格式：
[{
  "fence_id": "FENCE_001",
  "latitude": 24.5096,
  "longitude": 118.1552,
  "net_flow": 12,
  "return_count": 45,
  "borrow_count": 33,
  "window_start": "2020-12-22T08:00:00",
  "weather_temp": 18.5,
  "weather_rain": 0
}]


步骤 8：前端动态大屏


frontend/
├── index.html        # 主页面：地图 + 图表 + 控制面板
├── css/style.css     # 布局样式
└── js/
    ├── map.js        # Leaflet 地图初始化 + 围栏热力图层
    ├── chart.js      # ECharts 时间序列 + 天气柱状图
    ├── websocket.js  # WebSocket 连接 + 数据更新逻辑
    └── playback.js   # 回放控制（播放/暂停/速度/进度条）


前端做的事：
1. 页面加载时连接 ws://localhost:8000/ws/tidal
2. 接收围栏级净流入数据，在 Leaflet 地图上渲染：
   • 每个围栏用一个圆形标记

   • 颜色：红（淤积）→ 白（平衡）→ 蓝（缺车）

   • 半径：正比于 net_flow

3. ECharts 绘制全城潮汐指数随时间变化的折线图
4. 侧边面板显示当前小时天气信息 + 天气对比图
5. 底部回放控制栏：
   • ▶ 播放 / ⏸ 暂停

   • 速度调节：1x / 2x / 5x（控制 WebSocket 请求频率）

   • 时间进度条：显示当前回放到的日期+时段

六、文件结构总览


bike-stream/
├── README.md
├── Shared bicycle data/
│   └── Shared bicycle data/
│       ├── Order data/
│       │   └── order data.csv
│       ├── Electronic fence data/
│       │   └── STATION.csv
│       └── Weather data/
│           └── weather.csv
├── producer/
│   └── kafka_producer.py
├── spark/
│   └── streaming_consumer.py
├── api/
│   └── main.py
└── frontend/
    ├── index.html
    ├── css/style.css
    └── js/
        ├── map.js
        ├── chart.js
        ├── websocket.js
        └── playback.js


七、完整启动顺序

推荐用 scripts/ 下的三个脚本，环境变量路径已按本机配好，开箱即用。
全程需要 4 个终端窗口，按顺序启动：


# ── 终端 1：Docker（Kafka + MySQL + Redis + Zookeeper）
docker compose up -d
docker ps          # 确认 4 个容器都是 Up 状态


# ── 终端 2：Spark 消费端（先启动，再投数据，避免丢首批）
bash scripts/run_consumer.sh           # 追加 --reset 表示从头重算（清 checkpoint）
# 看到 "[START] 流任务已启动" 后再进行下一步


# ── 终端 3：Kafka 生产者（历史订单回放）
bash scripts/run_producer.sh                        # 全量 5 天
bash scripts/run_producer.sh --date 2020-12-21      # 只回放一天
bash scripts/run_producer.sh --speed 1200           # 调快回放倍速
# Kafka topic 若不存在会自动创建，无需手动执行 kafka-topics


# ── 终端 4：FastAPI 后端（REST + WebSocket + 托管前端）
bash scripts/run_api.sh


# ── 浏览器
http://127.0.0.1:8000


生产者在推送的同时，终端 2 的 Spark 会每 30 秒一个微批写出聚合结果，
终端 4 的接口随即能查到，前端大屏点「播放」即可看到围栏颜色随时间演变。


八、只想看效果 / 数据库已有数据时的最简启动

如果 MySQL 的 bike_agg_results 表已经有数据（历史跑过流任务），
不需要重跑 Kafka 和 Spark，直接两步就能看到大屏：

    docker compose up -d
    bash scripts/run_api.sh
    # 浏览器打开 http://127.0.0.1:8000

想清空历史结果重来：

    docker exec -i mysql mysql -uroot -p bike_db \
        -e "TRUNCATE TABLE bike_agg_results; TRUNCATE TABLE bike_weather_effect;"
    rm -rf checkpoint/agg        # 同时清掉 Spark checkpoint，否则 offset 不会回溯


九、常见问题

1) Git Bash 里执行 spark-submit 报 "bad array subscript"
   spark-submit.cmd 是批处理脚本，Git Bash 调不了。
   scripts/run_consumer.sh 已改为直接用 java 启动 org.apache.spark.deploy.SparkSubmit，
   效果等价。在 PowerShell / CMD 里可以直接用 spark-submit。

2) 前端地图瓦片空白
   检查网络能否访问瓦片服务。map.js 里默认的 CartoDB 瓦片源在国内可能不稳，
   可替换为高德：https://webrd01.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}
   前端依赖库（Leaflet / ECharts）已下载到 frontend/vendor/，不依赖 CDN。

3) Spark 报中文乱码 / UnicodeEncodeError
   脚本里已设置 PYTHONIOENCODING=utf-8 与 -Dfile.encoding=UTF-8，勿删。

4) MySQL 报 "Unknown column" 类错误
   说明建表脚本更新过但旧表没重建，执行一次：
   docker exec -i mysql mysql -uroot -p < mysql/init/01_schema.sql

5) 端口 8000 被占用
   上一个 uvicorn 实例没退出。关掉占用进程再启动，或换端口：
   bash scripts/run_api.sh --port 8001


十、接口一览

GET  /api/fences            围栏列表（含中心点坐标）
GET  /api/windows           已有数据的时间窗口列表
GET  /api/agg?window=...    指定窗口的围栏级净流入数据
GET  /api/agg/latest        最新窗口数据
GET  /api/series            全城潮汐时序（潮汐指数 E(t)）
GET  /api/weather?window=   当前窗口天气 + 天气效应对比
GET  /api/state             流任务进度
GET  /api/health            健康检查
WS   /ws/tidal              新窗口到达时推送给前端