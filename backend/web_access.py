"""Web accounts, browser sessions and optional face/expression endpoints."""

from contextlib import closing
from functools import wraps
from hmac import compare_digest
from math import isfinite
from pathlib import Path
import time
from uuid import uuid4

import click
from flask import abort, g, jsonify, redirect, request
from pymysql import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


ROLES = ("operator", "manager", "maintainer")
SESSION_COOKIE = "sorting_session"
SESSION_SECONDS = 8 * 60 * 60
EXPRESSION_ACTIONS = {"smile_wave", "smile_nod"}


def now_ms():
    return int(time.time() * 1000)


def json_object(key=None):
    payload = request.get_json(silent=True)
    if key and isinstance(payload, dict) and key in payload:
        payload = payload[key]
    if not isinstance(payload, dict):
        abort(400, description="请求体必须是 JSON 对象。")
    return payload


def field_text(payload, field, limit, *, default="", required=False):
    value = payload.get(field, default)
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        abort(400, description=f"{field} 格式无效。")
    return value.strip()


def read_confidence(payload):
    value = payload.get("confidence", 0)
    if type(value) not in (int, float) or not 0 <= value <= 1:
        abort(400, description="confidence 必须是 0 到 1 之间的数字。")
    return value


def insert_user(connection, operator_id, name, role, status, password, badge_code):
    user_id = f"user-{uuid4()}"
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO `user` (`user_id`, `operator_id`, `name`, `role`, `status`, "
            "`password_hash`, `badge_code`, `created_at`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, operator_id, name, role, status,
             generate_password_hash(password) if password else None, badge_code or None, now_ms()),
        )
    connection.commit()
    return {"user_id": user_id, "operator_id": operator_id, "role": role, "status": status}


