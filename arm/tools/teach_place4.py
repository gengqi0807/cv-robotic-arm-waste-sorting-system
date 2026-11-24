#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只学 4 个放置点（place_layer1~4），不改动已有的其它点

适用场景：
    抓取的 9 个点（ready + 4 pick + 4 pick_above）已经学好了，
    现在只需要补“中间那一摞”的 4 个落点。

行为：
    - 读现有 stack_poses.json（保留 ready / pick_* / pick_*_above）
    - 只更新 place_layer1 ~ place_layer4
    - 学完直接写回，不会丢掉其它 9 个点

摆放要点：
    4 个点的 XY **完全一样**（都在中间那摞的正上方），只有高度逐层增加。
    place_layer1 = 积木贴桌面
    place_layer2 = 搭在第 1 块上
    place_layer3 = 搭在第 2 块上
    place_layer4 = 搭在第 3 块上（最顶）

用法（板子上）：
    cd /home/HwHiAiUser/RobotArm-a/arm_basic
    /usr/bin/python3 teach_place4.py
"""
import json
import time

from Arm_Lib import Arm_Device

POSES_FILE = "stack_poses.json"

TEACH_ORDER = [
    ("place_layer1", "第 1 层：积木贴桌面（夹爪松开时积木刚好落在桌上）"),
    ("place_layer2", "第 2 层：放到第 1 块上面（夹爪松开时积木刚好搭住）"),
    ("place_layer3", "第 3 层：放到第 2 块上面"),
    ("place_layer4", "第 4 层：放到第 3 块上面（最顶）"),
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
            raise RuntimeError(
                "舵机 %d 读不到。检查：① 是否还在学习模式 ② 扩展板电源/蓝灯 "
                "③ 排线 ④ 串口是否被其它程序占用" % i)
        vals.append(int(a))
        time.sleep(0.03)
    return vals


def teach(name, desc, idx):
    Arm.Arm_Button_Mode(1)
    print()
    print("=" * 66)
    print("  [%s]  (%d/4)" % (name, idx))
    print("  %s" % desc)
    print("=" * 66)
    input("  把机械臂掰到该位置，扶稳后按回车记录：")
    Arm.Arm_Button_Mode(0)
    time.sleep(0.8)
    angles = read5()
    print("  已记录 %s = %s" % (name, angles))
    poses[name] = angles
    return angles


def main():
    global poses

    # 读现有 json（保留其它 9 个点）
    with open(POSES_FILE, encoding="utf-8") as f:
        poses = json.load(f)
    print("=" * 66)
    print("  只学 4 个放置点（已有 %d 个点会保留）" % len(poses))
    print("  共 4 个点，约 3~5 分钟")
    print("=" * 66)
    print("现有：", ", ".join(sorted(poses.keys())))
    print()

    Arm.Arm_Button_Mode(0)
    time.sleep(0.5)
    probe = [Arm.Arm_serial_servo_read(i) for i in range(1, 7)]
    print("六轴读值：", probe)
    if any(v is None for v in probe):
        raise SystemExit("有舵机读不到，先排查：学习模式/排线/电源/串口占用")

    for idx, (name, desc) in enumerate(TEACH_ORDER, 1):
        teach(name, desc, idx)

    with open(POSES_FILE, "w", encoding="utf-8") as f:
        json.dump(poses, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 66)
    print("  已保存，共 %d 个点" % len(poses))
    print("=" * 66)
    for k, v in poses.items():
        print("  %-24s %s" % (k, v))

    # 检查 4 层高度顺序（j3 应该递增）
    print()
    print("=== 四层 j3（判断高度顺序）===")
    j3s = []
    for n in range(1, 5):
        k = "place_layer%d" % n
        if k in poses:
            j3s.append(poses[k][2])
            print("  第 %d 层 j3 = %d" % (n, poses[k][2]))
    if len(j3s) == 4:
        if all(j3s[i] < j3s[i + 1] for i in range(3)):
            print("  ✓ 高度顺序正确（逐层递增）")
        else:
            print("  !! 高度顺序可能不对，检查哪一层没抬够")


if __name__ == "__main__":
    main()