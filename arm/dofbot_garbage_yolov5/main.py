import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import Arm_Lib
from garbage_identify import garbage_identify, XYT_path
import time
import subprocess
import threading 
import json
import os
import urllib.request
import urllib.error
import numpy as np
from video_source import VideoSource, LatestCamera

# 本地 Web 前端（零依赖，备用）
from web_server import SharedState, start_web_server

# 叠罗汉 / 积木搬运工执行器
from stack_runner import StackRunner, StackAborted

# 联调模式：连电脑上的 Flask（同学的 robot_bridge.py，会自动读 robot_bridge.env）
try:
    from robot_bridge import RobotBridge
except Exception:
    RobotBridge = None

# (1) 导入包
# (2) 定义全局变量
dp = [] # 存放透视变换四个顶点的坐标



# 注意：RobotBridge 由上面的 from robot_bridge import RobotBridge 提供（同学那版，会读 robot_bridge.env）。
# 之前这里有一份内置的简化版 RobotBridge 类，会把导入的类覆盖掉，导致永远用 127.0.0.1，已删除。

def read_XYT(rf_path):
    """
    (3) 读取 XYT_config.txt 文件中相机校准后 1号、2号舵机的角度值
    """
    dict_val = {}
    try:
        with open(rf_path, "r") as rf:
            for line in rf.readlines():
                if '=' in line:
                    index = line.find('=')
                    # 去除空格和换行
                    key = line[:index].strip()
                    val = line[index+1:].strip()
                    dict_val[key] = val
        xy = [int(dict_val['x']), int(dict_val['y'])]
        return xy
    except Exception as e:
        print("Read XYT_config Error !!!", e)
        return [90, 130] # 默认值

# (3.5) 读取透视变换参数(课程任务5要求,原代码遗漏)
def load_dp():
    """
    从 config/dp.bin 读取相机校准时保存的十字标定板四个角点。

    课程任务5「任务指导」要求:"读取透视变换参数,并调用 Perspective_transform
    函数对图像进行透视变换"。原代码只有 Perspective_transform 函数,没有读取
    参数的代码,dp 一直是空列表,导致透视变换被跳过、画面始终是原始斜视图。

    dp.bin 由 camera.ipynb 标定时生成,内容为 4 个 int32 坐标(共 8 个整数)。
    读取失败时 dp 保持为空,Perspective_transform 会自动跳过,行为与改动前一致,
    不会让程序崩溃。
    """
    global dp
    dp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'dp.bin')

    if not os.path.exists(dp_path):
        print("[警告] dp.bin 不存在,透视变换将被跳过:", dp_path)
        return False

    try:
        raw = np.fromfile(dp_path, dtype=np.int32)
        if raw.size < 8:
            print("[警告] dp.bin 数据不足(需要 8 个整数,实际 %d 个),透视变换跳过" % raw.size)
            return False

        # 文件末尾可能带多余字节(如换行符 0x0a),按 int32 读取时会被自动忽略
        pts = raw[:8].reshape(4, 2).astype(np.float32)

        # 按坐标重新排成"左上、左下、右下、右上",防止文件里点的存放顺序变化导致画面画歪
        by_x = pts[np.argsort(pts[:, 0])]       # 按 x 升序:前两个在左,后两个在右
        left, right = by_x[:2], by_x[2:]
        left = left[np.argsort(left[:, 1])]     # 左侧按 y 升序:上是左上,下是左下
        right = right[np.argsort(right[:, 1])]  # 右侧同理
        dp = [left[0], left[1], right[1], right[0]]

        print("已加载标定四角(左上,左下,右下,右上):")
        for _n, _p in zip(["左上", "左下", "右下", "右上"], dp):
            print("    %s = (%.0f, %.0f)" % (_n, _p[0], _p[1]))
        return True
    except Exception as e:
        print("[警告] 读取 dp.bin 失败,透视变换跳过:", e)
        return False

