import unittest
import io
from threading import Thread
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import create_app
from backend.app import create_app as create_backend
from frontend.app import create_app as create_frontend
from test_events_api import FakeConnection, FakeCursor, FakeDatabase, make_event


class WebCursor(FakeCursor):
    def execute(self, query, params=()):
        sql = " ".join(query.split())
        database = self.database
        if "FROM `session` s JOIN `user`" in sql:
            session = database.sessions.get(params[0])
            self.result = session if session and session["expires_at_ms"] > params[1] else None
        elif sql.startswith("SELECT * FROM `user`"):
            self.result = database.users.get(params[0])
        elif sql.startswith("INSERT INTO `session`"):
            fields = ("session_id", "operator_id", "role", "expires_at_ms", "created_at")
            database.sessions[params[0]] = dict(zip(fields, params))
        elif sql.startswith("DELETE FROM `session` WHERE `session_id`"):
            database.sessions.pop(params[0], None)
        elif sql.startswith("INSERT INTO `auth_log`"):
            pass
        elif sql.startswith("SELECT * FROM `expression_config`"):
            self.result = database.expression_config.get(params[0])
        elif sql.startswith("INSERT INTO `expression_config`"):
            fields = ("operator_id", "enabled", "authorized", "action_id", "updated_at")
            database.expression_config[params[0]] = dict(zip(fields, params))
        elif sql.startswith("INSERT INTO `expression_log`"):
            database.expression_results.append(params[-1])
        else:
            super().execute(query, params)


class WebConnection(FakeConnection):
    def cursor(self):
        return WebCursor(self.database)


class WebAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = generate_password_hash("test-password")

    def setUp(self):
        self.database = FakeDatabase()
        self.database.users = {
            role: {"operator_id": role, "role": role, "status": "active",
                   "password_hash": self.password_hash, "badge_code": None}
            for role in ("operator", "manager", "maintainer")
        }
        self.database.sessions = {}
        self.database.expression_config = {}
        self.database.expression_results = []
        self.app = create_app({
            "TESTING": True,
            "WEB_AUTH_REQUIRED": True,
            "ROBOT_BRIDGE_TOKEN": "test-board-token",
            "FACE_TERMINAL_TOKEN": "test-terminal-token",
            "FACE_IMAGE_LOGIN_ENABLED": False,
        }, lambda: WebConnection(self.database))
        self.client = self.app.test_client()

    def login(self, role="operator"):
        response = self.client.post("/api/auth/password", json={"operator_id": role, "password": "test-password"})
        self.assertEqual(response.status_code, 200)
        return response

    def test_cookie_navigation_and_server_logout(self):
        self.assertEqual(self.client.get("/tasks").status_code, 302)
        self.assertEqual(self.client.post("/api/tasks/control", json={}).status_code, 401)
        response = self.login()
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertEqual(self.client.get("/login").location, "/tasks")
        page = self.client.get("/tasks")
        self.assertEqual(page.status_code, 200)
        self.assertIn("表情互动", page.get_data(as_text=True))
        self.assertEqual(self.client.get("/monitor").location, "/tasks")
        self.assertNotIn(response.json["session_id"], page.get_data(as_text=True))
        self.assertNotIn("export-csv", self.client.get("/records").get_data(as_text=True))
        self.assertEqual(self.client.get("/api/events/export").status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", json={}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)
        self.assertFalse(self.database.sessions)

    def test_role_specific_routes_and_expiry(self):
        self.login("manager")
        self.assertEqual(self.client.get("/users").status_code, 200)
        monitor = self.client.get("/monitor")
        self.assertEqual(monitor.status_code, 200)
        self.assertIn('id="camera-image"', monitor.get_data(as_text=True))
        self.assertNotIn('id="start-task"', monitor.get_data(as_text=True))
        self.assertNotIn('data-action="rgb"', monitor.get_data(as_text=True))
        self.assertEqual(self.client.get("/tasks").status_code, 403)
        self.assertIn("export-csv", self.client.get("/records").get_data(as_text=True))
        response = self.login("maintainer")
        self.assertEqual(self.client.get("/maintain").status_code, 200)
        self.assertEqual(self.client.get("/monitor").status_code, 200)
        self.assertEqual(self.client.get("/api/users").status_code, 403)
        self.database.sessions[response.json["session_id"]]["expires_at_ms"] = 0
        self.assertEqual(self.client.get("/maintain").status_code, 302)

    def test_board_token_and_authenticated_operator_keep_existing_task_contract(self):
        board = self.app.test_client()
        headers = {"Authorization": "Bearer test-board-token"}
        self.assertEqual(board.post("/api/robot/connect", headers=headers).status_code, 200)
        self.assertEqual(board.post("/api/events", json=make_event("case-1")).status_code, 401)
        self.assertEqual(board.post("/api/events", headers=headers, json=make_event("case-1")).status_code, 201)
        self.login()
        control = self.client.post("/api/tasks/control", json={
            "command": "start", "task_mode": "single", "request_id": "browser-1",
        })
        self.assertEqual(control.status_code, 200)
        self.assertEqual(board.get("/api/robot/poll", headers=headers).json["commands"][0]["command"], "start")

    def test_separate_frontend_preserves_cookie_and_download_headers(self):
        backend = create_backend(
            {"TESTING": True, "WEB_AUTH_REQUIRED": True},
            lambda: WebConnection(self.database),
        )
        self.assertEqual(backend.test_client().get("/stats").status_code, 404)
        server = make_server("127.0.0.1", 0, backend)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            frontend = create_frontend(backend_url=f"http://127.0.0.1:{server.server_port}")
            client = frontend.test_client()
            self.assertEqual(client.get("/stats").location, "/login")
            login = client.post(
                "/api/auth/password", headers={"Origin": "http://localhost"},
                json={"operator_id": "manager", "password": "test-password"},
            )
            self.assertEqual(login.status_code, 200)
            self.assertIn("HttpOnly", login.headers["Set-Cookie"])
            self.assertEqual(client.get("/stats").status_code, 200)
            download = client.get("/api/events/export?class_id=recyclable")
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.headers["Content-Disposition"], "attachment; filename=events.csv")
            self.assertEqual(client.post("/api/auth/logout").location, "/login")
            self.assertEqual(client.get("/api/auth/session").status_code, 401)
        finally:
            server.shutdown()
            worker.join()
            server.server_close()

    def test_face_result_requires_trusted_terminal_and_uses_server_clock(self):
        payload = {"operator_id": "operator", "auth_result": "success", "confidence": 0.95,
                   "terminal_id": "test-terminal", "timestamp_ms": 1}
        self.assertEqual(self.client.post("/api/auth/face", json=payload).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/face/image").status_code, 503)
        with patch("backend.web_access.now_ms", return_value=15000):
            response = self.client.post("/api/auth/face", json=payload,
                                        headers={"Authorization": "Bearer test-terminal-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["expires_at_ms"], 15000 + 8 * 60 * 60 * 1000)

    def test_expression_checks_bridge_state_and_records_simulated_result(self):
        headers = {"Authorization": "Bearer test-board-token"}
        self.login()
        payload = {"detected": True, "confidence": 0.95, "task_state": "idle"}
        self.assertEqual(self.client.post("/api/expression/trigger", json=payload).json["result"], "已关闭")
        response = self.client.post("/api/expression/config", json={
            "enabled": True, "authorized": True, "action_id": "smile_nod",
        })
        self.assertEqual(response.status_code, 200)
        idle_result = self.client.post("/api/expression/trigger", json=payload).json
        self.assertTrue(idle_result["triggered"])
        self.assertTrue(idle_result["simulated"])
        self.assertEqual(self.client.post("/api/robot/status", headers=headers, json={
            "task_state": "running", "robot_state": "running",
        }).status_code, 200)
        blocked = self.client.post("/api/expression/trigger", json=payload).json
        self.assertFalse(blocked["triggered"])
        self.assertEqual(blocked["result"], "分拣中禁止互动")
        self.assertEqual(self.app.extensions["robot_bridge"]["commands"], [])
        self.assertEqual(self.database.expression_results, ["已关闭", "success", "分拣中禁止互动"])

    def test_face_image_proxy_session_and_rejections(self):
        import cv2
        import numpy as np
        from backend.train_face_model import NoFaceError
        from werkzeug.test import Client

        backend = create_backend({'TESTING': True, 'WEB_AUTH_REQUIRED': True,
                                  'FACE_IMAGE_LOGIN_ENABLED': True, 'FACE_THRESHOLD': '0.45'},
                                 lambda: WebConnection(self.database))
        def forward(method, path, headers, body=b'', query=b''):
            return Client(backend, use_cookies=False).open(path, method=method, headers=headers,
                                                         data=body, query_string=query.decode('ascii'))
        client = create_frontend(forward).test_client()
        photo = cv2.imencode('.jpg', np.zeros((64, 64, 3), dtype=np.uint8))[1].tobytes()
        def submit(origin='http://localhost'):
            return client.post('/api/auth/face/image', headers={'Origin': origin},
                               data={'image': (io.BytesIO(photo), 'face.jpg')})

        with patch('backend.train_face_model.FaceRecognizer') as recognizer:
            recognizer.return_value.predict.return_value = ('operator', 0.1)
            response = submit()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['authorized'])
            self.assertIn('HttpOnly', response.headers['Set-Cookie'])
            self.assertEqual(client.get('/tasks').status_code, 200)
            self.assertEqual(client.get('/api/video/config', base_url='http://localhost').status_code, 200)
            self.assertEqual(submit('http://other.example').status_code, 403)
            client.post('/api/auth/logout', json={})
            recognizer.return_value.predict.return_value = ('operator', 0.8)
            self.assertEqual(submit().status_code, 401)
            self.assertFalse(self.database.sessions)
            recognizer.return_value.predict.side_effect = NoFaceError('no face')
            self.assertEqual(submit().status_code, 422)
            self.assertFalse(self.database.sessions)
        self.assertEqual(client.post('/api/auth/face/image', data={'image': (io.BytesIO(b'bad'), 'bad.jpg')}).status_code, 400)
