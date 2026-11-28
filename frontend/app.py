"""Render browser pages and keep browser API requests same-origin."""

from functools import wraps
import os

from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import Headers
from werkzeug.exceptions import RequestEntityTooLarge

from project_config import load_local_env

from .backend_client import BackendClient, BackendUnavailable


ROLES = ("operator", "manager", "maintainer")
ROLE_HOME = {"operator": "tasks_page", "manager": "stats_page", "maintainer": "maintain_page"}
NAVIGATION = (
    ("records_page", "▤", "记录查询", ROLES, True),
    ("stats_page", "▥", "运营统计", ("manager",), True),
    ("tasks_page", "▷", "分拣工作台", ("operator",), True),
    ("monitor_page", "◎", "实时监控", ROLES, True),
    ("expression_page", "☺", "表情互动", ("operator",), False),
    ("users_page", "⚙", "账号管理", ("manager",), False),
    ("maintain_page", "⚒", "设备维护", ("maintainer",), False),
)
PAGE_ACCESS = {endpoint: (roles, public) for endpoint, _, _, roles, public in NAVIGATION}
API_HEADERS = ("Accept", "Authorization", "Content-Type", "Cookie", "Origin", "X-Frame-Id", "X-Session-ID")


def create_app(backend_request=None, *, backend_url=None):
    load_local_env()
    if backend_request is None:
        default_url = f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '5002')}"
        backend_request = BackendClient(backend_url or os.environ.get("BACKEND_URL", default_url))

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 + 65536
    app.jinja_env.globals["navigation"] = NAVIGATION

    def request_headers(names):
        return Headers((name, request.headers[name]) for name in names if name in request.headers)

    def backend_result(path):
        response = backend_request("GET", path, request_headers(("Cookie", "X-Session-ID")))
        if response.status_code >= 500:
            raise BackendUnavailable("后端 API 暂时不可用。")
        if response.status_code != 200:
            abort(response.status_code)
        result = response.get_json(silent=True)
        if not isinstance(result, dict):
            raise BackendUnavailable("后端 API 返回了无效数据。")
        return result

    def view_context():
        if "web_context" not in g:
            g.web_context = backend_result("/api/web/context")
            g.web_user = g.web_context["user"]
        return g.web_context

    def can_control():
        return not g.web_context["auth_required"] or g.web_user and g.web_user["role"] == "operator"

    def page_access(view):
        @wraps(view)
        def guarded():
            context = view_context()
            roles, public = PAGE_ACCESS[request.endpoint]
            if not (public and not context["auth_required"]):
                if not g.web_user:
                    return redirect(url_for("login_page"))
                if g.web_user["role"] not in roles:
                    abort(403, description="当前账号无权访问该页面。")
            return view()
        return guarded

    @app.after_request
    def prevent_caching_user_pages(response):
        if "web_context" in g:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(BackendUnavailable)
    def unavailable(error):
        if request.path.startswith("/api/"):
            return jsonify(error=str(error)), 502
        return str(error), 502, {"Content-Type": "text/plain; charset=utf-8"}

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(error):
        return jsonify(error="请求体超出上传大小限制。"), error.code

    @app.get("/")
    def home_page():
        return redirect(url_for("records_page"))

    @app.get("/records")
    @page_access
    def records_page():
        return render_template("records.html")

    @app.get("/stats")
    @page_access
    def stats_page():
        return render_template("stats.html")

    @app.get("/tasks")
    @page_access
    def tasks_page():
        return render_template("workbench.html", can_control=True)

    @app.get("/monitor")
    @page_access
    def monitor_page():
        if can_control():
            return redirect(url_for("tasks_page"))
        return render_template("workbench.html", can_control=False)

    @app.get("/login")
    def login_page():
        view_context()
        if g.web_user:
            return redirect(url_for(ROLE_HOME[g.web_user["role"]]))
        return render_template("login.html")

    @app.get('/api/video/config')
    def video_config():
        # Resolve on the browser-facing host, never on the remote API host.
        from urllib.parse import urlsplit
        context = view_context()
        if context['auth_required'] and not g.web_user:
            return jsonify(error='请先登录。'), 401
        host = urlsplit(request.host_url).hostname
        host = f'[{host}]' if ':' in host else host
        base = app.config.get('VIDEO_WEBRTC_BASE_URL', os.environ.get('VIDEO_WEBRTC_BASE_URL', '')).strip().rstrip('/') or f'{request.scheme}://{host}:8889'
        stream = app.config.get('VIDEO_STREAM_PATH', os.environ.get('VIDEO_STREAM_PATH', 'robot-camera'))
        parsed = urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            return jsonify(error='视频地址配置无效。'), 503
        if not stream or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in stream):
            return jsonify(error='视频流名称配置无效。'), 503
        mode = app.config.get('VIDEO_TRANSPORT', os.environ.get('VIDEO_TRANSPORT', 'webrtc'))
        if mode not in ('webrtc', 'jpeg'):
            return jsonify(error='视频传输模式配置无效。'), 503
        response = jsonify(transport=mode,
                           whep_url=f'{base}/{stream}/whep', jpeg_url='/api/robot/frame')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get("/users")
    @page_access
    def users_page():
        return render_template("users.html")

    @app.get("/maintain")
    @page_access
    def maintain_page():
        return render_template("maintain.html", device=backend_result("/api/web/maintenance"))

    @app.get("/expression")
    @page_access
    def expression_page():
        return render_template("expression.html")

    @app.route("/api/<path:api_path>", methods=["GET", "POST", "DELETE"])
    def forward_api(api_path):
        origin = request.headers.get("Origin")
        if request.method in {"POST", "DELETE"} and origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            return jsonify(error="跨来源请求已拒绝。"), 403
        return backend_request(request.method, f"/api/{api_path}", request_headers(API_HEADERS),
                               request.get_data(), request.query_string)

    return app
