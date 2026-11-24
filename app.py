"""分拣事件保存、查询、导出与统计 API。"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import io
import math
import os
import hmac
import threading
import time as monotonic_time
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException


def load_local_env() -> None:
    """Load simple KEY=VALUE settings from the ignored local .env file."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


load_local_env()

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
TASK_STATES = {"idle", "running", "paused", "finished", "fault", "estop"}
ROBOT_STATES = {"idle", "running", "paused", "fault", "estop"}


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
    app.config.setdefault("ROBOT_BRIDGE_TOKEN", os.environ.get("ROBOT_BRIDGE_TOKEN", ""))
    bridge_lock = threading.RLock()
    bridge = {
        "task_id": None,
        "task_state": "idle",
        "robot_state": "idle",
        "updated_at_ms": None,
        "last_seen_monotonic": None,
        "message": "等待机械臂连接。",
        "detections": [],
        "is_busy": False,
        "last_command_seq": 0,
        "latest_frame": None,
        "latest_frame_id": None,
        "latest_frame_timestamp_ms": None,
        "commands": [],
    }
    app.extensions["robot_bridge"] = bridge
    app.extensions["robot_bridge_lock"] = bridge_lock
    if connection_factory is None:
        db_config = load_db_config(app.config.get("DB_CONFIG_PATH"))
        connection_factory = lambda: get_connection(db_config)

    def open_connection() -> Any:
        return connection_factory()

    def bridge_snapshot() -> dict[str, Any]:
        with bridge_lock:
            online = (
                bridge["last_seen_monotonic"] is not None
                and monotonic_time.monotonic() - bridge["last_seen_monotonic"] <= 5
            )
            return {
                "task_id": bridge["task_id"],
                "task_state": bridge["task_state"],
                "robot_state": bridge["robot_state"] if online else "idle",
                "device_online": online,
                "timestamp_ms": bridge["updated_at_ms"],
                "message": bridge["message"] if online else "机械臂未连接或心跳超时。",
                "detections": list(bridge["detections"]),
                "is_busy": bool(bridge["is_busy"]),
                "last_command_seq": bridge["last_command_seq"],
            }

    def require_robot_token() -> tuple[Any, int] | None:
        configured_token = app.config.get("ROBOT_BRIDGE_TOKEN")
        if not isinstance(configured_token, str) or not configured_token:
            return jsonify({"error": "服务端未配置 ROBOT_BRIDGE_TOKEN。"}), 503
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {configured_token}"
        if not hmac.compare_digest(supplied, expected):
            return jsonify({"error": "设备认证失败。"}), 401
        return None

    def require_same_origin() -> tuple[Any, int] | None:
        origin = request.headers.get("Origin")
        if origin:
            expected_origin = request.host_url.rstrip("/")
            if not hmac.compare_digest(origin.rstrip("/"), expected_origin):
                return jsonify({"error": "跨来源任务控制请求已拒绝。"}), 403
        return None

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

    @app.post("/api/tasks/control")
    def control_task() -> tuple[Any, int] | Any:
        origin_error = require_same_origin()
        if origin_error:
            return origin_error
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise RequestError("请求体必须是 JSON 对象。")
        command = payload.get("command")
        if command not in {"start", "pause", "resume", "finish"}:
            raise RequestError("command 必须是 start、pause、resume 或 finish。")
        for field, max_length in (("operator_id", 64), ("session_id", 64), ("request_id", 64)):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > max_length:
                raise RequestError(f"字段 {field} 必须是1到{max_length}个字符的非空字符串。")
        if payload.get("task_mode") not in {"single", "continuous"}:
            raise RequestError("task_mode 必须是 single 或 continuous。")

        with bridge_lock:
            online = (
                bridge["last_seen_monotonic"] is not None
                and monotonic_time.monotonic() - bridge["last_seen_monotonic"] <= 5
            )
            if not online:
                return jsonify({"accepted": False, "reason_code": "ROBOT_OFFLINE", "message": "机械臂未在线，未发送任务命令。"}), 409
            state = bridge["task_state"]
            allowed = {
                "start": {"idle", "finished"},
                "pause": {"running"},
                "resume": {"paused"},
                "finish": {"running", "paused"},
            }
            if state not in allowed[command]:
                return jsonify({"accepted": False, "reason_code": "INVALID_TRANSITION", "message": f"当前状态 {state} 不允许执行 {command}。"}), 409
            task_id = uuid4().hex if command == "start" else bridge["task_id"]
            next_state = {"start": "running", "pause": "paused", "resume": "running", "finish": "finished"}[command]
            bridge["last_command_seq"] += 1
            queued = {
                "seq": bridge["last_command_seq"],
                "command": command,
                "task_id": task_id,
                "task_mode": payload["task_mode"],
                "created_at_ms": int(monotonic_time.time() * 1000),
            }
            bridge["commands"].append(queued)
            bridge["commands"] = bridge["commands"][-100:]
            bridge["task_id"] = task_id
            bridge["task_state"] = next_state
            bridge["updated_at_ms"] = queued["created_at_ms"]
            bridge["message"] = f"任务命令已排队：{command}。"
            result = bridge_snapshot()
        return jsonify({"accepted": True, **result, "command_seq": queued["seq"]})

    @app.get("/api/tasks/state")
    def get_task_state() -> Any:
        return jsonify(bridge_snapshot())

    # ================= 课程任务动作接口 =================
    # 和 /api/tasks/control 不同：这个接口不受 idle/running 状态机约束，
    # 专门用来触发一次性的演示动作（RGB、蜂鸣器、读舵机、跳舞、叠罗汉……）。
    TASK_ACTIONS = {
        "rgb", "buzzer", "read_servo", "wave", "dance",
        "grab_once", "stack", "carry", "asr",
    }

    @app.post("/api/tasks/action")
    def task_action() -> tuple[Any, int] | Any:
        origin_error = require_same_origin()
        if origin_error:
            return origin_error
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise RequestError("请求体必须是 JSON 对象。")
        action = payload.get("action")
        if action not in TASK_ACTIONS:
            raise RequestError(f"action 必须是 {', '.join(sorted(TASK_ACTIONS))} 之一。")
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            raise RequestError("args 必须是 JSON 对象。")

        with bridge_lock:
            online = (
                bridge["last_seen_monotonic"] is not None
                and monotonic_time.monotonic() - bridge["last_seen_monotonic"] <= 5
            )
            if not online:
                return jsonify({"accepted": False, "reason_code": "ROBOT_OFFLINE",
                                "message": "机械臂未在线，未发送动作命令。"}), 409
            bridge["last_command_seq"] += 1
            queued = {
                "seq": bridge["last_command_seq"],
                "command": "action",
                "action": action,
                "args": args,
                "created_at_ms": int(monotonic_time.time() * 1000),
            }
            bridge["commands"].append(queued)
            bridge["commands"] = bridge["commands"][-100:]
            bridge["message"] = f"动作命令已排队：{action}。"
            result = bridge_snapshot()
        return jsonify({"accepted": True, **result, "command_seq": queued["seq"]})

    @app.post("/api/robot/connect")
    def connect_robot() -> tuple[Any, int] | Any:
        auth_error = require_robot_token()
        if auth_error:
            return auth_error
        now_ms = int(monotonic_time.time() * 1000)
        with bridge_lock:
            bridge["last_seen_monotonic"] = monotonic_time.monotonic()
            bridge["updated_at_ms"] = now_ms
            bridge["robot_state"] = "idle"
            bridge["is_busy"] = False
            bridge["detections"] = []
            bridge["message"] = "机械臂客户端已连接，等待任务启动。"
            # 新进程从当前序号开始，避免重放上次进程遗留的启动命令。
            cursor = bridge["last_command_seq"]
        return jsonify({"after_seq": cursor, "timestamp_ms": now_ms})

    @app.get("/api/robot/poll")
    def poll_robot() -> tuple[Any, int] | Any:
        auth_error = require_robot_token()
        if auth_error:
            return auth_error
        try:
            after_seq = int(request.args.get("after_seq", "0"))
        except ValueError as error:
            raise RequestError("after_seq 必须是非负整数。") from error
        if after_seq < 0:
            raise RequestError("after_seq 必须是非负整数。")
        with bridge_lock:
            bridge["last_seen_monotonic"] = monotonic_time.monotonic()
            commands = [item for item in bridge["commands"] if item["seq"] > after_seq]
            return jsonify({"commands": commands, "last_command_seq": bridge["last_command_seq"], "timestamp_ms": int(monotonic_time.time() * 1000)})

    @app.post("/api/robot/status")
    def report_robot_status() -> tuple[Any, int] | Any:
        auth_error = require_robot_token()
        if auth_error:
            return auth_error
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise RequestError("请求体必须是 JSON 对象。")
        task_state = payload.get("task_state")
        robot_state = payload.get("robot_state")
        if task_state not in TASK_STATES or robot_state not in ROBOT_STATES:
            raise RequestError("task_state 或 robot_state 无效。")
        detections = payload.get("detections", [])
        if not isinstance(detections, list) or len(detections) > 20 or any(not isinstance(item, str) or len(item) > 64 for item in detections):
            raise RequestError("detections 必须是最多20个字符串组成的数组。")
        is_busy = payload.get("is_busy", False)
        if not isinstance(is_busy, bool):
            raise RequestError("is_busy 必须是布尔值。")
        message = payload.get("message", "")
        if not isinstance(message, str) or len(message) > 256:
            raise RequestError("message 必须是不超过256个字符的字符串。")
        with bridge_lock:
            bridge["task_state"] = task_state
            bridge["robot_state"] = robot_state
            bridge["task_id"] = payload.get("task_id") or bridge["task_id"]
            bridge["detections"] = detections
            bridge["is_busy"] = is_busy
            bridge["message"] = message or "设备遥测正常。"
            bridge["last_seen_monotonic"] = monotonic_time.monotonic()
            bridge["updated_at_ms"] = int(monotonic_time.time() * 1000)
        return jsonify({"accepted": True, "timestamp_ms": bridge["updated_at_ms"]})

    @app.post("/api/robot/frame")
    def upload_robot_frame() -> tuple[Any, int] | Any:
        """接受机械臂端上传的最新 JPEG 帧，供监控页读取。"""
        auth_error = require_robot_token()
        if auth_error:
            return auth_error
        if request.mimetype != "image/jpeg":
            return jsonify({"error": "视频帧必须使用 image/jpeg。"}), 415
        frame = request.get_data(cache=False)
        if not frame or len(frame) > 2 * 1024 * 1024:
            return jsonify({"error": "视频帧为空或超过2MB。"}), 413
        now_ms = int(monotonic_time.time() * 1000)
        frame_id = request.headers.get("X-Frame-Id", f"frame_{now_ms}")[:64]
        with bridge_lock:
            bridge["latest_frame"] = frame
            bridge["latest_frame_id"] = frame_id
            bridge["latest_frame_timestamp_ms"] = now_ms
            bridge["last_seen_monotonic"] = monotonic_time.monotonic()
        return jsonify({"accepted": True, "frame_id": frame_id, "timestamp_ms": now_ms})

    @app.get("/api/robot/frame")
    def get_robot_frame() -> Response:
        with bridge_lock:
            frame = bridge["latest_frame"]
            frame_id = bridge["latest_frame_id"]
            timestamp_ms = bridge["latest_frame_timestamp_ms"]
        if not frame:
            return Response(status=204)
        response = Response(frame, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["X-Frame-Id"] = frame_id or ""
        response.headers["X-Frame-Timestamp-Ms"] = str(timestamp_ms or "")
        return response

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
                    "SUM(CASE WHEN `result` = 'failed' THEN 1 ELSE 0 END) AS `failure_count`, "
                    "SUM(CASE WHEN `result` = 'unverified' THEN 1 ELSE 0 END) AS `unverified_count`, "
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
                "unverified_count": int(row["unverified_count"] or 0),
                "fault_count": int(row["fault_count"] or 0),
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=os.environ.get("FLASK_HOST", "0.0.0.0"), port=int(os.environ.get("FLASK_PORT", "5000")), debug=False)
