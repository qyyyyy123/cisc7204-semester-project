-- ============================================================
-- 厦门共享单车潮汐分析 - 结果表结构
-- 每次结果重跑可覆盖执行：DROP + CREATE 保证字段始终最新
-- ============================================================
CREATE DATABASE IF NOT EXISTS bike_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE bike_db;

-- 围栏级净流入聚合结果（Spark 每批次累加写入）
DROP TABLE IF EXISTS bike_agg_results;
CREATE TABLE bike_agg_results (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    fence_id            VARCHAR(64)  NOT NULL COMMENT '电子围栏ID',
    window_start        DATETIME     NOT NULL COMMENT '窗口开始',
    window_end          DATETIME     NOT NULL COMMENT '窗口结束',
    net_flow            DOUBLE       DEFAULT 0 COMMENT '净流入 = 还车 - 借车（正=淤积，负=缺车）',
    return_count        INT          DEFAULT 0 COMMENT '还车量 LOCK_STATUS=1',
    borrow_count        INT          DEFAULT 0 COMMENT '借车量 LOCK_STATUS=0',
    avg_duration        DOUBLE       DEFAULT NULL COMMENT '该窗口订单平均时长(秒)',
    weather_type        VARCHAR(32)  DEFAULT NULL COMMENT '天气类型 COCO 映射',
    weather_temp        DOUBLE       DEFAULT NULL COMMENT '温度 ℃',
    weather_rain        DOUBLE       DEFAULT NULL COMMENT '降水 mm',
    weather_visibility  DOUBLE       DEFAULT NULL COMMENT '能见度 km（由 COCO 推算，原数据无该字段）',
    hour                INT          DEFAULT NULL COMMENT '小时 6/7/8/9',
    date                DATE         DEFAULT NULL COMMENT '日期',
    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fence_window (fence_id, window_start),
    KEY idx_window (window_start),
    KEY idx_date_hour (date, hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='围栏级潮汐聚合结果';

-- 天气效应汇总（不同天气条件下的骑行量对比）
DROP TABLE IF EXISTS bike_weather_effect;
CREATE TABLE bike_weather_effect (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    weather_type   VARCHAR(32) NOT NULL COMMENT '天气类型',
    hour           INT         NOT NULL COMMENT '小时 6/7/8/9',
    avg_net_flow   DOUBLE      DEFAULT 0 COMMENT '平均净流入',
    total_borrow   INT         DEFAULT 0 COMMENT '总借车量',
    total_return   INT         DEFAULT 0 COMMENT '总还车量',
    created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_weather_hour (weather_type, hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='天气效应对比表';

-- 流处理进度（供前端读取当前回放进度）
DROP TABLE IF EXISTS bike_stream_state;
CREATE TABLE bike_stream_state (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    state_key     VARCHAR(64) NOT NULL,
    state_value   VARCHAR(128),
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_key (state_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='流任务状态';
