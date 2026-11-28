"""Offline only: no camera, TCP listeners, FFmpeg or MediaMTX processes."""
import importlib.util
import os
from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app import create_app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'arm/dofbot_garbage_yolov5'))
from video_source import LatestCamera, VideoSource

spec = importlib.util.spec_from_file_location('start_video', ROOT / 'tools/start_video.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class VideoConfigTests(unittest.TestCase):
    def test_browser_address_and_no_token_exposure(self):
        client = create_app({'TESTING': True, 'ROBOT_BRIDGE_TOKEN': 'never-expose',
                             'VIDEO_WEBRTC_BASE_URL': '', 'VIDEO_TRANSPORT': 'webrtc',
                             'VIDEO_STREAM_PATH': 'robot-camera'}).test_client()
        result = client.get('/api/video/config', base_url='http://192.168.137.101:5000')
        self.assertEqual(result.json['whep_url'], 'http://192.168.137.101:8889/robot-camera/whep')
        self.assertEqual(result.json['transport'], 'webrtc')
        self.assertNotIn('never-expose', result.get_data(as_text=True))
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        local = client.get('/api/video/config', base_url='http://localhost:5000')
        self.assertEqual(local.json['whep_url'], 'http://localhost:8889/robot-camera/whep')

    def test_configuration_override_and_rejection(self):
        client = create_app({'TESTING': True, 'VIDEO_WEBRTC_BASE_URL': 'https://video.example',
                             'VIDEO_STREAM_PATH': 'arm', 'VIDEO_TRANSPORT': 'jpeg'}).test_client()
        self.assertEqual(client.get('/api/video/config').json['whep_url'], 'https://video.example/arm/whep')
        client.application.config['VIDEO_WEBRTC_BASE_URL'] = 'http://user:secret@host'
        self.assertEqual(client.get('/api/video/config').status_code, 503)

    def test_command_is_low_latency_and_keeps_credentials_out_of_url(self):
        command = launcher.ffmpeg_command(Path('ffmpeg.exe'), '192.168.137.100', 'fixed-test', 15, 'robot-camera')
        self.assertIn('zerolatency', command)
        self.assertIn('baseline', command)
        self.assertIn('http://192.168.137.100:5001/stream', command)
        self.assertEqual(command[-1], 'rtsp://127.0.0.1:8554/robot-camera')
        self.assertIn('Authorization: Bearer fixed-test\r\n', command)
        with self.assertRaises(ValueError):
            launcher.ffmpeg_command('ffmpeg', '127.0.0.1', 'bad\r\nHeader: yes', 15, 'arm')

    def test_yaml_and_check_only_never_start_process(self):
        config = launcher.configuration('192.168.137.101', 'robot-camera')
        self.assertIn('webrtcAdditionalHosts: ["192.168.137.101", "127.0.0.1"]', config)
        self.assertIn('rtspAddress: 127.0.0.1:8554', config)
        with patch.object(launcher, 'locate', return_value=Path('fake.exe')), \
             patch.object(launcher, 'load_env'), patch.object(launcher, 'check_ports') as ports, \
             patch.dict(os.environ, {'ROBOT_BRIDGE_TOKEN': 'fixed-test'}), \
             patch.object(sys, 'argv', ['start_video.py', '--check-only']), \
             patch.object(launcher.subprocess, 'Popen') as launch:
            launcher.main()
        launch.assert_not_called()
        ports.assert_not_called()

    def test_latest_frame_queue_replaces_old_work(self):
        source = VideoSource.__new__(VideoSource)
        source.stopped = threading.Event()
        source.pending = queue.Queue(maxsize=1)
        source.submit([1])
        source.submit([2])
        self.assertEqual(source.pending.qsize(), 1)
        self.assertEqual(source.pending.get_nowait(), [2])

    def test_capture_continues_without_inference_consumer(self):
        capture = Mock()
        count = 0
        stop_after = threading.Event()
        def read():
            nonlocal count
            count += 1
            if count <= 3:
                return True, [count]
            stop_after.set()
            return False, None
        capture.read.side_effect = read
        source = Mock()
        camera = LatestCamera(capture, source)
        try:
            self.assertTrue(stop_after.wait(1))
            self.assertEqual(source.submit.call_count, 3)
            ok, frame = camera.read()
            self.assertTrue(ok)
            self.assertEqual(frame, [3])  # No accumulated queue of old frames.
        finally:
            camera.release()
        capture.release.assert_called_once()

    def test_source_requires_token_and_jpeg_mode_is_opt_out(self):
        with self.assertRaises(ValueError):
            VideoSource(token='')
        with patch.dict(os.environ, {'ROBOT_VIDEO_MODE': 'jpeg'}):
            self.assertIsNone(VideoSource.from_env('test'))


if __name__ == '__main__':
    unittest.main()
