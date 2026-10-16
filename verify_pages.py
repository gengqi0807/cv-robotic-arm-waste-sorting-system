"""测量 FR-03 页面依赖的记录显示、持久性和统计一致性指标。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import math
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from app import EVENT_FIELDS, create_app
from database import DEFAULT_CONFIG_PATH, get_connection, load_db_config


class PagesAcceptanceVerifier:
    """通过 Flask test_client 和真实 MySQL 验证 FR-03 硬指标。"""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_db_config(config_path)
        self.app = create_app({"TESTING": True, "DB_CONFIG_PATH": config_path})
        self.client = self.app.test_client()
        self.run_id = uuid4().hex
        self.event_prefix = f"pages_{self.run_id}"
        self.task_prefix = f"pages_task_{self.run_id}"
        self.base_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.latency_limit_ms = 1000.0
        self.results: list[dict[str, Any]] = []

    def make_event(self, batch: str, index: int, timestamp_ms: int) -> dict[str, Any]:
        """构造带唯一前缀且符合 event 契约的数据。"""
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
            "result": "success" if index % 2 == 0 else "failed",
            "error_code": None if index % 2 == 0 else "TEST_FAILURE",
            "duration_ms": 500,
            "calibration_id": "pages-acceptance-calibration",
        }

    def record_result(self, name: str, expected: str, actual: str, passed: bool) -> None:
        self.results.append(
            {"name": name, "expected": expected, "actual": actual, "passed": passed}
        )

    def run_check(self, name: str, check: Callable[[], None]) -> None:
        try:
            check()
        except Exception as error:
            self.record_result(
                name,
                "检查按预期完成",
                f"异常：{type(error).__name__}: {error}",
                False,
            )

    def measure_display_latency(self, batch: str, timestamp_offset: int) -> dict[str, Any]:
        visible_count = 0
        latencies_ms: list[float] = []
        failures: list[str] = []

        for index in range(30):
            payload = self.make_event(
                batch,
                index,
                self.base_time_ms + timestamp_offset + index,
            )
            started = perf_counter()
            post_response = self.client.post("/api/events", json=payload)
            query_response = self.client.get(
                "/api/events",
                query_string={
                    "start_time_ms": payload["timestamp_ms"],
                    "end_time_ms": payload["timestamp_ms"] + 1,
                    "class_id": payload["class_id"],
                    "result": payload["result"],
                    "page": 1,
                    "page_size": 100,
                },
            )
            latency_ms = (perf_counter() - started) * 1000.0
            latencies_ms.append(latency_ms)

            if post_response.status_code not in (200, 201):
                failures.append(f"POST {post_response.status_code}")
                continue
            if query_response.status_code != 200:
                failures.append(f"GET {query_response.status_code}")
                continue
            body = query_response.get_json()
            if any(item["event_id"] == payload["event_id"] for item in body["items"]):
                visible_count += 1
            else:
                failures.append(f"{payload['event_id']} 未查到")

        under_limit = sum(latency <= self.latency_limit_ms for latency in latencies_ms)
        percentage = under_limit / 30 * 100.0
        p95_ms = sorted(latencies_ms)[math.ceil(0.95 * len(latencies_ms)) - 1]
        required_count = math.ceil(30 * 0.95)
        return {
            "visible_count": visible_count,
            "under_limit": under_limit,
            "percentage": percentage,
            "p95_ms": p95_ms,
            "passed": visible_count == 30 and under_limit >= required_count,
            "failure": failures[0] if failures else None,
        }

    def check_display_latency(self) -> None:
        first = self.measure_display_latency("latency_a", 0)
        second = self.measure_display_latency("latency_b", 100)
        p95_delta_ms = abs(first["p95_ms"] - second["p95_ms"])
        stable = p95_delta_ms <= 50.0
        passed = first["passed"] and second["passed"] and stable
        actual = (
            f"第1轮：可见{first['visible_count']}/30，1秒内{first['under_limit']}/30"
            f"（{first['percentage']:.2f}%），P95={first['p95_ms']:.2f}ms；"
            f"第2轮：可见{second['visible_count']}/30，1秒内{second['under_limit']}/30"
            f"（{second['percentage']:.2f}%），P95={second['p95_ms']:.2f}ms；"
            f"P95波动={p95_delta_ms:.2f}ms（{'稳定' if stable else '超过50ms'}）"
        )
        failures = [first["failure"], second["failure"]]
        failure = next((item for item in failures if item), None)
        if failure:
            actual += f"；异常示例：{failure}"
        self.record_result(
            "1. 分拣记录1秒内显示",
            "两轮各30条均可查且至少29/30在1000ms内可见；两次P95波动不超过50ms",
            actual,
            passed,
        )

    def read_window(self, client: Any, start_ms: int, end_ms: int) -> tuple[list[dict[str, Any]], int]:
        """使用 API 分页读取一个时间窗口，页大小取接口允许的最大值。"""
        items: list[dict[str, Any]] = []
        page = 1
        total = 0
        while True:
            response = client.get(
                "/api/events",
                query_string={
                    "start_time_ms": start_ms,
                    "end_time_ms": end_ms,
                    "page": page,
                    "page_size": 100,
                },
            )
            if response.status_code != 200:
                raise AssertionError(
                    f"GET /api/events 第{page}页失败：HTTP {response.status_code}"
                )
            body = response.get_json()
            items.extend(body["items"])
            total = int(body["pagination"]["total"])
            if page >= int(body["pagination"]["total_pages"]):
                break
            page += 1
        return items, total

    def insert_bulk(self, batch: str, start_ms: int, count: int) -> int:
        """用 executemany 快速准备大量持久性验收数据。"""
        columns = ", ".join(f"`{field}`" for field in EVENT_FIELDS)
        placeholders = ", ".join(["%s"] * len(EVENT_FIELDS))
        statement = f"INSERT INTO `event` ({columns}) VALUES ({placeholders})"
        payloads = [self.make_event(batch, index, start_ms + index) for index in range(count)]
        connection = get_connection(self.config)
        try:
            with connection.cursor() as cursor:
                cursor.executemany(
                    statement,
                    [tuple(payload[field] for field in EVENT_FIELDS) for payload in payloads],
                )
                inserted = cursor.rowcount
            connection.commit()
            return inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def check_persistence(self) -> None:
        start_ms = self.base_time_ms + 1000
        end_ms = start_ms + 600
        inserted = self.insert_bulk("persist", start_ms, 600)
        before_items, before_total = self.read_window(self.client, start_ms, end_ms)

        # 使用全新 Flask app 和 test_client 模拟服务重启后的重新接入。
        restarted_app = create_app({"TESTING": True, "DB_CONFIG_PATH": self.config_path})
        restarted_client = restarted_app.test_client()
        after_items, after_total = self.read_window(restarted_client, start_ms, end_ms)

        passed = (
            inserted == 600
            and before_total == 500
            and len(before_items) == 500
            and after_total == 500
            and len(after_items) == 500
            and {row["event_id"] for row in before_items}
            == {row["event_id"] for row in after_items}
        )
        self.record_result(
            "2. 500条查询与重建 app 后持久性",
            "批量写入600条；重建 app 前后接口total及返回条数均为500",
            f"写入={inserted}/600；重建前total/返回={before_total}/{len(before_items)}；"
            f"重建后total/返回={after_total}/{len(after_items)}；记录集合一致="
            f"{ {row['event_id'] for row in before_items} == {row['event_id'] for row in after_items} }",
            passed,
        )

    def check_stats_consistency(self) -> None:
        start_ms = self.base_time_ms + 2000
        end_ms = start_ms + 30
        inserted = 0
        failed_posts: list[str] = []
        for index in range(30):
            payload = self.make_event("stats", index, start_ms + index)
            response = self.client.post("/api/events", json=payload)
            if response.status_code == 201 and response.get_json().get("event_id") == payload["event_id"]:
                inserted += 1
            else:
                failed_posts.append(f"{payload['event_id']} HTTP {response.status_code}")

        detail_rows, detail_total = self.read_window(self.client, start_ms, end_ms)
        stats_response = self.client.get(
            "/api/stats",
            query_string={"start_time_ms": start_ms, "end_time_ms": end_ms},
        )
        if stats_response.status_code != 200:
            raise AssertionError(f"GET /api/stats 失败：HTTP {stats_response.status_code}")
        stats = stats_response.get_json()

        detail_categories = {
            class_id: sum(row["class_id"] == class_id for row in detail_rows)
            for class_id in ("recyclable", "kitchen", "hazardous", "other")
        }
        detail_success = sum(row["result"] == "success" for row in detail_rows)
        detail_failure = sum(row["result"] != "success" for row in detail_rows)
        category_checks = {
            class_id: int(stats["categories"][class_id]["count"]) == count
            for class_id, count in detail_categories.items()
        }
        total_matches = int(stats["total_count"]) == detail_total == len(detail_rows)
        success_matches = int(stats["success_count"]) == detail_success
        failure_matches = int(stats["failure_count"]) == detail_failure
        passed = (
            inserted == 30
            and total_matches
            and all(category_checks.values())
            and success_matches
            and failure_matches
        )
        actual = (
            f"写入={inserted}/30；总数统计/明细={stats['total_count']}/{detail_total}"
            f"({'一致' if total_matches else '不一致'})；类别统计/明细={detail_categories} / "
            f"{ {key: int(stats['categories'][key]['count']) for key in detail_categories} }"
            f"（逐类一致={category_checks}）；成功统计/明细={stats['success_count']}/{detail_success}"
            f"（{'一致' if success_matches else '不一致'}）；失败统计/明细={stats['failure_count']}/{detail_failure}"
            f"（{'一致' if failure_matches else '不一致'}）"
        )
        if failed_posts:
            actual += f"；POST异常示例：{failed_posts[0]}"
        self.record_result(
            "3. 统计与明细一致性",
            "总数、四类数量、成功数和失败数均与30条窗口明细一致",
            actual,
            passed,
        )

    def cleanup(self) -> tuple[int, int]:
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
            return deleted, remaining
        finally:
            connection.close()

    def print_summary(self) -> None:
        print("\nFR-03 页面验收结果")
        print("=" * 72)
        for result in self.results:
            status = "通过" if result["passed"] else "失败"
            print(f"[{status}] {result['name']}")
            print(f"  预期：{result['expected']}")
            print(f"  实际：{result['actual']}")
        passed_count = sum(result["passed"] for result in self.results)
        print("=" * 72)
        print(f"合计：{passed_count}/{len(self.results)} 项通过")

    def run(self) -> int:
        checks = (
            ("1. 分拣记录1秒内显示", self.check_display_latency),
            ("2. 500条查询与重建 app 后持久性", self.check_persistence),
            ("3. 统计与明细一致性", self.check_stats_consistency),
        )
        try:
            for name, check in checks:
                self.run_check(name, check)
        finally:
            try:
                deleted, remaining = self.cleanup()
                self.record_result(
                    "4. 测试数据清理",
                    "本次测试前缀记录剩余0条",
                    f"删除{deleted}条；剩余{remaining}条",
                    remaining == 0,
                )
            except Exception as error:
                self.record_result(
                    "4. 测试数据清理",
                    "本次测试前缀记录剩余0条",
                    f"清理失败：{type(error).__name__}: {error}",
                    False,
                )
            self.print_summary()
        return 0 if all(result["passed"] for result in self.results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="验收 FR-03 记录显示、查询持久性和统计一致性。")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"MySQL 配置文件（默认：{DEFAULT_CONFIG_PATH}）",
    )
    args = parser.parse_args()
    try:
        verifier = PagesAcceptanceVerifier(args.config)
    except Exception as error:
        print(f"无法启动验收测试：{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return verifier.run()


if __name__ == "__main__":
    raise SystemExit(main())