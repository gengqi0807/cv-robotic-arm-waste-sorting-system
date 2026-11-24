"""Load the retained fixed-token configuration without requiring shell exports."""
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error
import cv2


def load_bridge_env():
    explicit = os.environ.get("ROBOT_BRIDGE_ENV")
    candidates = ([Path(explicit)] if explicit else
                  [parent / "robot_bridge.env" for parent in Path(__file__).resolve().parents])
    for config in candidates:
        if not config.is_file():
            continue
        for line in config.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("ROBOT_"):
                os.environ.setdefault(key, value.strip().strip("\"'"))
        print(f"[Web配置] 已加载 {config}（不显示令牌）", flush=True)
        return config
    return None


class RobotBridge:
    """通过 Flask HTTP 桥接接收任务控制并上报设备心跳。"""
    def __init__(self):
        load_bridge_env()
        self.last_error = None
        self.last_error_time = 0.0
        self.poll_failed = False
        self.base_url = os.environ.get("ROBOT_BRIDGE_URL", "http://127.0.0.1:5000").rstrip("/")
        self.token = os.environ.get("ROBOT_BRIDGE_TOKEN", "")
        self.after_seq = 0
        self.task_id = None
        self.task_mode = 'continuous'
        self.task_state = "idle"
        self.robot_state = "idle"
        self.message = "等待任务启动。"
        self.clock_offset_ms = 0

    def report_error(self, error):
        now = time.monotonic()
        if now - self.last_error_time >= 5:
            print(f"[Web连接失败] {self.base_url}: {error}", flush=True)
            self.last_error_time = now

    def _request(self, method, path, payload=None):
        if not self.token:
            return None
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=body, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            self.last_error = str(error)
            self.report_error(error)
            return None

    def connect(self):
        result = self._request("POST", "/api/robot/connect", {})
        if result:
            self.after_seq = int(result.get("after_seq", self.after_seq))
            # Use the Web server's clock for record date filters, even when the
            # device clock has drifted. This does not change the system clock.
            self.clock_offset_ms = int(result.get('timestamp_ms', time.time() * 1000)) - int(time.time() * 1000)
        return result is not None

    def complete_motion(self, error):
        if error:
            if getattr(error, 'recovered_to_observation', False) and self.robot_state != 'fault':
                if self.task_state != 'finished':
                    self.task_state = self.robot_state = 'paused'
                else:
                    self.robot_state = 'idle'
                self.message = f'本次抓取失败，已确认撤离并回观察位；检查夹爪后点击恢复，无需重启：{error}'
                print(f'[任务暂停] {self.message}', flush=True)
                return
            self.task_state = self.robot_state = 'fault'
            self.message = f'动作失败，停止后续抓取：{error}'
        else:
            if self.task_mode == 'single' and self.task_state == 'running':
                self.task_state = 'finished'
            self.robot_state = 'running' if self.task_state == 'running' else (
                'paused' if self.task_state == 'paused' else 'idle')
            self.message = '分拣动作完成，已确认返回观察位。'

    def poll(self):
        result = self._request("GET", f"/api/robot/poll?after_seq={self.after_seq}")
        self.poll_failed = bool(self.token) and result is None
        if not result:
            return []
        commands = result.get("commands", [])
        if commands:
            self.after_seq = max(int(item.get("seq", self.after_seq)) for item in commands)
        return commands

    def status(self, detections=None, busy=False, message=None):
        self._request("POST", "/api/robot/status", {
            "task_id": self.task_id,
            "task_state": self.task_state,
            "robot_state": self.robot_state,
            "detections": detections or [],
            "is_busy": bool(busy),
            "message": (message or self.message)[:256],
        })

    def upload_frame(self, frame, frame_id):
        """上传压缩后的 JPEG 帧；失败时不阻断本地识别循环。"""
        if not self.token:
            return
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if not ok:
            return
        request = urllib.request.Request(
            self.base_url + "/api/robot/frame", data=encoded.tobytes(), method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "image/jpeg",
                "X-Frame-Id": str(frame_id),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=1):
                pass
        except (urllib.error.URLError, TimeoutError) as error:
            self.report_error(error)