def register_web_access(app, open_connection, bridge_snapshot, require_same_origin):
    def session_id():
        return request.headers.get("X-Session-ID") or request.cookies.get(SESSION_COOKIE)

    def load_user():
        if "web_user" not in g:
            g.web_user = None
            identifier = session_id()
            if identifier and len(identifier) <= 64:
                with closing(open_connection()) as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT s.`session_id`, s.`operator_id`, u.`role`, s.`expires_at_ms` "
                        "FROM `session` s JOIN `user` u ON u.`operator_id` = s.`operator_id` "
                        "WHERE s.`session_id` = %s AND s.`expires_at_ms` > %s AND u.`status` = 'active'",
                        (identifier, now_ms()),
                    )
                    g.web_user = cursor.fetchone()
        return g.web_user

    def require_role(*roles, optional=False):
        def decorate(view):
            @wraps(view)
            def guarded(*args, **kwargs):
                if optional and not app.config["WEB_AUTH_REQUIRED"]:
                    return view(*args, **kwargs)
                user = load_user()
                if not user:
                    abort(401, description="请先登录或重新登录。")
                if user["role"] not in roles:
                    abort(403, description="权限不足。")
                return view(*args, **kwargs)
            return guarded
        return decorate

    def same_origin():
        if require_same_origin():
            abort(403, description="跨来源请求已拒绝。")

    def authenticate(operator_id, terminal_id, check, *, confidence=None, error_code=None, **details):
        timestamp = now_ms()
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            user = None
            if operator_id:
                cursor.execute("SELECT * FROM `user` WHERE `operator_id` = %s FOR UPDATE", (operator_id,))
                user = cursor.fetchone()
            accepted = bool(user and user["status"] == "active" and check(user))
            if not accepted:
                cursor.execute(
                    "INSERT INTO `auth_log` (`log_id`, `operator_id`, `auth_result`, `confidence`, "
                    "`timestamp_ms`, `terminal_id`, `error_code`) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (str(uuid4()), operator_id if user else None, "failed", confidence,
                     timestamp, terminal_id, error_code),
                )
                connection.commit()
                response = jsonify(authorized=False, role=None, expires_at_ms=None, auth_result="unknown",
                                   confidence=confidence, **details)
                response.status_code = 422 if error_code == "NO_FACE" else 401
                return response
            identifier = str(uuid4())
            expires_at = timestamp + SESSION_SECONDS * 1000
            cursor.execute(
                "INSERT INTO `auth_log` (`log_id`, `operator_id`, `auth_result`, `confidence`, "
                "`timestamp_ms`, `terminal_id`) VALUES (%s, %s, %s, %s, %s, %s)",
                (str(uuid4()), operator_id, "success", confidence, timestamp, terminal_id),
            )
            cursor.execute(
                "INSERT INTO `session` (`session_id`, `operator_id`, `role`, `expires_at_ms`, `created_at`) "
                "VALUES (%s, %s, %s, %s, %s)",
                (identifier, operator_id, user["role"], expires_at, timestamp),
            )
            connection.commit()
        response = jsonify(
            authorized=True, role=user["role"], session_id=identifier, expires_at_ms=expires_at,
            operator_id=operator_id, timestamp_ms=timestamp, auth_result="success", confidence=confidence, **details,
        )
        response.set_cookie(SESSION_COOKIE, identifier, max_age=SESSION_SECONDS, httponly=True,
                            samesite="Lax", secure=app.config["WEB_SECURE_COOKIE"])
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/web/context")
    def web_context():
        current_user = load_user()
        user = None
        if current_user:
            user = {key: current_user[key] for key in ("operator_id", "role")}
        response = jsonify(auth_required=app.config["WEB_AUTH_REQUIRED"],
                           face_image_login_enabled=app.config["FACE_IMAGE_LOGIN_ENABLED"], user=user)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/web/maintenance")
    @require_role("maintainer")
    def maintenance_status():
        return jsonify(bridge_snapshot())

    @app.post("/api/auth/password")
    def password_auth():
        same_origin()
        payload = json_object("password_auth")
        operator_id = field_text(payload, "operator_id", 64, required=True)
        terminal = field_text(payload, "terminal_id", 128, default="web-login", required=True)
        password = field_text(payload, "password", 256)
        badge = field_text(payload, "badge_code", 64)

        def check(user):
            if password and user["password_hash"] and check_password_hash(user["password_hash"], password):
                return True
            return bool(badge and user["badge_code"] and
                        compare_digest(badge.encode(), user["badge_code"].encode()))

        return authenticate(operator_id, terminal, check)

    @app.post("/api/auth/face")
    def face_auth():
        token = app.config["FACE_TERMINAL_TOKEN"]
        if not token:
            abort(503, description="人脸识别终端尚未配置。")
        if not compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
            abort(401, description="终端认证失败。")
        payload = json_object("face_auth")
        operator_id = field_text(payload, "operator_id", 64, required=True)
        terminal = field_text(payload, "terminal_id", 128, required=True)
        confidence = read_confidence(payload)
        verified = payload.get("auth_result") == "success" and confidence >= 0.75
        return authenticate(operator_id, terminal, lambda user: verified, confidence=confidence)

    @app.post("/api/auth/face/image")
    def face_auth_image():
        same_origin()
        if not app.config["FACE_IMAGE_LOGIN_ENABLED"]:
            abort(503, description="图片识别登录尚未启用。")
        if request.content_length is not None and request.content_length > 2 * 1024 * 1024 + 65536:
            abort(413, description="图片不得超过 2 MB。")
        image = request.files.get("image") or request.files.get("file")
        if not image:
            abort(400, description="请上传人脸图片。")
        raw_bytes = image.read(2 * 1024 * 1024 + 1)
        if len(raw_bytes) > 2 * 1024 * 1024:
            abort(413, description="图片不得超过 2 MB。")
        if not raw_bytes:
            abort(400, description="图片不能为空。")
        terminal = field_text(request.form, "terminal_id", 128, default="web-image", required=True)
        started = time.perf_counter()
        try:
            import cv2
            import numpy as np
            from .train_face_model import FaceRecognizer, NoFaceError
        except ImportError:
            abort(503, description="请安装人脸识别依赖。")

        try:
            decoded = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error:
            abort(400, description="图片解码失败。")
        if decoded is None:
            abort(400, description="图片解码失败。")
        try:
            threshold = float(app.config["FACE_THRESHOLD"])
            if not isfinite(threshold) or not 0 < threshold <= 1:
                raise ValueError("FACE_THRESHOLD 必须大于 0 且不超过 1。")
            recognizer = FaceRecognizer(Path(app.config["FACE_MODEL_DIR"]))
            operator_id, distance = recognizer.predict(decoded)
        except NoFaceError:
            return authenticate(None, terminal, lambda user: False, error_code="NO_FACE",
                                confidence=0.0, message="图片中未检测到单张人脸。",
                                duration_ms=int((time.perf_counter() - started) * 1000))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, cv2.error):
            app.logger.exception("人脸识别模型或配置不可用")
            abort(503, description="人脸识别模型或阈值配置不可用。")
        verified = isfinite(distance) and distance < threshold
        confidence = max(0.0, min(1.0, 1 - distance / threshold)) if isfinite(distance) else 0.0
        return authenticate(
            operator_id if verified else None, terminal, lambda user: verified, confidence=confidence,
            distance=distance if isfinite(distance) else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    @app.post("/api/auth/logout")
    def logout():
        same_origin()
        identifier = session_id()
        if identifier:
            with closing(open_connection()) as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM `session` WHERE `session_id` = %s", (identifier,))
                connection.commit()
        response = redirect("/login") if request.mimetype != "application/json" else jsonify(logged_out=True)
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="Lax", secure=app.config["WEB_SECURE_COOKIE"])
        return response

    @app.get("/api/auth/session")
    @require_role(*ROLES)
    def get_session():
        response = jsonify(authorized=True, **g.web_user)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/users")
    @require_role("manager")
    def list_users():
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT `user_id`, `operator_id`, `name`, `role`, `status`, `created_at` "
                           "FROM `user` ORDER BY `created_at` DESC")
            return jsonify(items=cursor.fetchall())

    @app.post("/api/users")
    @require_role("manager")
    def create_user():
        same_origin()
        payload = json_object()
        operator_id = field_text(payload, "operator_id", 64, required=True)
        name = field_text(payload, "name", 128, required=True)
        role = payload.get("role")
        status = payload.get("status", "active")
        if role not in ROLES or status not in ("active", "inactive"):
            abort(400, description="角色或账号状态无效。")
        password = field_text(payload, "password", 256)
        badge = field_text(payload, "badge_code", 64)
        with closing(open_connection()) as connection:
            try:
                return jsonify(insert_user(connection, operator_id, name, role, status, password, badge)), 201
            except IntegrityError as error:
                if error.args[0] != 1062:
                    raise
                abort(409, description="操作员编号已存在。")

    @app.delete("/api/users/<string:operator_id>")
    @require_role("manager")
    def delete_user(operator_id):
        same_origin()
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT `operator_id` FROM `user` WHERE `operator_id` = %s FOR UPDATE", (operator_id,))
            if cursor.fetchone() is None:
                abort(404, description="账号不存在。")
            cursor.execute("DELETE FROM `session` WHERE `operator_id` = %s", (operator_id,))
            cursor.execute("DELETE FROM `expression_config` WHERE `operator_id` = %s", (operator_id,))
            cursor.execute("DELETE FROM `expression_log` WHERE `operator_id` = %s", (operator_id,))
            cursor.execute("UPDATE `auth_log` SET `operator_id` = NULL WHERE `operator_id` = %s", (operator_id,))
            cursor.execute("DELETE FROM `user` WHERE `operator_id` = %s", (operator_id,))
            connection.commit()
        return jsonify(deleted=True, operator_id=operator_id)

    def get_config(cursor):
        cursor.execute("SELECT * FROM `expression_config` WHERE `operator_id` = %s", (g.web_user["operator_id"],))
        row = cursor.fetchone()
        if row:
            return {**row, "enabled": bool(row["enabled"]), "authorized": bool(row["authorized"])}
        return {"operator_id": g.web_user["operator_id"], "enabled": False, "authorized": False,
                "action_id": "smile_wave", "updated_at": None}

    @app.get("/api/expression/config")
    @require_role("operator")
    def get_expression_config():
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            return jsonify(get_config(cursor))

    @app.post("/api/expression/config")
    @require_role("operator")
    def update_expression_config():
        same_origin()
        payload = json_object("expression_config")
        enabled, authorized = payload.get("enabled", False), payload.get("authorized", False)
        action = field_text(payload, "action_id", 64, default="smile_wave")
        if not isinstance(enabled, bool) or not isinstance(authorized, bool) or action not in EXPRESSION_ACTIONS:
            abort(400, description="互动开关、授权或动作无效。")
        identifier, timestamp = g.web_user["operator_id"], now_ms()
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO `expression_config` (`operator_id`, `enabled`, `authorized`, `action_id`, `updated_at`) "
                "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE `enabled` = %s, `authorized` = %s, "
                "`action_id` = %s, `updated_at` = %s",
                (identifier, enabled, authorized, action, timestamp, enabled, authorized, action, timestamp),
            )
            connection.commit()
        return jsonify(operator_id=identifier, enabled=enabled, authorized=authorized, action_id=action,
                       updated_at=timestamp)

    @app.get("/api/expression/log")
    @require_role("operator")
    def get_expression_log():
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM `expression_log` WHERE `operator_id` = %s "
                           "ORDER BY `triggered_at_ms` DESC LIMIT 20", (g.web_user["operator_id"],))
            return jsonify(items=cursor.fetchall())

    @app.post("/api/expression/trigger")
    @require_role("operator")
    def trigger_expression():
        same_origin()
        payload = json_object("expression")
        confidence = read_confidence(payload)
        detected = payload.get("detected", False)
        if not isinstance(detected, bool):
            abort(400, description="detected 必须为布尔值。")
        with closing(open_connection()) as connection, connection.cursor() as cursor:
            config = get_config(cursor)
            task = bridge_snapshot()
            if not config["enabled"]:
                result = "已关闭"
            elif not config["authorized"]:
                result = "未授权"
            elif task["task_state"] == "running":
                result = "分拣中禁止互动"
            elif task["task_state"] in {"paused", "fault", "estop"} or task["robot_state"] != "idle" or task["is_busy"]:
                result = "设备当前状态禁止互动"
            elif not detected or confidence < 0.8:
                result = "置信度不足"
            else:
                result = "success"
            cursor.execute(
                "INSERT INTO `expression_log` (`log_id`, `operator_id`, `action_id`, `triggered_at_ms`, `result`) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid4()), config["operator_id"], config["action_id"], now_ms(), result),
            )
            connection.commit()
        return jsonify(triggered=result == "success", simulated=True, result=result, action_id=config["action_id"])

    @app.cli.command("create-manager")
    @click.option("--operator-id", prompt="员工编号")
    @click.option("--name", prompt="姓名")
    @click.password_option()
    def create_manager(operator_id, name, password):
        operator_id, name, password = operator_id.strip(), name.strip(), password.strip()
        if not (0 < len(operator_id) <= 64 and 0 < len(name) <= 128 and 0 < len(password) <= 256):
            raise click.ClickException("请输入有效的员工编号、姓名和密码。")
        with closing(open_connection()) as connection:
            try:
                insert_user(connection, operator_id, name, "manager", "active", password, "")
            except IntegrityError as error:
                if error.args[0] != 1062:
                    raise
                raise click.ClickException("员工编号已存在。") from error
        click.echo(f"已创建班组长账号 {operator_id}。")

    return require_role