# (4) 定义透视变换方法
def Perspective_transform(image):
    global dp
    if len(dp) != 4:
        return image
        
    # 原图像4个顶点坐标
    pts1 = np.float32([dp[0], dp[1], dp[2], dp[3]])
    # 期望变换后的4个顶点坐标
    pts2 = np.float32([[0, 0], [0, 480], [640, 480], [640, 0]])
    
    # 计算透视变换矩阵 M
    M = cv2.getPerspectiveTransform(pts1, pts2)
    # 应用透视变换
    Transform_img = cv2.warpPerspective(image, M, (640, 480))
    return Transform_img

# (5) 实现垃圾分拣主程序
def main():
    # ====== 新增：在同一个终端后台启动逆解服务 ======
    print("正在后台启动逆解服务...")
    # 替换为你实际启动逆解的包名和节点名！
    service_process = subprocess.Popen(["ros2", "run", "dofbot_moveit", "dofbot_server"]) 
    time.sleep(2) # 给服务一点启动时间
    
    # 初始化 ROS
    rclpy.init()
    
    # 调用 read_XYT 读取 1号、2号舵机角度值
    xy = read_XYT(XYT_path)
    print("Read xy is:", xy)

    # 读取标定四角,启用透视变换
    # (dp.bin 缺失或损坏时自动跳过,行为与改动前一致)
    load_dp()
    
    # 利用读取的角度值初始化机械臂
    arm = Arm_Lib.Arm_Device()
    joints_0 = [xy[0], xy[1], 0, 0, 90, 30] 
    arm.Arm_serial_servo_write6_array(joints_0, 1000)
    time.sleep(1)

    # 实例化垃圾识别与抓取类
    garbage_robot = garbage_identify()

    # ================= 前端连接 =================
    # ROBOT_BRIDGE_LOCAL=0  -> 联调模式：连电脑上的 Flask（同学那套页面 + MySQL）
    # 其他值 / 不设         -> 本地模式：板子自己起 Web 服务（零依赖，无数据库）
    use_flask = os.environ.get("ROBOT_BRIDGE_LOCAL", "1") == "0"

    state = None
    bridge = None
    if use_flask and RobotBridge is not None:
        bridge = RobotBridge()
        ok = bridge.connect()
        # 一启动就直接开始分拣（页面上的“暂停/结束”仍然有效）
        bridge.task_state = "running"
        bridge.robot_state = "running"
        bridge.message = "机械臂已启动，正在识别并分拣"
        bridge.status(message=bridge.message)
        print("[联调模式] Flask=%s  连接=%s" % (bridge.base_url, "成功" if ok else "失败（会持续重试）"))
        if not ok:
            print("           检查：① 电脑上 python app.py 在跑 ② 防火墙放行 5000 ③ IP 对不对")
    else:
        state = SharedState()
        state.set_status(task_state="running", robot_state="running",
                         message="已启动，正在识别并分拣")
        try:
            start_web_server(state, port=5000)
        except OSError as e:
            print("[Web] 启动失败（端口 5000 可能被占）:", e)
        print("[本地模式] 控制台: http://<板子IP>:5000/")

    # 统一的状态/画面上报入口，主循环只调这里
    def report(**kw):
        if bridge is not None:
            dets = kw.get("detections")
            busy = kw.get("is_busy", False)
            msg = kw.get("message") or bridge.message
            bridge.status(detections=dets, busy=busy, message=msg)
        elif state is not None:
            state.set_status(**kw)

    video_source = VideoSource.from_env(bridge.token) if bridge is not None else None

    def push_frame(frame_out):
        # 注意：frame_out 是 numpy 图像（原始帧），不是已编码的 bytes。
        #   联调模式：robot_bridge.upload_frame 内部自己 cv2.imencode，必须传 numpy。
        #   本地模式：SharedState 存的是 bytes，这里自己编码。
        if video_source is not None:
            return  # The capture thread streams raw frames independently.
        elif bridge is not None:
            bridge.upload_frame(frame_out, "frame_%d" % int(time.time() * 1000))
        elif state is not None:
            ok, jpeg = cv2.imencode(".jpg", frame_out, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ok:
                state.set_frame(jpeg.tobytes())

    def get_task_state():
        if bridge is not None:
            return bridge.task_state
        return state.task_state

    # ================= 抓取事件上报 =================
    # 同学 Flask 的记录页读的是 /api/events 表，板子必须 POST 才有数据。
    # 注意：app.py 里 class_id 只接受 recyclable / kitchen / hazardous / other，
    #       传颜色名（Red/Blue/...）会 400，一条都存不进去。
    # garbage_identify.get_pos 返回的 arm_xy = ((px-320)/4000, (480-py)/3000)，
    # 这里反推像素坐标；置信度模型没返回，先用 0.95 占位。
    COLOR_TO_CLASS = {
        "Red": "hazardous",     # 有害垃圾
        "Blue": "recyclable",   # 可回收物
        "Green": "kitchen",     # 厨余垃圾
        "Yellow": "other",      # 其他垃圾
    }

    def post_event(class_id, arm_xy, duration_ms, result="success", error_code=None):
        if bridge is None:
            return
        try:
            u_px = arm_xy[0] * 4000 + 320
            v_px = 480 - arm_xy[1] * 3000
            mapped = COLOR_TO_CLASS.get(str(class_id), "other")
            payload = {
                "event_id": "evt_%d_%s" % (int(time.time() * 1000), mapped),
                "task_id": "sort_%d" % int(time.time()),
                "timestamp_ms": int(time.time() * 1000),
                "class_id": mapped,
                "confidence": 0.95,
                "u_px": round(u_px, 1),
                "v_px": round(v_px, 1),
                "target_x_m": round(arm_xy[0], 4),
                "target_y_m": round(arm_xy[1], 4),
                "target_z_m": None,
                "result": result,
                "error_code": error_code,
                "duration_ms": int(duration_ms),
                "calibration_id": "calib_local",
            }
            req = urllib.request.Request(
                bridge.base_url + "/api/events",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                print("[记录] 已上报 %s(%s) %s -> HTTP %d"
                      % (class_id, mapped, result, resp.status))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            print("[记录] 上报被拒 HTTP %s: %s" % (e.code, body))
        except Exception as e:
            print("[记录] 上报失败:", e)

    # ================= 课程任务动作 =================
    # 前端点“课程任务演示”里的按钮 -> /api/tasks/action -> 入队 ->
    # 板子 poll 到 command="action" -> 这里执行。
    # 简单动作（读舵机/蜂鸣器/RGB/摆动）直接在这里做；
    # 复杂动作（叠罗汉/搬运/跳舞/语音）调用仓库里的脚本或留给后续。
    _action_lock = threading.Lock()

    def handle_action(action, args):
        if not _action_lock.acquire(blocking=False):
            print("[动作] 上一个动作还没结束，忽略:", action)
            report(message="上一个动作还在执行，请稍候。")
            return

        def _run():
            try:
                # 叠罗汉 / 搬运工 / 跳舞 这类长动作，开始前把任务状态强制置为 running。
                # 否则之前点过「结束」→ task_state 是 finished →
                # should_continue 第一次检查就返回 False，动作刚到 ready 就被中止。
                if action in ("stack", "carry", "dance", "wave"):
                    if bridge is not None:
                        bridge.task_state = "running"
                        bridge.robot_state = "running"
                    if state is not None:
                        state.set_status(task_state="running", robot_state="running")

                print("[动作] 开始:", action)
                report(message="执行中：%s" % action, is_busy=True)
                if action == "read_servo":
                    vals = [arm.Arm_serial_servo_read(i) for i in range(1, 7)]
                    txt = "舵机角度 1-6: %s" % vals
                    print("[动作]", txt)
                    report(message=txt)

                elif action == "buzzer":
                    arm.Arm_Buzzer_On(1)
                    time.sleep(0.15)
                    arm.Arm_Buzzer_Off()
                    report(message="蜂鸣器响了一声")

                elif action == "rgb":
                    done = False
                    for cname, rgb in (("红", (255, 0, 0)), ("绿", (0, 255, 0)), ("蓝", (0, 0, 255))):
                        try:
                            arm.Arm_RGB_set(*rgb)
                            done = True
                        except TypeError:
                            try:
                                arm.Arm_RGB_set(rgb)
                                done = True
                            except Exception as e:
                                print("[RGB] 调用失败:", e)
                                break
                        time.sleep(0.7)
                    report(message="RGB 灯已演示（红/绿/蓝）" if done else "RGB 调用失败，检查扩展板")

                elif action == "wave":
                    base = [arm.Arm_serial_servo_read(i) for i in range(1, 7)]
                    base = [v if v is not None else 90 for v in base]
                    seq = [
                        [base[0] - 20, base[1], base[2], base[3], 90, base[5]],
                        [base[0] + 20, base[1], base[2], base[3], 90, base[5]],
                        [base[0], base[1] + 15, base[2], base[3], 90, base[5]],
                        [base[0], base[1] - 15, base[2], base[3], 90, base[5]],
                        base,
                    ]
                    for pose in seq:
                        arm.Arm_serial_servo_write6_array(pose, 700)
                        time.sleep(0.9)
                    report(message="上下左右摆动完成")

                elif action == "dance":
                    seq = [
                        [70, 120, 30, 30, 90, 30],
                        [110, 120, 30, 30, 90, 120],
                        [90, 90, 60, 50, 90, 30],
                        [70, 100, 40, 40, 90, 120],
                        [110, 100, 40, 40, 90, 30],
                        [90, 120, 30, 30, 90, 60],
                    ]
                    for _ in range(2):
                        for pose in seq:
                            arm.Arm_serial_servo_write6_array(pose, 600)
                            time.sleep(0.75)
                    report(message="跳舞动作完成")

                elif action == "stack":
                    try:
                        runner = StackRunner(arm,
                                             should_continue=lambda: get_task_state() == "running")
                        top = (args or {}).get("top_color", "yellow")
                        runner.run(top_color=top, on_log=lambda s: print("[叠罗汉]", s))
                        report(message="叠罗汉完成（最顶 = %s）" % top)
                    except StackAborted as e:
                        print("[叠罗汉] 已中断:", e)
                        report(message="叠罗汉已中断：%s" % e)
                    except Exception as e:
                        print("[叠罗汉] 失败:", e)
                        report(message="叠罗汉失败：%s" % e)

                elif action == "carry":
                    try:
                        runner = StackRunner(arm,
                                             should_continue=lambda: get_task_state() == "running")
                        top = (args or {}).get("top_color", "yellow")
                        runner.run_carry(top_color=top,
                                         on_log=lambda s: print("[搬运工]", s))
                        report(message="积木搬运工完成（已搬回四周）")
                    except StackAborted as e:
                        print("[搬运工] 已中断:", e)
                        report(message="积木搬运工已中断：%s" % e)
                    except Exception as e:
                        print("[搬运工] 失败:", e)
                        report(message="积木搬运工失败：%s" % e)

                elif action == "asr":
                    report(message="语音识别：请在终端按 常用命令.md 第四节跑")

                elif action == "grab_once":
                    # 先把机械臂送回识别位，让摄像头重新对准工作区，
                    # 否则它会停在叠罗汉/搬运工结束时的姿态上，识别框全偏。
                    arm.Arm_serial_servo_write6_array(
                        [xy[0], xy[1], 0, 0, 90, 30], 1000)
                    time.sleep(1.2)
                    report(message="已回到识别位，等分拣循环识别到目标就会抓",
                           robot_state="running")

                else:
                    report(message="未知动作：%s" % action)

                print("[动作] 完成:", action)
            except Exception as e:
                print("[动作] 失败:", action, e)
                report(message="动作失败：%s" % e)
            finally:
                try:
                    report(is_busy=False)
                except Exception:
                    pass
                _action_lock.release()

        threading.Thread(target=_run, daemon=True).start()

    # 打开摄像头
    capture = cv2.VideoCapture(0)
    frame_count = 0
    grab_lock = threading.Lock()
    last_wait_message = None
    _fps_t0 = time.time()
    _fps_n = 0
    _poll_counter = 0
    # 多帧确认参数：连续 STABLE_FRAMES 帧同一颜色 + 坐标差 <= POS_TOL（米）才抓
    STABLE_FRAMES = 3
    POS_TOL = 0.02
    _recent_msgs = []

    # 新增：定义抓取线程函数，防止阻塞画面刷新
    def run_grab(msg):
        garbage_robot.garbage_grap(msg, xy)

    if capture.isOpened():
        if video_source is not None:
            capture = LatestCamera(capture, video_source)
        while rclpy.ok():
            # ---- 每 10 帧轮询一次前端命令（start/pause/resume/finish/action）----
            _poll_counter += 1
            if bridge is not None and _poll_counter % 10 == 0:
                try:
                    for _cmd in bridge.poll():
                        _name = _cmd.get("command")
                        if _name == "start":
                            bridge.task_state = "running"
                            bridge.robot_state = "running"
                            bridge.message = "任务运行中。"
                        elif _name == "pause":
                            bridge.task_state = "paused"
                            bridge.robot_state = "paused"
                            bridge.message = "已暂停后续抓取。"
                        elif _name == "resume":
                            bridge.task_state = "running"
                            bridge.robot_state = "running"
                            bridge.message = "任务已继续。"
                        elif _name == "finish":
                            bridge.task_state = "finished"
                            bridge.robot_state = "idle"
                            bridge.message = "任务已结束。"
                        elif _name == "action":
                            handle_action(_cmd.get("action"), _cmd.get("args") or {})
                except Exception as _e:
                    print("[poll] 轮询失败:", _e)

            ret, frame = capture.read()
            if not ret:
                time.sleep(0.05)
                continue

            frame = cv2.resize(frame, (640, 480))

            # 执行透视变换
            transform_frame = Perspective_transform(frame)

            # 执行垃圾识别
            frame_out, msg = garbage_robot.garbage_run(transform_frame)

            # ---- 多帧确认：过滤单帧误检 ----
            # NPU 偶尔会在某一帧把绿色认成红色、或者把桌面当成积木。
            # 只凭一帧就抓，机械臂会直接跑去错误位置。
            # 做法：按“每个颜色分别判断”——
            #   该颜色在最近 STABLE_FRAMES 帧里都出现，且坐标波动 <= POS_TOL，
            #   才算稳定目标。画面里同时有多块积木时，各自独立判断，
            #   稳定的那几块一起交给 garbage_grap，它会一块一块抓。
            _recent_msgs.append(dict(msg))
            if len(_recent_msgs) > STABLE_FRAMES:
                _recent_msgs.pop(0)
            stable_msg = None
            if len(_recent_msgs) == STABLE_FRAMES:
                _common = set(_recent_msgs[0].keys())
                for _m in _recent_msgs[1:]:
                    _common &= set(_m.keys())
                _stable = {}
                for _name in _common:
                    _xs = [_m[_name][0] for _m in _recent_msgs]
                    _ys = [_m[_name][1] for _m in _recent_msgs]
                    if (max(_xs) - min(_xs) <= POS_TOL) and (max(_ys) - min(_ys) <= POS_TOL):
                        _stable[_name] = _recent_msgs[-1][_name]
                if _stable:
                    stable_msg = _stable
            _stability = "+".join(stable_msg.keys()) if stable_msg else "确认中"

            # ---- 同步状态给前端 ----
            # 动作执行期间（叠罗汉/搬运工/读舵机…）不要覆盖它自己的提示，
            # 否则页面永远显示“任务运行中，正在识别并等待可抓取目标”。
            _busy_action = _action_lock.locked()
            if msg and get_task_state() != "running":
                wait_message = "已识别到积木：%s（%s）；点页面上的「开始」即可抓取。" % (
                    ", ".join(msg.keys()), _stability)
                if wait_message != last_wait_message:
                    print(wait_message)
                    last_wait_message = wait_message
                report(detections=list(msg.keys()), is_busy=garbage_robot.is_busy,
                       message=wait_message, robot_state="idle")
            elif not _busy_action:
                if stable_msg:
                    _m = "任务运行中，目标已稳定（%s）" % "+".join(stable_msg.keys())
                elif msg:
                    _m = "任务运行中，目标确认中（需连续 %d 帧一致）" % STABLE_FRAMES
                else:
                    _m = "任务运行中，正在识别并等待可抓取目标。"
                report(detections=list(msg.keys()), is_busy=garbage_robot.is_busy,
                       message=_m)
                last_wait_message = None
            else:
                # 动作进行中：只更新识别结果，保留动作自己的 message
                report(detections=list(msg.keys()), is_busy=True)

            # 帧率和画面（每 3 帧推一帧给前端，省带宽）
            _fps_n += 1
            _el = time.time() - _fps_t0
            _fps = 0.0
            if _el >= 1.0:
                _fps = _fps_n / _el
                _fps_t0 = time.time()
                _fps_n = 0
            if video_source is not None or frame_count % 3 == 0:
                push_frame(frame_out)

            # 可视化显示（无显示器时自动跳过，不影响运行）
            try:
                cv2.namedWindow("Garbage Identify", cv2.WINDOW_NORMAL)
                cv2.resizeWindow("Garbage Identify", 960, 720)
                cv2.imshow("Garbage Identify", frame_out)
                _key = cv2.waitKey(1) & 0xFF
            except cv2.error:
                _key = 255

            # 每隔 5 帧执行一次抓取（且必须多帧确认稳定）
            if frame_count % 5 == 0:
                # 分拣抓取：叠罗汉/搬运工/跳舞等动作执行期间必须暂停，
                # 否则两条线程同时下发 servo_write6，机械臂会抽风。
                # stable_msg 为 None 时不抓：避免单帧误识别（绿变红、桌面变积木）导致抓错位置。
                if (stable_msg and not garbage_robot.is_busy
                        and get_task_state() == "running"
                        and not _action_lock.locked()
                        and grab_lock.acquire(blocking=False)):
                    print("Start Grab:", stable_msg)
                    _grab_msg = dict(stable_msg)
                    def grab_and_report():
                        _t0 = time.time()
                        target_name = next(iter(_grab_msg))
                        arm_xy = _grab_msg[target_name]
                        try:
                            # 抓之前先回固定观察位：保证每次都从同一个起点出发，
                            # 否则上一次抓完停在别处，这一次的视觉/逆解都会偏。
                            try:
                                arm.Arm_serial_servo_write6_array(
                                    [xy[0], xy[1], 0, 0, 90, 30], 1000)
                                time.sleep(1.2)
                            except Exception as _e:
                                print("[回位] 失败:", _e)
                            garbage_robot.garbage_grap(_grab_msg, xy)
                            report(is_busy=False, message="抓取动作完成。")
                            if bridge is not None:
                                bridge.complete_motion(None)
                            post_event(target_name, arm_xy,
                                       int((time.time() - _t0) * 1000),
                                       result="success")
                        except Exception as error:
                            print("抓取执行失败:", error)
                            report(robot_state="fault", is_busy=False,
                                   message="抓取执行失败：%s" % error)
                            if bridge is not None:
                                bridge.complete_motion(error)
                            post_event(target_name, arm_xy,
                                       int((time.time() - _t0) * 1000),
                                       result="failed",
                                       error_code=str(error)[:60])
                        finally:
                            grab_lock.release()
                    threading.Thread(target=grab_and_report, daemon=True).start()

            # 如果机械臂正在抓取，暂停几帧的显示更新，防止镜头晃动造成视觉误判
            if garbage_robot.is_busy:
                if _key == 27:
                    break
                continue
            frame_count += 1

            if _key == 27:
                break
    else:
        print("Camera Open Failed!")

    capture.release()
    if video_source is not None:
        video_source.close()
    cv2.destroyAllWindows()

    # ====== 新增：退出时清理后台进程 ======
    service_process.terminate() 
    if bridge is not None:
        bridge.task_state = "finished"
        bridge.robot_state = "idle"
        bridge.status(busy=False, message="机械臂客户端已停止。")
    if state is not None:
        state.set_status(task_state="finished", robot_state="idle",
                         is_busy=False, message="机械臂客户端已停止。")
    rclpy.shutdown()

if __name__ == '__main__':
    main()
