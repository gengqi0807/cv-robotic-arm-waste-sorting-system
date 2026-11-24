#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
叠罗汉 13 点示教（全部手掰）

13 个点：
    ready               高处准备位
    pick_yellow         抓取：黄色
    pick_yellow_above   黄色上方过渡点
    pick_red            抓取：红色
    pick_red_above      红色上方过渡点
    pick_green          抓取：绿色
    pick_green_above    绿色上方过渡点
    pick_blue           抓取：蓝色
    pick_blue_above     蓝色上方过渡点
    place_layer1        放置：第 1 层
    place_layer2        放置：第 2 层
    place_layer3        放置：第 3 层
    place_layer4        放置：第 4 层

说明：
    - 过渡点是“先到上方、再垂直下降”用的，防止机械臂斜着撞到旁边的积木。
    - 过渡点的 XY 和对应抓取点一样，只是高一点（j3 小 10~15 度）。
    - 放置点不需要过渡点：ready 本身在高处，从 ready 直接到 place_layer 是安全的。

用法（板子上）：
    cd /home/HwHiAiUser/RobotArm-a/arm_basic
    /usr/bin/python3 teach_stack13.py
"""
import json
import time

from Arm_Lib import Arm_Device

POSES_FILE = "stack_poses.json"

TEACH_ORDER = [
    ("ready",             "高处准备位：高于所有木块，不挡摄像头视线"),
    ("pick_yellow",       "抓取：黄色（右前）——夹爪正好夹住积木中心"),
    ("pick_yellow_above", "黄色上方过渡点——和上面同一个 XY，只是抬高 10~15 度"),
    ("pick_red",          "抓取：红色（左前）"),
    ("pick_red_above",    "红色上方过渡点"),
    ("pick_green",        "抓取：绿色（右后）"),
    ("pick_green_above",  "绿色上方过渡点"),
    ("pick_blue",         "抓取：蓝色（左后）"),
    ("pick_blue_above",   "蓝色上方过渡点"),
    ("place_layer1",      "放置：第 1 层（积木贴桌面）"),
    ("place_layer2",      "放置：第 2 层（放到第 1 块上面）"),
    ("place_layer3",      "放置：第 3 层（放到第 2 块上面）"),
    ("place_layer4",      "放置：第 4 层（放到第 3 块上面，最顶）"),
]

Arm = Arm_Device()
time.sleep(0.3)


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


def teach(name, desc):
    Arm.Arm_Button_Mode(1)          # 松扭矩，可手掰
    print()
    print("=" * 66)
    print("  [%s]  (%d/13)" % (name, len(poses_done) + 1))
    print("  %s" % desc)
    print("=" * 66)
    input("  把机械臂掰到该位置，扶稳后按回车记录：")
    Arm.Arm_Button_Mode(0)          # 恢复扭矩
    time.sleep(0.8)
    angles = read5()
    print("  已记录 %s = %s" % (name, angles))
    poses_done[name] = angles
    return angles


poses_done = {}


def main():
    print("=" * 66)
    print("  叠罗汉 13 点示教（全部手掰）")
    print("  每个点：进学习模式 → 手掰 → 回车 → 自动读角度")
    print("  共 13 个点，约 8~15 分钟")
    print("=" * 66)

    Arm.Arm_Button_Mode(0)
    time.sleep(0.5)
    probe = [Arm.Arm_serial_servo_read(i) for i in range(1, 7)]
    print("六轴读值：", probe)
    if any(v is None for v in probe):
        raise SystemExit("有舵机读不到，先排查：学习模式/排线/电源/串口占用")
    print("通信正常，开始示教。")

    for name, desc in TEACH_ORDER:
        teach(name, desc)

    with open(POSES_FILE, "w", encoding="utf-8") as f:
        json.dump(poses_done, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 66)
    print("  已保存 %d 个点到 %s" % (len(poses_done), POSES_FILE))
    print("=" * 66)
    for k, v in poses_done.items():
        print("  %-24s %s" % (k, v))

    # 校验：stack_runner.py 只强制要求这 9 个
    need = ["ready"]
    for c in ["red", "blue", "green", "yellow"]:
        need.append("pick_%s" % c)
    for n in range(1, 5):
        need.append("place_layer%d" % n)
    missing = [n for n in need if n not in poses_done]
    print()
    if missing:
        print("!! 缺少：", "、".join(missing))
    else:
        print("校验通过：9 个必需点齐全，4 个过渡点也已就位（共 13 个）。")


if __name__ == "__main__":
    main()