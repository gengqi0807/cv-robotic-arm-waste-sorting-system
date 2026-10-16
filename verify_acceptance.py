"""使用 Flask test_client 对分拣事件 API 执行验收测试。"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import io
from pathlib import Path
import sys
from typing import Any, Callable
from uuid import uuid4

from app import EVENT_FIELDS, create_app
from database import DEFAULT_CONFIG_PATH, get_connection, load_db_config


class AcceptanceVerifier:
    """执行接口验收并清理本次创建的测试记录。"""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_db_config(config_path)
        self.app = create_app({"TESTING": True, "DB_CONFIG_PATH": config_path})
        self.client = self.app.test_client()
        self.run_id = uuid4().hex
        self.event_prefix = f"acceptance_{self.run_id}"
        self.task_prefix = f"acceptance_task_{self.run_id}"
        self.base_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.first_batch_count = 30
        self.bulk_batch_count = 600
        self.results: list[dict[str, Any]] = []

    def make_event(self, batch: str, index: int, timestamp_ms: int) -> dict[str, Any]:
        """构造满足 event 接口契约的测试事件。"""
        return {
            "event_id": f"{self.event_prefix}-{batch}-{index:04d}",
            "task_id": f"{self.task_prefix}-{batch}",
            "timestamp_ms": timestamp_ms,
            "class_id": ("recyclable", "kitchen", "hazardous", "other")[index % 4],
            "confidence": 0.95,
            "u_px": float(100 + index),
            "v_px": float(200 + index),
            "target_x_m": 0.1,
            "target_y_m": 0.2,
            "target_z_m": 0.03,
            "result": "success",
            "error_code": None,
            "duration_ms": 500,
            "calibration_id": "acceptance-calibration",
        }

    def add_result(self, name: str, expected: str, actual: str, passed: bool) -> None:
        self.results.append(
            {"name": name, "expected": expected, "actual": actual, "passed": passed}
        )

    def safely_run(self, name: str, check: Callable[[], None]) -> None:
        """单项异常记为失败后继续执行其他检查。"""
        try:
            check()
        except Exception as error:
            self.add_result(
                name,
                "检查按预期完成",
                f"异常：{type(error).__name__}: {error}",
                False,
            )

    def check_write_30(self) -> None:
        succeeded = 0
        failures: list[str] = []
        for index in range(self.first_batch_count):
            payload = self.make_event("first", index, self.base_time_ms + index)
            response = self.client.post("/api/events", json=payload)
            if response.status_code == 201 and response.json.get("event_id") == payload["event_id"]:
                succeeded += 1
            else:
                failures.append(f"{payload['event_id']} -> HTTP {response.status_code}")
        self.add_result(
            "1. 连续写入30条不同记录",
            "30/30 写入成功",
            f"{succeeded}/30 成功" + (f"；失败示例：{failures[0]}" if failures else ""),
            succeeded == self.first_batch_count,
        )

    def check_duplicate(self) -> None:
        payload = self.make_event("first", 0, self.base_time_ms)
        response = self.client.post("/api/events", json=payload)
        connection = get_connection(self.config)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS `row_count` FROM `event` WHERE `event_id` = %s",
                    (payload["event_id"],),
                )
                row_count = int(cursor.fetchone()["row_count"])
        finally:
            connection.close()
        passed = response.status_code == 200 and row_count == 1
        self.add_result(
            "2. 重复 event_id 幂等",
            "重复提交不新增记录，表中该 event_id 恰好1条",
            f"HTTP {response.status_code}；event_id 行数={row_count}",
            passed,
        )

    def get_all_pages(self, start_time_ms: int, end_time_ms: int) -> tuple[list[dict[str, Any]], int]:
        """按 API 支持的最大页大小读取时间范围内的全部最近记录。"""
        items: list[dict[str, Any]] = []
        page = 1
        total = 0
        while True:
            response = self.client.get(
                "/api/events",
                query_string={
                    "start_time_ms": start_time_ms,
                    "end_time_ms": end_time_ms,
                    "page": page,
                    "page_size": 100,
                },
            )
            if response.status_code != 200:
                raise AssertionError(f"查询第{page}页失败：HTTP {response.status_code} {response.get_data(as_text=True)}")
            body = response.get_json()
            items.extend(body["items"])
            total = int(body["pagination"]["total"])
            if page >= int(body["pagination"]["total_pages"]):
                break
            page += 1
        return items, total

    def check_recent_query(self) -> None:
        end_time_ms = self.base_time_ms + self.first_batch_count
        items, total = self.get_all_pages(self.base_time_ms, end_time_ms)
        timestamps = [int(item["timestamp_ms"]) for item in items]
        descending = timestamps == sorted(timestamps, reverse=True)
        passed = descending and len(items) <= 500 and total == self.first_batch_count
        self.add_result(
            "3. 最近记录倒序及数量上限",
            "按 timestamp_ms 倒序且最多500条",
            f"本时间窗返回{len(items)}条；总数={total}；倒序={descending}",
            passed,
        )

    def check_bulk_limit(self) -> None:
        start_time_ms = self.base_time_ms + 1000
        columns = ", ".join(f"`{field}`" for field in EVENT_FIELDS)
        placeholders = ", ".join(["%s"] * len(EVENT_FIELDS))
        insert_sql = f"INSERT INTO `event` ({columns}) VALUES ({placeholders})"
        payloads = [
            self.make_event("bulk", index, start_time_ms + index)
            for index in range(self.bulk_batch_count)
        ]
        connection = get_connection(self.config)
        try:
            with connection.cursor() as cursor:
                cursor.executemany(
                    insert_sql,
                    [tuple(payload[field] for field in EVENT_FIELDS) for payload in payloads],
                )
                inserted_count = cursor.rowcount
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        items, total = self.get_all_pages(
            start_time_ms,
            start_time_ms + self.bulk_batch_count,
        )
        passed = (
            inserted_count == self.bulk_batch_count
            and total == 500
            and len(items) == 500
            and len({item["event_id"] for item in items}) == 500
        )
        self.add_result(
            "4. 600条记录查询500条上限",
            "批量准备600条；test_client 查询返回最近500条",
            f"批量写入{inserted_count}/600；分页读取{len(items)}条；接口total={total}条",
            passed,
        )

    def check_stats_match_details(self) -> None:
        end_time_ms = self.base_time_ms + self.first_batch_count
        details, detail_total = self.get_all_pages(self.base_time_ms, end_time_ms)
        response = self.client.get(
            "/api/stats",
            query_string={
                "start_time_ms": self.base_time_ms,
                "end_time_ms": end_time_ms,
            },
        )
        if response.status_code != 200:
            raise AssertionError(f"统计请求失败：HTTP {response.status_code} {response.get_data(as_text=True)}")
        stats_total = int(response.get_json()["total_count"])
        passed = stats_total == detail_total == len(details) == self.first_batch_count
        self.add_result(
            "5. 统计总数与明细总数一致",
            "统计总数等于同一时间窗内明细条数",
            f"统计总数={stats_total}；明细条数={len(details)}；分页total={detail_total}",
            passed,
        )

    def check_required_fields(self) -> None:
        connection = get_connection(self.config)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW COLUMNS FROM `event`")
                columns = cursor.fetchall()
                required_columns = [row for row in columns if row["Null"] == "NO"]
                if not required_columns:
                    raise AssertionError("event 表未检测到 NOT NULL 必填字段。")

                aggregates = ["COUNT(*) AS `total_rows`"]
                for index, column in enumerate(required_columns):
                    field = column["Field"]
                    data_type = column["Type"].lower().split("(", maxsplit=1)[0]
                    nonempty_check = f"`{field}` IS NOT NULL"
                    if data_type in {"char", "varchar", "tinytext", "text", "mediumtext", "longtext", "enum", "set"}:
                        nonempty_check += f" AND TRIM(`{field}`) <> ''"
                    aggregates.append(
                        f"SUM(CASE WHEN {nonempty_check} THEN 1 ELSE 0 END) AS `nonnull_{index}`"
                    )
                cursor.execute(f"SELECT {', '.join(aggregates)} FROM `event`")
                row = cursor.fetchone()
        finally:
            connection.close()

        total_rows = int(row["total_rows"] or 0)
        field_rates: list[tuple[str, float]] = []
        present_cells = 0
        for index, column in enumerate(required_columns):
            field = column["Field"]
            present = int(row[f"nonnull_{index}"] or 0)
            rate = (present / total_rows * 100.0) if total_rows else 100.0
            field_rates.append((field, rate))
            present_cells += present
        total_cells = total_rows * len(required_columns)
        overall_rate = (present_cells / total_cells * 100.0) if total_cells else 100.0
        details = ", ".join(f"{field}={rate:.2f}%" for field, rate in field_rates)
        passed = overall_rate >= 99.0 and all(rate >= 99.0 for _, rate in field_rates)
        self.add_result(
            "6. event必填字段非空率",
            "整体及各必填字段非空率至少99%",
            f"扫描{total_rows}条、{len(required_columns)}个必填字段；整体={overall_rate:.2f}%；{details}",
            passed,
        )

    def check_csv_export(self) -> None:
        start_time_ms = self.base_time_ms + 1000
        response = self.client.get(
            "/api/events/export",
            query_string={
                "start_time_ms": start_time_ms,
                "end_time_ms": start_time_ms + self.bulk_batch_count,
            },
        )
        content_type = response.content_type or ""
        text = response.get_data(as_text=True).lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(text)))
        header = rows[0] if rows else []
        has_contract_header = all(field in header for field in EVENT_FIELDS)
        data_row_count = max(0, len(rows) - 1)
        passed = (
            response.status_code == 200
            and content_type.startswith("text/csv")
            and has_contract_header
            and data_row_count >= 1
        )
        self.add_result(
            "7. CSV导出",
            "text/csv；包含契约列头和至少1条数据",
            f"HTTP {response.status_code}；类型={content_type}；列头完整={has_contract_header}；数据行={data_row_count}",
            passed,
        )

    def cleanup(self) -> None:
        connection = get_connection(self.config)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM `event` "
                    "WHERE LEFT(`event_id`, CHAR_LENGTH(%s)) = %s "
                    "OR LEFT(`task_id`, CHAR_LENGTH(%s)) = %s",
                    (self.event_prefix, self.event_prefix, self.task_prefix, self.task_prefix),
                )
                deleted = cursor.rowcount
                connection.commit()
                cursor.execute(
                    "SELECT COUNT(*) AS `remaining` FROM `event` "
                    "WHERE LEFT(`event_id`, CHAR_LENGTH(%s)) = %s "
                    "OR LEFT(`task_id`, CHAR_LENGTH(%s)) = %s",
                    (self.event_prefix, self.event_prefix, self.task_prefix, self.task_prefix),
                )
                remaining = int(cursor.fetchone()["remaining"])
        finally:
            connection.close()
        self.add_result(
            "8. 测试数据清理",
            "删除本次全部测试记录，剩余0条",
            f"删除{deleted}条；剩余{remaining}条",
            remaining == 0,
        )

    def run(self) -> int:
        checks = (
            ("1. 连续写入30条不同记录", self.check_write_30),
            ("2. 重复 event_id 幂等", self.check_duplicate),
            ("3. 最近记录倒序及数量上限", self.check_recent_query),
            ("4. 600条记录查询500条上限", self.check_bulk_limit),
            ("5. 统计总数与明细总数一致", self.check_stats_match_details),
            ("6. event必填字段非空率", self.check_required_fields),
            ("7. CSV导出", self.check_csv_export),
        )
        try:
            for name, check in checks:
                self.safely_run(name, check)
        finally:
            try:
                self.cleanup()
            except Exception as error:
                self.add_result(
                    "8. 测试数据清理",
                    "删除本次全部测试记录，剩余0条",
                    f"清理失败：{type(error).__name__}: {error}",
                    False,
                )
            self.print_summary()
        return 0 if all(item["passed"] for item in self.results) else 1

    def print_summary(self) -> None:
        print("\n验收测试结果汇总")
        print("=" * 72)
        for item in self.results:
            status = "通过" if item["passed"] else "失败"
            print(f"[{status}] {item['name']}")
            print(f"  预期：{item['expected']}")
            print(f"  实际：{item['actual']}")
        passed_count = sum(item["passed"] for item in self.results)
        print("=" * 72)
        print(f"合计：{passed_count}/{len(self.results)} 项通过")


def main() -> int:
    parser = argparse.ArgumentParser(description="验收分拣事件保存、查询、统计和导出接口。")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"MySQL 配置文件（默认：{DEFAULT_CONFIG_PATH}）",
    )
    args = parser.parse_args()

    try:
        verifier = AcceptanceVerifier(args.config)
    except Exception as error:
        print(f"无法启动验收测试：{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return verifier.run()


if __name__ == "__main__":
    raise SystemExit(main())