#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 Web 前端（零依赖，只用 Python 标准库）

为什么不用 Flask：
    板子上没网，pip 装不了东西。这个文件只用 http.server / threading / json，
    Python 自带就能跑。

提供：
    GET  /                  控制页面（HTML）
    GET  /api/state         当前状态 JSON
    POST /api/control       控制指令 ?cmd=start|pause|resume|stop
    GET  /frame             最新一帧 JPEG（用于 <img> 轮询）
    GET  /stream            MJPEG 实时流（更流畅）

用法（在 main.py 里）：
    from web_server import SharedState, start_web_server
    state = SharedState()
    start_web_server(state, port=5000)
    # 每帧：
    state.set_frame(jpeg_bytes)
    state.set_status(detections=["Red"], is_busy=False)
    # 判断：
    if state.task_state == "running": ...
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class SharedState:
    """main.py 和 Web 服务共享的状态（线程安全）"""

    def __init__(self):
        self.lock = threading.Lock()
        self.task_state = "idle"      # idle / running / paused / finished
        self.robot_state = "idle"     # idle / running / paused / fault
        self.message = "等待启动"
        self.detections = []          # 当前识别到的类别列表
        self.is_busy = False          # 机械臂是否正在抓取
        self.jpeg = None              # 最新一帧 JPEG 字节
        self.frame_id = 0             # 帧序号（页面用来判断是否新帧）
        self.fps = 0.0                # 估算帧率

    def snapshot(self):
        with self.lock:
            return {
                "task_state": self.task_state,
                "robot_state": self.robot_state,
                "message": self.message,
                "detections": list(self.detections),
                "is_busy": self.is_busy,
                "frame_id": self.frame_id,
                "fps": round(self.fps, 1),
            }

    def set_frame(self, jpeg):
        with self.lock:
            self.jpeg = jpeg
            self.frame_id += 1

    def get_frame(self):
        with self.lock:
            return self.jpeg, self.frame_id

    def set_status(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def control(self, cmd):
        """返回 (ok, message)"""
        with self.lock:
            if cmd == "start":
                self.task_state = "running"
                self.robot_state = "running"
                self.message = "已启动，正在识别并分拣"
            elif cmd == "pause":
                if self.task_state != "running":
                    return False, "当前不在运行状态"
                self.task_state = "paused"
                self.robot_state = "paused"
                self.message = "已暂停（当前动作会先完成）"
            elif cmd == "resume":
                if self.task_state != "paused":
                    return False, "当前不是暂停状态"
                self.task_state = "running"
                self.robot_state = "running"
                self.message = "已继续"
            elif cmd == "stop":
                self.task_state = "finished"
                self.robot_state = "idle"
                self.message = "已结束"
            else:
                return False, "未知指令: %s" % cmd
            return True, self.message


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>机械臂分拣控制台</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; background: #0f172a; color: #e2e8f0;
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  }
  h1 { font-size: 18px; margin: 0 0 14px; font-weight: 600; letter-spacing: .5px; }
  h1 span { color: #38bdf8; }
  .wrap { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
  .left { flex: 1 1 640px; min-width: 320px; }
  .right { flex: 0 1 320px; min-width: 280px; }
  .card {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 12px; margin-bottom: 12px;
  }
  .card h2 {
    font-size: 13px; margin: 0 0 10px; color: #94a3b8;
    font-weight: 600; text-transform: uppercase; letter-spacing: .8px;
  }
  #view { width: 100%; border-radius: 8px; background: #000; display: block; }
  .row { display: flex; align-items: center; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid #263449; font-size: 14px; }
  .row:last-child { border-bottom: none; }
  .row .k { color: #94a3b8; }
  .row .v { font-weight: 600; }
  .badge { padding: 2px 9px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .b-idle    { background: #334155; color: #cbd5e1; }
  .b-running { background: #14532d; color: #86efac; }
  .b-paused  { background: #78350f; color: #fcd34d; }
  .b-finished{ background: #1e3a8a; color: #93c5fd; }
  .b-fault   { background: #7f1d1d; color: #fca5a5; }
  .btns { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  button {
    padding: 11px 10px; border: none; border-radius: 8px; cursor: pointer;
    font-size: 14px; font-weight: 600; color: #fff; transition: filter .15s;
  }
  button:hover { filter: brightness(1.15); }
  button:active { transform: translateY(1px); }
  button:disabled { opacity: .4; cursor: not-allowed; }
  .start  { background: #16a34a; }
  .pause  { background: #d97706; }
  .resume { background: #2563eb; }
  .stop   { background: #dc2626; }
  #msg { font-size: 13px; color: #94a3b8; line-height: 1.5; min-height: 38px; }
  .dots { display: flex; gap: 6px; flex-wrap: wrap; }
  .dot {
    padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 700;
    background: #334155; color: #cbd5e1;
  }
  .dot.Red    { background: #7f1d1d; color: #fecaca; }
  .dot.Blue   { background: #1e3a8a; color: #bfdbfe; }
  .dot.Green  { background: #14532d; color: #bbf7d0; }
  .dot.Yellow { background: #713f12; color: #fef08a; }
  .hint { font-size: 12px; color: #64748b; margin-top: 8px; line-height: 1.5; }
  @media (max-width: 720px) {
    .wrap { flex-direction: column; }
    .right { width: 100%; }
  }
</style>
</head>
<body>
<h1>机械臂分拣 <span>控制台</span></h1>
<div class="wrap">
  <div class="left">
    <div class="card">
      <h2>实时画面</h2>
      <img id="view" src="/stream" alt="等待画面">
    </div>
  </div>
  <div class="right">
    <div class="card">
      <h2>状态</h2>
      <div class="row"><span class="k">任务</span><span class="v"><span id="task" class="badge b-idle">等待</span></span></div>
      <div class="row"><span class="k">机械臂</span><span class="v"><span id="robot" class="badge b-idle">空闲</span></span></div>
      <div class="row"><span class="k">抓取中</span><span class="v" id="busy">否</span></div>
      <div class="row"><span class="k">帧率</span><span class="v" id="fps">-</span></div>
      <div class="row"><span class="k">识别到</span><span class="v"><span class="dots" id="dets">-</span></span></div>
    </div>

    <div class="card">
      <h2>控制</h2>
      <div class="btns">
        <button class="start"  onclick="ctl('start')">开始</button>
        <button class="pause"  onclick="ctl('pause')">暂停</button>
        <button class="resume" onclick="ctl('resume')">继续</button>
        <button class="stop"   onclick="ctl('stop')">结束</button>
      </div>
      <div class="hint" id="msg">等待启动</div>
    </div>

    <div class="card">
      <h2>说明</h2>
      <div class="hint">
        开始 = 进入分拣循环，识别到积木就抓<br>
        暂停 = 当前动作做完后停止抓新的<br>
        继续 = 恢复抓取<br>
        结束 = 停止任务，机械臂保持当前位置
      </div>
    </div>
  </div>
</div>

<script>
const TASK_CN = {idle:'等待', running:'运行中', paused:'已暂停', finished:'已结束', fault:'故障'};
const ROBOT_CN = {idle:'空闲', running:'运行中', paused:'已暂停', fault:'故障'};

function badge(el, val, map) {
  el.textContent = map[val] || val;
  el.className = 'badge b-' + (val in map ? val : 'idle');
}

async function refresh() {
  try {
    const r = await fetch('/api/state', {cache:'no-store'});
    const s = await r.json();
    badge(document.getElementById('task'), s.task_state, TASK_CN);
    badge(document.getElementById('robot'), s.robot_state, ROBOT_CN);
    document.getElementById('busy').textContent = s.is_busy ? '是' : '否';
    document.getElementById('fps').textContent = s.fps ? s.fps + ' fps' : '-';
    document.getElementById('msg').textContent = s.message || '';

    const box = document.getElementById('dets');
    if (!s.detections || s.detections.length === 0) {
      box.innerHTML = '<span class="dot">无</span>';
    } else {
      box.innerHTML = s.detections.map(d =>
        '<span class="dot ' + d + '">' + d + '</span>').join('');
    }
  } catch (e) {
    document.getElementById('msg').textContent = '连接断开，重试中…';
  }
}

async function ctl(cmd) {
  try {
    const r = await fetch('/api/control?cmd=' + cmd, {method:'POST'});
    const j = await r.json();
    document.getElementById('msg').textContent = j.message || '';
    refresh();
  } catch (e) {
    document.getElementById('msg').textContent = '指令发送失败';
  }
}

refresh();
setInterval(refresh, 500);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    state = None          # 由 start_web_server 注入

    def log_message(self, fmt, *args):
        pass              # 不刷屏

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            return

        if path == "/api/state":
            body = json.dumps(self.state.snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return

        if path == "/frame":
            jpeg, _ = self.state.get_frame()
            if jpeg is None:
                self._send(503, "text/plain", b"no frame yet")
                return
            self._send(200, "image/jpeg", jpeg)
            return

        if path == "/stream":
            self._stream_mjpeg()
            return

        self._send(404, "text/plain", b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/control":
            qs = parse_qs(parsed.query)
            cmd = (qs.get("cmd", [""])[0] or "").strip()
            ok, msg = self.state.control(cmd)
            body = json.dumps({"ok": ok, "message": msg}, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return
        self._send(404, "text/plain", b"not found")

    def _stream_mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last_id = -1
        try:
            while True:
                jpeg, fid = self.state.get_frame()
                if jpeg is None or fid == last_id:
                    time.sleep(0.03)
                    continue
                last_id = fid
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n")
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def start_web_server(state, host="0.0.0.0", port=5000):
    """起一个后台线程跑 HTTP 服务，返回 server 对象"""
    Handler.state = state
    server = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print("[Web] 控制台已启动: http://<板子IP>:%d/" % port, flush=True)
    return server


if __name__ == "__main__":
    # 单独跑测试（没有真实画面，只验证页面能开）
    st = SharedState()
    st.set_status(detections=["Red", "Blue"], is_busy=False, fps=29.7)
    start_web_server(st, port=5000)
    print("单独测试模式，按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass