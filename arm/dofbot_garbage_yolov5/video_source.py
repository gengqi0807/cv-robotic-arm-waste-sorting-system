"""Latest-frame MJPEG source for PC-side FFmpeg; never opens a second camera."""
import hmac
import atexit
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class LatestCamera:
    """One capture owner: video continues while inference or HTTP status waits."""
    def __init__(self, capture, source):
        self.capture = capture
        self.source = source
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.frame = None
        self.sequence = 0
        self.last_read = 0
        self.updated = 0
        self.thread = threading.Thread(target=self._capture, daemon=True)
        self.thread.start()
        atexit.register(self.release)

    def _capture(self):
        try:
            while not self.stopped.is_set():
                ok, frame = self.capture.read()
                if not ok:
                    self.stopped.wait(.05)
                    continue
                with self.condition:
                    self.frame = frame
                    self.sequence += 1
                    self.updated = time.monotonic()
                    self.condition.notify_all()
                self.source.submit(frame)
        finally:
            self.capture.release()

    def isOpened(self):
        return not self.stopped.is_set()

    def read(self):
        with self.condition:
            ready = self.condition.wait_for(
                lambda: self.sequence != self.last_read or self.stopped.is_set(), timeout=.5)
            if not ready or self.stopped.is_set() or time.monotonic() - self.updated > 1:
                return False, None
            self.last_read = self.sequence
            return True, self.frame.copy()

    def release(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        # The capture thread owns release too; never release the device while
        # another thread is inside a native VideoCapture.read call.
        self.thread.join(timeout=2)


class VideoSource:
    def __init__(self, host='0.0.0.0', port=5001, token='', fps=15):
        if not token:
            raise ValueError('视频源需要 ROBOT_BRIDGE_TOKEN')
        self.token = token
        self.interval = 1 / max(1, min(30, fps))
        self.pending = queue.Queue(maxsize=1)
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.jpeg = None
        self.seq = 0
        self.updated = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path != '/stream':
                    self.send_error(404)
                    return
                supplied = self.headers.get('Authorization', '')
                if not hmac.compare_digest(supplied, 'Bearer ' + owner.token):
                    self.send_error(401)
                    return
                self.connection.settimeout(5)
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                seq = -1
                try:
                    while not owner.stopped.is_set():
                        with owner.condition:
                            owner.condition.wait_for(
                                lambda: (owner.jpeg is not None and owner.seq != seq) or owner.stopped.is_set(), timeout=2)
                            if owner.stopped.is_set():
                                return
                            if time.monotonic() - owner.updated > 5:
                                return
                            if owner.jpeg is None or owner.seq == seq:
                                # A stalled camera must disconnect the publisher,
                                # not repeat an old frame indefinitely.
                                if time.monotonic() - owner.updated > 5:
                                    return
                                continue
                            jpeg, seq = owner.jpeg, owner.seq
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                                         str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n')
                        self.wfile.flush()
                except (OSError, TimeoutError):
                    return

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.worker = threading.Thread(target=self._encode, daemon=True)
        self.listener = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.updated = time.monotonic()
        self.worker.start()
        self.listener.start()
        print(f'[视频源] HTTP MJPEG :{self.server.server_port}/stream，等待PC拉流', flush=True)

    @classmethod
    def from_env(cls, token):
        if os.environ.get('ROBOT_VIDEO_MODE', 'webrtc') == 'jpeg':
            return None
        source = cls(port=int(os.environ.get('ROBOT_VIDEO_PORT', '5001')), token=token,
                     fps=int(os.environ.get('ROBOT_VIDEO_FPS', '15')))
        atexit.register(source.close)
        return source

    def submit(self, frame):
        if self.stopped.is_set():
            return
        # Copy owns the buffer; replacing queued work prevents growing latency.
        try:
            self.pending.get_nowait()
        except queue.Empty:
            pass
        try:
            self.pending.put_nowait(frame.copy())
        except queue.Full:
            pass

    def _encode(self):
        import cv2
        while not self.stopped.is_set():
            try:
                frame = self.pending.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                ok, data = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if ok:
                    with self.condition:
                        self.jpeg = data.tobytes()
                        self.seq += 1
                        self.updated = time.monotonic()
                        self.condition.notify_all()
            except Exception as error:
                print(f'[视频编码失败] {type(error).__name__}', flush=True)
            self.stopped.wait(self.interval)

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.listener.join(timeout=2)
