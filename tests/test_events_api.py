"""事件 API 的隔离测试，不连接真实 MySQL。"""

from __future__ import annotations

import re
import unittest
from typing import Any

from app import EVENT_FIELDS, create_app


class FakeDatabase:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def connect(self) -> "FakeConnection":
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.database)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeCursor:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.rowcount = 0
        self.result: Any = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT IGNORE"):
            columns = re.search(r"\((.*?)\) VALUES", normalized)
            assert columns is not None
            field_names = [field.strip().strip("`") for field in columns.group(1).split(",")]
            row = dict(zip(field_names, params))
            if row["event_id"] not in self.database.rows:
                self.database.rows[row["event_id"]] = row
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        rows = list(self.database.rows.values())
        filter_values = list(params)
        where_match = re.search(r" WHERE (.*?) ORDER BY| WHERE (.*?)$", normalized)
        where_text = next((part for part in where_match.groups() if part is not None), "") if where_match else ""
        if "`timestamp_ms` >= %s" in where_text:
            start_time = filter_values.pop(0)
            end_time = filter_values.pop(0)
            rows = [row for row in rows if start_time <= row["timestamp_ms"] < end_time]
        if "`class_id` = %s" in where_text:
            class_id = filter_values.pop(0)
            rows = [row for row in rows if row["class_id"] == class_id]
        if "`result` = %s" in where_text:
            result = filter_values.pop(0)
            rows = [row for row in rows if row["result"] == result]

        if normalized.startswith("SELECT COUNT(*) AS `count`"):
            self.result = {"count": len(rows)}
        elif "FROM `event` WHERE `event_id` = %s" in normalized:
            self.result = self.database.rows.get(params[0])
        elif "AS `total_count`" in normalized:
            categories = {class_id: sum(row["class_id"] == class_id for row in rows) for class_id in ("recyclable", "kitchen", "hazardous", "other")}
            self.result = {
                "total_count": len(rows),
                "success_count": sum(row["result"] == "success" for row in rows),
                "failure_count": sum(row["result"] != "success" for row in rows),
                "fault_count": sum(bool(row["error_code"]) for row in rows),
                **{f"{key}_count": value for key, value in categories.items()},
            }
        else:
            rows.sort(key=lambda row: (row["timestamp_ms"], row["event_id"]), reverse=True)
            if "AS `recent_events`" in normalized:
                limit_count = int(params[-3])
                page_size = int(params[-2])
                offset = int(params[-1])
                rows = rows[:limit_count][offset : offset + page_size]
            self.result = rows

    def fetchone(self) -> Any:
        return self.result

    def fetchall(self) -> Any:
        return self.result


def make_event(event_id: str, timestamp_ms: int = 1000, **overrides: Any) -> dict[str, Any]:
    event = {
        "event_id": event_id,
        "task_id": "task-001",
        "timestamp_ms": timestamp_ms,
        "class_id": "recyclable",
        "confidence": 0.95,
        "u_px": 120.0,
        "v_px": 240.0,
        "target_x_m": 0.12,
        "target_y_m": 0.24,
        "target_z_m": 0.03,
        "result": "success",
        "error_code": None,
        "duration_ms": 500,
        "calibration_id": "cal-001",
    }
    event.update(overrides)
    return event


class EventsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.client = create_app({"TESTING": True}, self.database.connect).test_client()

    def test_duplicate_event_id_is_idempotent(self) -> None:
        event = make_event("event-001")
        first = self.client.post("/api/events", json=event)
        second = self.client.post("/api/events", json=event)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json["created"])
        self.assertFalse(second.json["created"])
        self.assertEqual(len(self.database.rows), 1)

    def test_list_is_filtered_sorted_and_paginated(self) -> None:
        self.database.rows = {
            "event-001": make_event("event-001", 1000),
            "event-002": make_event("event-002", 3000, class_id="kitchen"),
            "event-003": make_event("event-003", 2000),
        }
        response = self.client.get("/api/events?class_id=recyclable&page=1&page_size=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["items"][0]["event_id"], "event-003")
        self.assertEqual(response.json["pagination"]["total"], 2)

    def test_detail_and_missing_event(self) -> None:
        self.database.rows["event-001"] = make_event("event-001")
        found = self.client.get("/api/events/event-001")
        missing = self.client.get("/api/events/not-found")

        self.assertEqual(found.status_code, 200)
        self.assertEqual(set(found.json), set(EVENT_FIELDS))
        self.assertEqual(missing.status_code, 404)
        self.assertIsInstance(missing.json, dict)

    def test_export_csv_has_utf8_bom_and_contract_fields(self) -> None:
        self.database.rows["event-001"] = make_event("event-001")
        response = self.client.get("/api/events/export")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"event_id,task_id,timestamp_ms", response.data)

    def test_stats_reports_category_ratios_and_counts(self) -> None:
        self.database.rows = {
            "event-001": make_event("event-001", 1000),
            "event-002": make_event("event-002", 2000, class_id="kitchen", result="failed", error_code="PICK_FAIL"),
        }
        response = self.client.get("/api/stats?start_time_ms=0&end_time_ms=3000")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["total_count"], 2)
        self.assertEqual(response.json["categories"]["recyclable"], {"count": 1, "ratio": 0.5})
        self.assertEqual(response.json["success_count"], 1)
        self.assertEqual(response.json["failure_count"], 1)
        self.assertEqual(response.json["fault_count"], 1)

    def test_invalid_input_returns_json_error(self) -> None:
        response = self.client.post("/api/events", json={"event_id": "missing-fields"})

        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.json, dict)
        self.assertIn("error", response.json)

    def test_task_and_monitor_pages_render(self) -> None:
        task_page = self.client.get("/tasks")
        monitor_page = self.client.get("/monitor")

        self.assertEqual(task_page.status_code, 200)
        self.assertIn("/api/tasks/control", task_page.get_data(as_text=True))
        self.assertEqual(monitor_page.status_code, 200)
        self.assertIn("模拟数据", monitor_page.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()