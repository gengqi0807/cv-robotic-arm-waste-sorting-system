"""MySQL 配置加载和 PyMySQL 连接。"""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any

import pymysql


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "db" / "config.ini"


def load_db_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """读取 INI 配置，并允许 MYSQL_* 环境变量覆盖。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    parser = configparser.ConfigParser()
    if path.is_file():
        parser.read(path, encoding="utf-8")
    file_config = parser["mysql"] if parser.has_section("mysql") else {}

    host = os.getenv("MYSQL_HOST", file_config.get("host", "127.0.0.1"))
    ssl_disabled_value = os.getenv(
        "MYSQL_SSL_DISABLED",
        file_config.get("ssl_disabled", "auto"),
    ).strip().lower()
    if ssl_disabled_value == "auto":
        ssl_disabled = host.lower() in {"127.0.0.1", "localhost", "::1"}
    elif ssl_disabled_value in {"true", "1", "yes", "on"}:
        ssl_disabled = True
    elif ssl_disabled_value in {"false", "0", "no", "off"}:
        ssl_disabled = False
    else:
        raise ValueError("ssl_disabled 必须是 auto、true 或 false。")

    config: dict[str, Any] = {
        "host": host,
        "port": int(os.getenv("MYSQL_PORT", file_config.get("port", "3306"))),
        "user": os.getenv("MYSQL_USER", file_config.get("user", "")),
        "password": os.getenv("MYSQL_PASSWORD", file_config.get("password", "")),
        "database": os.getenv("MYSQL_DATABASE", file_config.get("database", "garbage_sorting")),
        "charset": os.getenv("MYSQL_CHARSET", file_config.get("charset", "utf8mb4")),
        "ssl_disabled": ssl_disabled,
    }
    if config["database"] != "garbage_sorting":
        raise ValueError("MYSQL_DATABASE 必须为 garbage_sorting。")
    return config


def get_connection(config: dict[str, Any] | None = None) -> Any:
    """建立启用字典游标的 MySQL 连接。"""
    settings = config or load_db_config()
    return pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=settings["database"],
        charset=settings["charset"],
        ssl_disabled=settings["ssl_disabled"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )