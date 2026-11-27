CREATE DATABASE IF NOT EXISTS `garbage_sorting`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `garbage_sorting`;

CREATE TABLE IF NOT EXISTS `role` (
  `role` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`role`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='角色枚举：operator、manager、maintainer';

INSERT INTO `role` (`role`) VALUES ('operator'), ('manager'), ('maintainer')
  ON DUPLICATE KEY UPDATE `role` = VALUES(`role`);

CREATE TABLE IF NOT EXISTS `user` (
  `user_id` VARCHAR(64) NOT NULL,
  `operator_id` VARCHAR(64) NOT NULL,
  `name` VARCHAR(128) NOT NULL,
  `role` VARCHAR(32) NOT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'active',
  `password_hash` VARCHAR(255) NULL,
  `badge_code` VARCHAR(64) NULL,
  `created_at` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`user_id`),
  UNIQUE KEY `uq_user_operator_id` (`operator_id`),
  KEY `idx_user_role` (`role`),
  KEY `idx_user_status` (`status`),
  CONSTRAINT `fk_user_role` FOREIGN KEY (`role`) REFERENCES `role` (`role`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='用户账号信息；不保存人脸照片或模板';

ALTER TABLE `user` MODIFY `password_hash` VARCHAR(255) NULL;

CREATE TABLE IF NOT EXISTS `session` (
  `session_id` VARCHAR(64) NOT NULL,
  `operator_id` VARCHAR(64) NOT NULL,
  `role` VARCHAR(32) NOT NULL,
  `expires_at_ms` BIGINT UNSIGNED NOT NULL,
  `created_at` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`session_id`),
  KEY `idx_session_operator_id` (`operator_id`),
  KEY `idx_session_expires_at_ms` (`expires_at_ms`),
  CONSTRAINT `fk_session_operator_id` FOREIGN KEY (`operator_id`) REFERENCES `user` (`operator_id`),
  CONSTRAINT `fk_session_role` FOREIGN KEY (`role`) REFERENCES `role` (`role`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='登录会话；按 expires_at_ms 过期';

CREATE TABLE IF NOT EXISTS `auth_log` (
  `log_id` VARCHAR(64) NOT NULL,
  `operator_id` VARCHAR(64) NULL,
  `auth_result` VARCHAR(32) NOT NULL,
  `confidence` DOUBLE NULL,
  `timestamp_ms` BIGINT UNSIGNED NOT NULL,
  `terminal_id` VARCHAR(128) NOT NULL,
  `error_code` VARCHAR(64) NULL,
  PRIMARY KEY (`log_id`),
  KEY `idx_auth_log_operator_id` (`operator_id`),
  KEY `idx_auth_log_timestamp_ms` (`timestamp_ms`),
  CONSTRAINT `fk_auth_log_operator_id` FOREIGN KEY (`operator_id`) REFERENCES `user` (`operator_id`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='人脸/密码认证审计日志；不保存人脸照片或模板';

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

CREATE TABLE IF NOT EXISTS `expression_config` (
  `operator_id` VARCHAR(64) NOT NULL,
  `enabled` TINYINT(1) NOT NULL DEFAULT 0,
  `authorized` TINYINT(1) NOT NULL DEFAULT 0,
  `action_id` VARCHAR(64) NOT NULL DEFAULT 'smile_wave',
  `updated_at` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`operator_id`),
  CONSTRAINT `fk_expression_config_operator_id` FOREIGN KEY (`operator_id`) REFERENCES `user` (`operator_id`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='表情互动配置；不保存情绪和人脸照片';

CREATE TABLE IF NOT EXISTS `expression_log` (
  `log_id` VARCHAR(64) NOT NULL,
  `operator_id` VARCHAR(64) NOT NULL,
  `action_id` VARCHAR(64) NOT NULL,
  `triggered_at_ms` BIGINT UNSIGNED NOT NULL,
  `result` VARCHAR(128) NOT NULL,
  PRIMARY KEY (`log_id`),
  KEY `idx_expression_log_operator_id` (`operator_id`),
  KEY `idx_expression_log_triggered_at_ms` (`triggered_at_ms`),
  CONSTRAINT `fk_expression_log_operator_id` FOREIGN KEY (`operator_id`) REFERENCES `user` (`operator_id`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='表情互动安全验证日志；不记录情绪字段，不保存人脸照片';
