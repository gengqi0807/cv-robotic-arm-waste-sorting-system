"""Initialize the MySQL database and event table from init.sql."""

from __future__ import annotations

import argparse
import configparser
from pathlib import Path
import sys


DB_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = DB_DIR / "config.ini"
SCHEMA_FILE = DB_DIR / "init.sql"
EXPECTED_DATABASE = "garbage_sorting"


def load_config(config_path: Path) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"配置文件不存在：{config_path}\n"
            f"请先复制 {DB_DIR / 'config.example.ini'} 为 {config_path} 并填写 MySQL 连接信息。"
        )
    parser.read(config_path, encoding="utf-8")
    if "mysql" not in parser:
        raise ValueError("配置文件缺少 [mysql] 配置段。")

    config = parser["mysql"]
    required_keys = ("host", "port", "user", "database", "charset")
    missing = [key for key in required_keys if not config.get(key, "").strip()]
    if missing:
        raise ValueError(f"MySQL 配置缺少必填项：{', '.join(missing)}")
    if config.get("database") != EXPECTED_DATABASE:
        raise ValueError(f"database 必须与 init.sql 一致，当前固定为 {EXPECTED_DATABASE}。")
    try:
        port = config.getint("port")
    except ValueError as error:
        raise ValueError("MySQL port 必须是有效整数。") from error
    if not 1 <= port <= 65535:
        raise ValueError("MySQL port 必须在 1 到 65535 之间。")
    return config


def load_statements(schema_path: Path) -> list[str]:
    if not schema_path.is_file():
        raise FileNotFoundError(f"找不到 SQL 初始化文件：{schema_path}")
    return [statement.strip() for statement in schema_path.read_text(encoding="utf-8").split(";") if statement.strip()]


def initialize(config: configparser.SectionProxy, statements: list[str]) -> None:
    try:
        import pymysql
    except ImportError as error:
        raise RuntimeError("缺少 PyMySQL，请先运行：pip install -r requirements.txt") from error

    connection = pymysql.connect(
        host=config["host"],
        port=config.getint("port"),
        user=config["user"],
        password=config.get("password", ""),
        charset=config["charset"],
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        print(f"MySQL 初始化完成：数据库 {EXPECTED_DATABASE}，表 event。")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="创建项目 MySQL 数据库及 event 事件表。")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"MySQL 配置文件路径（默认：{DEFAULT_CONFIG}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="检查配置和 SQL 文件并列出将执行的语句，不连接 MySQL。",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        statements = load_statements(SCHEMA_FILE)
        if args.dry_run:
            print(f"配置有效：{config['host']}:{config['port']}/{config['database']}")
            for index, statement in enumerate(statements, start=1):
                print(f"\n[{index}]\n{statement};")
            return 0
        initialize(config, statements)
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
        print(f"初始化失败：{error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"MySQL 初始化失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())