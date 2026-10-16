CREATE DATABASE IF NOT EXISTS `garbage_sorting`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `garbage_sorting`;

CREATE TABLE IF NOT EXISTS `event` (
  `event_id` VARCHAR(64) NOT NULL,
  `task_id` VARCHAR(64) NOT NULL,
  `timestamp_ms` BIGINT UNSIGNED NOT NULL,
  `class_id` VARCHAR(32) NOT NULL,
  `confidence` DECIMAL(5, 4) NOT NULL,
  `u_px` DOUBLE NOT NULL,
  `v_px` DOUBLE NOT NULL,
  `target_x_m` DOUBLE NULL,
  `target_y_m` DOUBLE NULL,
  `target_z_m` DOUBLE NULL,
  `result` VARCHAR(64) NOT NULL,
  `error_code` VARCHAR(64) NULL,
  `duration_ms` INT UNSIGNED NOT NULL,
  `calibration_id` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`event_id`),
  KEY `idx_event_timestamp_ms` (`timestamp_ms`),
  KEY `idx_event_task_id` (`task_id`),
  KEY `idx_event_timestamp_class` (`timestamp_ms`, `class_id`),
  KEY `idx_event_timestamp_result` (`timestamp_ms`, `result`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='对应接口 event';