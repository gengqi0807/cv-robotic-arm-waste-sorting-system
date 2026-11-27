#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时调姿 · 终端版（不依赖 cv2 的键盘）

为什么有这个：
    cv2.imshow 的窗口在 SSH + X11 转发下经常收不到键盘事件（按 i 没反应就是这个原因）。
    这个版本把"控制"完全搬到终端：画面在后台线程刷新，命令在终端里打，最稳。

用法：
    cd /home/HwHiAiUser/RobotArm-a
    . ./ros2_dofbot_ws/setenv.sh
    export PYTHONPATH=$(ls -d /root/miniconda3/envs/ros-arm-env/lib/python3.10/site-packages/*.egg | tr '\n' ':')/root/miniconda3/envs/ros-arm-env/lib/python3.10/site-packages:$PYTHONPATH
    /usr/bin/python3 live_adjust_cli.py

命令（在终端里逐行输入）：
    s 89,125,0,0,90,30      直接设六个角度（留空=不改，例: s 90,,,,,30）
    j1 +5                   某个关节加 5（可写 j1..j5、g、grip）
    j3 -10                 某个关节减 10
    all                     打印当前六个角度
    copy                    打印可复制的 python 列表
    ready                   回到 [89,125,0,0,90,30]
    open / close            夹爪张开(30) / 夹紧(130)
    q                       退出

画面窗口里按 q 或 ESC 也能退出。
"""
import threading
import time

import cv2
from Arm_Lib import Arm_Device

LIMITS = [(0, 180), (0, 180), (0, 180), (0, 180), (90, 270), (0, 180)]
NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "gripper"]
SHORT = ["j1", "j2", "j3", "j4", "j5", "grip"]

joints = [89, 125, 0, 0, 90, 30]
lock = threading.Lock()
stop_flag = threading.Event()

arm = Arm_Device()
time.sleep(0.2)


def clamp(i, v):
    lo, hi = LIMITS[i]
    return max(lo, min(hi, int(v)))


def apply_and_report():
    with lock:
        snapshot = list(joints)
    arm.Arm_serial_servo_write6_array(snapshot, 600)
    time.sleep(0.4)
    print("  -> [%d, %d, %d, %d, %d, %d]" % tuple(snapshot), flush=True)


def parse_index(tok):
    """把 j1/j2/.../grip/g 解析成下标，失败返回 None"""
    t = tok.lower()
    if t in ("g", "grip", "jar", "夹爪"):
        return 5
    if t.startswith("j") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 5:
            return n - 1
    if t.isdigit() and 1 <= int(t) <= 6:
        return int(t) - 1
    return None


def display_thread():
    """后台线程：只负责把画面显示出来"""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[警告] 摄像头打不开，画面不可用（命令仍然有效）", flush=True)
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    while not stop_flag.is_set():
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        frame = cv2.resize(frame, (640, 480))

        with lock:
            snap = list(joints)
        line = "j1=%3d j2=%3d j3=%3d j4=%3d j5=%3d grip=%3d" % tuple(snap)
        cv2.rectangle(frame, (0, 0), (640, 30), (0, 0, 0), -1)
        cv2.putText(frame, line, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

        cv2.imshow("live_adjust_cli", frame)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord('q'), 27):
            stop_flag.set()
            break

    cap.release()
    cv2.destroyAllWindows()


def print_help():
    print("=" * 64, flush=True)
    print(" 命令：", flush=True)
    print("   s 89,125,0,0,90,30     设六个角度（留空=不改）", flush=True)
    print("   j1 +5 / j3 -10         单关节增减", flush=True)
    print("   all                    打印当前角度", flush=True)
    print("   copy                   打印可复制列表", flush=True)
    print("   ready                  回到 [89,125,0,0,90,30]", flush=True)
    print("   open / close           夹爪张开 / 夹紧", flush=True)
    print("   q                      退出", flush=True)
    print("=" * 64, flush=True)


def main():
    print("=" * 64, flush=True)
    print(" 实时调姿（终端控制）", flush=True)
    print("=" * 64, flush=True)
    print(" 夹爪参考：30=张开  60=半开  130=闭合", flush=True)
    print_help()

    arm.Arm_serial_servo_write6_array(joints, 600)
    time.sleep(0.5)
    print("初始: [%d, %d, %d, %d, %d, %d]" % tuple(joints), flush=True)

    t = threading.Thread(target=display_thread, daemon=True)
    t.start()
    time.sleep(0.5)

    while not stop_flag.is_set():
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue

        low = line.lower()

        # 退出
        if low in ("q", "quit", "exit"):
            break

        # 帮助
        if low in ("h", "help", "?"):
            print_help()
            continue

        # 打印当前
        if low in ("all", "show", "p"):
            with lock:
                snap = list(joints)
            print("  当前: [%d, %d, %d, %d, %d, %d]" % tuple(snap), flush=True)
            continue

        # 可复制列表
        if low in ("copy", "c"):
            with lock:
                snap = list(joints)
            print("  [%d, %d, %d, %d, %d, %d]" % tuple(snap), flush=True)
            continue

        # 回准备位
        if low in ("ready", "r"):
            with lock:
                joints[:] = [89, 125, 0, 0, 90, 30]
            apply_and_report()
            continue

        # 夹爪张开 / 夹紧
        if low in ("open", "o"):
            with lock:
                joints[5] = 30
            apply_and_report()
            continue
        if low in ("close", "k"):
            with lock:
                joints[5] = 130
            apply_and_report()
            continue

        # s 89,125,0,0,90,30
        if low.startswith("s ") or low.startswith("set "):
            body = line.split(" ", 1)[1] if " " in line else ""
            body = body.replace("，", ",").replace(" ", ",")
            parts = [p.strip() for p in body.split(",")]
            changed = []
            with lock:
                for i, p in enumerate(parts[:6]):
                    if not p:
                        continue
                    try:
                        v = clamp(i, int(float(p)))
                    except ValueError:
                        print("  [跳过] %r 不是数字" % p, flush=True)
                        continue
                    if v != joints[i]:
                        joints[i] = v
                        changed.append("%s=%d" % (NAMES[i], v))
            if changed:
                apply_and_report_because("设置 " + ", ".join(changed))
            else:
                print("  (没有变化)", flush=True)
            continue

        # j1 +5 / j3 -10 / g +20
        toks = line.replace("+", " +").replace("-", " -").split()
        if len(toks) >= 2:
            idx = parse_index(toks[0])
            if idx is not None:
                try:
                    delta = int(float(toks[1]))
                except ValueError:
                    print("  [错误] 增量要是数字，例如 j1 +5", flush=True)
                    continue
                with lock:
                    nv = clamp(idx, joints[idx] + delta)
                    old = joints[idx]
                    joints[idx] = nv
                if nv != old:
                    apply_and_report()
                else:
                    print("  %s 已在限位 %d" % (NAMES[idx], nv), flush=True)
                continue

        print("  [不懂] %r  输入 h 看帮助" % line, flush=True)

    stop_flag.set()
    time.sleep(0.3)
    with lock:
        snap = list(joints)
    print("\n退出。最终姿态: [%d, %d, %d, %d, %d, %d]" % tuple(snap), flush=True)
    print("可直接复制:", flush=True)
    print("  [%d, %d, %d, %d, %d, %d]" % tuple(snap), flush=True)


def apply_and_report_because(why):
    print("  " + why, flush=True)
    apply_and_report()


if __name__ == "__main__":
    main()