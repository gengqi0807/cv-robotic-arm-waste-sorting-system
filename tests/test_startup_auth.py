"""Offline startup-pose and command gate tests; never connect to hardware."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import io
import json

ROBOT_PACKAGE = (Path(__file__).resolve().parents[2] / 'robotic-arm/RobotArm-a/ros2_dofbot_ws/src/dofbot_garbage_yolov5/dofbot_garbage_yolov5')
sys.path.insert(0, str(ROBOT_PACKAGE))
from startup_auth import face_pose, move_verified
from robot_bridge import RobotBridge


class StartupAuthTests(unittest.TestCase):
    def test_pose_reuses_stack_ready_without_moving_gripper(self):
        ready = json.loads((ROBOT_PACKAGE.parents[3] / 'arm_basic/stack_poses.json').read_text())['ready']
        self.assertEqual(face_pose(), ready)
        arm = Mock()
        arm.Arm_serial_servo_read.side_effect = lambda sid: ready[sid-1]
        move_verified(arm, ready, sleep=lambda _: None)
        self.assertEqual([call.args[0] for call in arm.Arm_serial_servo_write.call_args_list], [1,2,3,4,5])

    def test_failed_feedback_cannot_report_ready(self):
        arm = Mock()
        arm.Arm_serial_servo_read.return_value = None
        clock = iter([0, 0.1, 4])
        with self.assertRaisesRegex(RuntimeError, '禁止认证'):
            move_verified(arm, [90]*5, sleep=lambda _: None, clock=lambda: next(clock))

    def test_old_boot_or_missing_session_cannot_initiate_actions(self):
        bridge = RobotBridge.__new__(RobotBridge)
        bridge.boot_id = 'current'
        bridge.session_id = None
        bridge.check_authorization = Mock(return_value=True)
        self.assertFalse(bridge.accept_command({'command':'start', 'boot_id':'old', 'session_id':'s'}))
        self.assertFalse(bridge.accept_command({'command':'start', 'boot_id':'current'}))
        bridge.check_authorization.assert_not_called()
        self.assertTrue(bridge.accept_command({'command':'start', 'boot_id':'current', 'session_id':'s'}))
        self.assertTrue(bridge.accept_command({'command':'finish'}))

    def test_session_created_before_boot_is_rejected(self):
        bridge = RobotBridge.__new__(RobotBridge)
        bridge.base_url = 'http://localhost'
        bridge.session_id = None
        bridge.boot_id = 'current'
        bridge.boot_timestamp_ms = 200
        for created, role, expected in [(100,'operator',False),(300,'manager',False),(300,'operator',True)]:
            payload = {'authorized':True, 'role':role, 'created_at':created, 'robot_boot_id':'current'}
            with patch('robot_bridge.urllib.request.urlopen') as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
                self.assertEqual(bridge.check_authorization('session'), expected)
