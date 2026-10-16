"""分拣事件保存、查询、导出与统计 API。"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import io
import math
from typing import Any

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from database import get_connection, load_db_config


EVENT_FIELDS = (
    "event_id",
    "task_id",
    "timestamp_ms",
    "class_id",
    "confidence",
    "u_px",
    "v_px",
    "target_x_m",
    "target_y_m",
    "target_z_m",
    "result",
    "error_code",
    "duration_ms",
    "calibration_id",
)
NULLABLE_FIELDS = {"target_x_m", "target_y_m", "target_z_m", "error_code"}
CLASS_IDS = {"recyclable", "kitchen", "hazardous", "other"}
MAX_RECENT_EVENTS = 500
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class RequestError(ValueError):
    """表示客户端请求参数无效。"""


def _finite_number(value: Any, field: str, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError(f"字段 {field} 必须是数字。")
    if not math.isfinite(value):
        raise RequestError(f"字段 {field} 必须是有限数字。")
    return float(value)


def validate_event(payload: Any) -> dict[str, Any]:
    """校验事件字段、类型、范围和类别枚举。"""
    if not isinstance(payload, dict):
        raise RequestError("请求体必须是 JSON 对象。")

    missing = [field for field in EVENT_FIELDS if field not in payload]
    if missing:
        raise RequestError(f"缺少必填字段：{', '.join(missing)}")
    extra = sorted(set(payload) - set(EVENT_FIELDS))
    if extra:
        raise RequestError(f"包含未定义字段：{', '.join(extra)}")

    event_data = dict(payload)
    for field, max_length in (("event_id", 64), ("task_id", 64), ("class_id", 32), ("result", 64), ("calibration_id", 64)):
        value = event_data[field]
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise RequestError(f"字段 {field} 必须是1到{max_length}个字符的非空字符串。")

    if event_data["class_id"] not in CLASS_IDS:
        raise RequestError("class_id 必须是 recyclable、kitchen、hazardous 或 other。")
    if event_data["error_code"] is not None and (
        not isinstance(event_data["error_code"], str) or len(event_data["error_code"]) > 64
    ):
        raise RequestError("字段 error_code 必须是最多64个字符的字符串或 null。")

    timestamp_ms = event_data["timestamp_ms"]
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or not 0 <= timestamp_ms <= 18446744073709551615:
        raise RequestError("timestamp_ms 必须是非负 BIGINT UNSIGNED 毫秒整数。")
    duration_ms = event_data["duration_ms"]
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 0 <= duration_ms <= 4294967295:
        raise RequestError("duration_ms 必须是非负 INT UNSIGNED 毫秒整数。")

    confidence = _finite_number(event_data["confidence"], "confidence")
    if confidence is None or not 0 <= confidence <= 1:
        raise RequestError("confidence 必须在0到1之间。")
    event_data["confidence"] = confidence
    for field in ("u_px", "v_px", "target_x_m", "target_y_m", "target_z_m"):
        event_data[field] = _finite_number(event_data[field], field, field in NULLABLE_FIELDS)

    return event_data


def _build_time_filter(args: Any) -> tuple[list[str], list[Any], int | None, int | None]:
    """解析 UTC 日期或 Unix epoch 毫秒时间范围。"""
    clauses: list[str] = []
    values: list[Any] = []
    start_time_ms: int | None = None
    end_time_ms: int | None = None

    date_value = args.get("date")
    start_value = args.get("start_time_ms")
    end_value = args.get("end_time_ms")
    if date_value:
        if start_value is not None or end_value is not None:
            raise RequestError("date 不能与 start_time_ms/end_time_ms 同时使用。")
        try:
            selected_date = date.fromisoformat(date_value)
        except (TypeError, ValueError) as error:
            raise RequestError("date 必须使用 YYYY-MM-DD 格式。") from error
        start_dt = datetime.combine(selected_date, time.min, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        start_time_ms = int(start_dt.timestamp() * 1000)
        end_time_ms = int(end_dt.timestamp() * 1000)
    elif start_value is not None or end_value is not None:
        if start_value is None or end_value is None:
            raise RequestError("start_time_ms 和 end_time_ms 必须同时提供。")
        try:
            start_time_ms = int(start_value)
            end_time_ms = int(end_value)
        except (TypeError, ValueError) as error:
            raise RequestError("时间范围必须是毫秒整数。") from error
        if str(start_time_ms) != str(start_value) or str(end_time_ms) != str(end_value):
            raise RequestError("时间范围必须是毫秒整数。")
        if start_time_ms < 0 or end_time_ms <= start_time_ms:
            raise RequestError("时间范围必须满足 0 <= start_time_ms < end_time_ms。")

    if start_time_ms is not None and end_time_ms is not None:
        clauses.extend(("`timestamp_ms` >= %s", "`timestamp_ms` < %s"))
        values.extend((start_time_ms, end_time_ms))
    return clauses, values, start_time_ms, end_time_ms


def _build_event_filters(args: Any) -> tuple[list[str], list[Any], int | None, int | None]:
    clauses, values, start_time_ms, end_time_ms = _build_time_filter(args)
    class_id = args.get("class_id")
    result = args.get("result")
    if class_id:
        if class_id not in CLASS_IDS:
            raise RequestError("class_id 必须是 recyclable、kitchen、hazardous 或 other。")
        clauses.append("`class_id` = %s")
        values.append(class_id)
    if result:
        if len(result) > 64:
            raise RequestError("result 最长为64个字符。")
        clauses.append("`result` = %s")
        values.append(result)
    return clauses, values, start_time_ms, end_time_ms


def _where_clause(clauses: list[str]) -> str:
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""


def _json_event(row: dict[str, Any]) -> dict[str, Any]:
    """将 MySQL 返回值转换为 JSON 可用类型。"""
    result = dict(row)
    if isinstance(result.get("confidence"), Decimal):
        result["confidence"] = float(result["confidence"])
    return result


def create_app(
    test_config: dict[str, Any] | None = None,
    connection_factory: Any | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(test_config or {})
    if connection_factory is None:
        db_config = load_db_config(app.config.get("DB_CONFIG_PATH"))
        connection_factory = lambda: get_connection(db_config)

    def open_connection() -> Any:
        return connection_factory()

    @app.get("/")
    def home_page() -> Response:
        return redirect(url_for("records_page"))

    @app.get("/records")
    def records_page() -> str:
        return render_template("records.html")

    @app.get("/stats")
    def stats_page() -> str:
        return render_template("stats.html")

    @app.get("/tasks")
    def tasks_page() -> str:
        return render_template("tasks.html")

    @app.get("/monitor")
    def monitor_page() -> str:
        return render_template("monitor.html")

    @app.errorhandler(RequestError)
    def handle_request_error(error: RequestError) -> tuple[Any, int]:
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> tuple[Any, int]:
        return jsonify({"error": error.description}), error.code or 500

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception) -> tuple[Any, int]:
        app.logger.exception("请求处理失败", exc_info=error)
        return jsonify({"error": "服务器内部错误。"}), 500

    @app.post("/api/events")
    def create_event() -> tuple[Any, int]:
        event_data = validate_event(request.get_json(silent=True))
        connection = open_connection()
        try:
            with connection.cursor() as cursor:
                columns = ", ".join(f"`{field}`" for field in EVENT_FIELDS)
                placeholders = ", ".join(["%s"] * len(EVENT_FIELDS))
                cursor.execute(
                    f"INSERT IGNORE INTO `event` ({columns}) VALUES ({placeholders})",
                    tuple(event_data[field] for field in EVENT_FIELDS),
                )
                created = cursor.rowcount == 1
            connection.commit()
            return jsonify({"event_id": event_data["event_id"], "created": created}), 201 if created else 200
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @app.get("/api/events")
    def list_events() -> tuple[Any, int] | Any:
        clauses, values, start_time_ms, end_time_ms = _build_event_filters(request.args)
        where_sql = _where_clause(clauses)
        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", str(DEFAULT_PAGE_SIZE)))
        except ValueError as error:
            raise RequestError("page 和 page_size 必须是整数。") from error
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise RequestError(f"page 必须大于0，page_size 必须在1到{MAX_PAGE_SIZE}之间。")

        connection = open_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS `count` FROM `event`{where_sql}", tuple(values))
                actual_total = int(cursor.fetchone()["count"])
                total = min(actual_total, MAX_RECENT_EVENTS)
                offset = (page - 1) * page_size
                cursor.execute(
                    "SELECT * FROM ("
                    f"SELECT {', '.join(f'`{field}`' for field in EVENT_FIELDS)} FROM `event`{where_sql} "
                    "ORDER BY `timestamp_ms` DESC, `event_id` DESC LIMIT %s"
                    ") AS `recent_events` "
                    "ORDER BY `timestamp_ms` DESC, `event_id` DESC LIMIT %s OFFSET %s",
                    tuple(values) + (MAX_RECENT_EVENTS, page_size, offset),
                )
                rows = [_json_event(row) for row in cursor.fetchall()]
        finally:
            connection.close()

        return jsonify(
            {
                "items": rows,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "max_records": MAX_RECENT_EVENTS,
                    "total_pages": (total + page_size - 1) // page_size,
                },
                "filter": {"start_time_ms": start_time_ms, "end_time_ms": end_time_ms},
            }
        )

    @app.get("/api/events/export")
    def export_events() -> Response:
        clauses, values, _, _ = _build_event_filters(request.args)
        where_sql = _where_clause(clauses)
        connection = open_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {', '.join(f'`{field}`' for field in EVENT_FIELDS)} "
                    f"FROM `event`{where_sql} ORDER BY `timestamp_ms` DESC, `event_id` DESC",
                    tuple(values),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=EVENT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_json_event(row))
        response = Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = "attachment; filename=events.csv"
        return response

    @app.get("/api/events/<string:event_id>")
    def get_event(event_id: str) -> tuple[Any, int] | Any:
        connection = open_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {', '.join(f'`{field}`' for field in EVENT_FIELDS)} "
                    "FROM `event` WHERE `event_id` = %s",
                    (event_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return jsonify({"error": "event_id 不存在。"}), 404
        return jsonify(_json_event(row))

    @app.get("/api/stats")
    def get_stats() -> Any:
        clauses, values, start_time_ms, end_time_ms = _build_time_filter(request.args)
        where_sql = _where_clause(clauses)
        connection = open_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS `total_count`, "
                    "SUM(CASE WHEN `result` = 'success' THEN 1 ELSE 0 END) AS `success_count`, "
                    "SUM(CASE WHEN `result` <> 'success' THEN 1 ELSE 0 END) AS `failure_count`, "
                    "SUM(CASE WHEN `error_code` IS NOT NULL AND `error_code` <> '' THEN 1 ELSE 0 END) AS `fault_count`, "
                    "SUM(CASE WHEN `class_id` = 'recyclable' THEN 1 ELSE 0 END) AS `recyclable_count`, "
                    "SUM(CASE WHEN `class_id` = 'kitchen' THEN 1 ELSE 0 END) AS `kitchen_count`, "
                    "SUM(CASE WHEN `class_id` = 'hazardous' THEN 1 ELSE 0 END) AS `hazardous_count`, "
                    "SUM(CASE WHEN `class_id` = 'other' THEN 1 ELSE 0 END) AS `other_count` "
                    f"FROM `event`{where_sql}",
                    tuple(values),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        total = int(row["total_count"] or 0)
        categories: dict[str, dict[str, Any]] = {}
        for class_id in ("recyclable", "kitchen", "hazardous", "other"):
            count = int(row[f"{class_id}_count"] or 0)
            categories[class_id] = {
                "count": count,
                "ratio": count / total if total else 0.0,
            }
        return jsonify(
            {
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "total_count": total,
                "categories": categories,
                "success_count": int(row["success_count"] or 0),
                "failure_count": int(row["failure_count"] or 0),
                "fault_count": int(row["fault_count"] or 0),
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)