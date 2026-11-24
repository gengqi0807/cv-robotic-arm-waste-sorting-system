#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只学 4 个“放置点上方过渡点”（place_layer1_above ~ place_layer4_above）

作用：
    放积木时，先到该层的“正上方”，再垂直下降放，避免斜着撞到下面的积木。

摆法：
    和对应的 place_layer<N> 同一个 XY，只是夹爪抬高 10~15cm（离该层积木顶部有明显缝隙）。
    不要左右前后移动，只往上抬。

前置：stack_poses.json 里已经有 place_layer1~4（这次不动它们）。

用法（板子上）：
    cd /home/HwHiAiUser/RobotArm-a/arm_basic
    /usr/bin/python3 teach_place_above4.py
"""
import json
import time

from Arm_Lib import Arm_Device

POSES_FILE = "stack_poses.json"

TEACH_ORDER = [
    ("place_layer1_above", "第 1 层正上方：place_layer1 的 XY，夹爪抬到离桌面约 15cm"),
    ("place_layer2_above", "第 2 层正上方：place_layer2 的 XY，比第 2 层积木顶再高 10~15cm"),
    ("place_layer3_above", "第 3 层正上方：place_layer3 的 XY，比第 3 层积木顶再高 10~15cm"),
    ("place_layer4_above", "第 4 层正上方：place_layer4 的 XY，比第 4 层积木顶再高 10~15cm"),
]

Arm = Arm_Device()
time.sleep(0.3)

poses = {}


def read5():
    vals = []
    for i in range(1, 6):
        a = Arm.Arm_serial_servo_read(i)
        if a is None:
            Arm.Arm_Button_Mode(0)
            raise RuntimeError("舵机 %d 读不到，检查学习模式/排线/电源/串口占用" % i)
        vals.append(int(a))
        time.sleep(0.03)
    return vals


def teach(name, desc, idx):
    Arm.Arm_Button_Mode(1)
    print()
    print("=" * 68)
    print("  [%s]  (%d/4)" % (name, idx))
    print("  %s" % desc)
    print("=" * 68)
    base = poses.get(name.replace("_above", ""))
    if base:
        print("  对应的放置点 = %s" % base)
        print("  → 先摆成这个姿态，然后只往上抬（不要左右前后动）")
    input("  摆好后按回车记录：")
    Arm.Arm_Button_Mode(0)
    time.sleep(0.8)
    angles = read5()
    print("  已记录 %s = %s" % (name, angles))
    poses[name] = angles
    return angles


def main():
    global poses

    with open(POSES_FILE, encoding="utf-8") as f:
        poses = json.load(f)

    print("=" * 68)
    print("  只学 4 个放置点上方过渡点（现有 %d 个点保留）" % len(poses))
    print("  共 4 个点，约 3~5 分钟")
    print("=" * 68)
    print()

    Arm.Arm_Button_Mode(0)
    time.sleep(0.5)
    probe = [Arm.Arm_serial_servo_read(i) for i in range(1, 7)]
    print("六轴读值：", probe)
    if any(v is None for v in probe):
        raise SystemExit("有舵机读不到，先排查：学习模式/排线/电源/串口占用")

    # 先提示四个放置点当前值
    print()
    print("四个放置点当前值：")
    for n in range(1, 5):
        k = "place_layer%d" % n
        if k in poses:
            print("  %-22s %s" % (k, poses[k]))

    for idx, (name, desc) in enumerate(TEACH_ORDER, 1):
        teach(name, desc, idx)

    with open(POSES_FILE, "w", encoding="utf-8") as f:
        json.dump(poses, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 68)
    print("  已保存，共 %d 个点" % len(poses))
    print("=" * 68)
    for k, v in poses.items():
        print("  %-26s %s" % (k, v))

    # 检查：每个 _above 应该和对应放置点 XY 接近，高度更高
    print()
    print("=== 放置点 vs 上方过渡点（j1/j2 应该接近，高度应更高）===")
    for n in range(1, 5):
        a = poses.get("place_layer%d" % n)
        b = poses.get("place_layer%d_above" % n)
        if a and b:
            print("  第 %d 层 放置=%s  过渡=%s" % (n, a, b))
            print("          j1差%+d  j2差%+d  j3差%+d" % (b[0]-a[0], b[1]-a[1], b[2]-a[2]))


if __name__ == "__main__":
    main()