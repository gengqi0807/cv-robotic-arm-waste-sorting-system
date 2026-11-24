#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
叠罗汉 / 积木搬运工 执行器

从 叠罗汉_搬运工_一键执行.ipynb 抽出来的纯 Python 版本，
让 main.py 的 Web 前端按钮可以直接调用。

依赖：
    stack_poses.json   示教点（ready / pick_<color> / pick_<color>_above
                              / place_layer<n> / place_layer<n>_above）

用法：
    from stack_runner import StackRunner
    runner = StackRunner(arm)
    runner.run(top_color="yellow", skip_check=False, on_log=print)
"""
import json
import math
import os
import time

COLORS = ["yellow", "red", "green", "blue"]
OPEN_ANGLE = 60
CLOSE_ANGLE = 125
MOVE_MS = 1500
CLAMP_MS = 500
SETTLE = 0.3


class StackAborted(Exception):
    """前端点了「结束」/「暂停」时抛出，用来中断正在执行的搬运。"""
    pass


class StackRunner:
    def __init__(self, arm, poses_file=None, should_continue=None):
        self.arm = arm
        self.poses_file = poses_file or self._find_poses()
        # 每一步动作前都会调用它；返回 False 就抛 StackAborted 中断。
        # main.py 传的是 lambda: get_task_state() == "running"，
        # 这样网页点「结束」能真正停下正在搬的积木，而不是把 4 块搬完才停。
        self.should_continue = should_continue or (lambda: True)
        self.poses = {}
        self.load()

    def _check(self):
        if not self.should_continue():
            raise StackAborted("任务已结束或暂停，搬运中断")

    def _find_poses(self):
        candidates = [
            "/home/HwHiAiUser/RobotArm-a/arm_basic/stack_poses.json",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "stack_poses.json"),
            "stack_poses.json",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return candidates[0]

    def load(self):
        with open(self.poses_file, encoding="utf-8") as f:
            self.poses = json.load(f)
        return self.poses

    # ---------- 基础动作 ----------
    def arm_move(self, p, s_time=MOVE_MS):
        """只动 1~5 号舵机（不动夹爪）；5 号行程长，时间给 1.2 倍。"""
        self._check()
        for idx, a in enumerate(p):
            sid = idx + 1
            dur = int(s_time * 1.2) if sid == 5 else s_time
            self.arm.Arm_serial_servo_write(sid, int(a), dur)
            time.sleep(0.01)
        # 等舵机走完，每 0.1 秒检查一次是否被要求中断
        remain = s_time * 1.2 / 1000.0 + 0.2
        while remain > 0:
            time.sleep(min(0.1, remain))
            remain -= 0.1
            self._check()

    def move_to(self, name):
        print("    → %s %s" % (name, self.poses[name]), flush=True)
        self.arm_move(self.poses[name])

    def move_above(self, name):
        """有名字对应的 _above 过渡点就走，没有就跳过。

        只学 9 个点（ready + 4 pick + 4 place）时没有 _above，
        直接从 ready 到目标点即可——ready 本身就在高处，够安全。
        """
        above = name + "_above"
        if above in self.poses:
            print("    → %s %s" % (above, self.poses[above]), flush=True)
            self.arm_move(self.poses[above])

    def clamp(self, closed):
        self._check()
        self.arm.Arm_serial_servo_write(6, CLOSE_ANGLE if closed else OPEN_ANGLE, CLAMP_MS)
        remain = 0.7
        while remain > 0:
            time.sleep(min(0.1, remain))
            remain -= 0.1
            self._check()

    # ---------- 校验 ----------
    def check(self):
        # 只强制要求 9 个点；_above 有就顺便校验，没有就跳过
        need = ["ready"]
        for c in COLORS:
            need += ["pick_%s" % c]
        for n in range(1, 5):
            need += ["place_layer%d" % n]
        missing = [n for n in need if n not in self.poses]
        if missing:
            raise RuntimeError("缺少示教点：" + "、".join(missing))
        # _above 是可选点，存在才校验
        optional = []
        for c in COLORS:
            optional += ["pick_%s_above" % c]
        for n in range(1, 5):
            optional += ["place_layer%d_above" % n]
        for n in need + [x for x in optional if x in self.poses]:
            p = self.poses[n]
            if len(p) != 5:
                raise ValueError("%s 必须是 5 个角度" % n)
            for idx, a in enumerate(p):
                limit = 270 if idx == 4 else 180
                if not isinstance(a, (int, float)) or isinstance(a, bool) or not math.isfinite(a):
                    raise ValueError("%s 的第 %d 个角度无效" % (n, idx + 1))
                if not (0 <= a <= limit):
                    raise ValueError("%s 的 %d 号舵机角度 %s 超出 0~%d"
                                     % (n, idx + 1, a, limit))
        return True

    # ---------- 单个搬运 ----------
    def transfer(self, color, layer, on_log=None):
        log = on_log or print
        log("  取 %s" % color)
        self.move_to("ready")
        self.clamp(False)
        self.move_above("pick_%s" % color)
        self.move_to("pick_%s" % color)
        self.clamp(True)
        self.move_above("pick_%s" % color)
        self.move_to("ready")
        log("  放第 %d 层" % layer)
        self.move_above("place_layer%d" % layer)
        self.move_to("place_layer%d" % layer)
        self.clamp(False)
        self.move_above("place_layer%d" % layer)
        self.move_to("ready")
        log("  完成：%s → 第 %d 层" % (color, layer))

    def transfer_back(self, color, layer, on_log=None):
        """积木搬运工用：从第 layer 层把那块抓起来，放回 color 原来的位置。

        这是 transfer() 的镜像：起点/终点对调。
        抓取点在 place_layer<layer>，放置点复用 pick_<color>（积木原本就摆在那）。
        """
        log = on_log or print
        log("  取第 %d 层（%s）" % (layer, color))
        self.move_to("ready")
        self.clamp(False)
        self.move_above("place_layer%d" % layer)
        self.move_to("place_layer%d" % layer)
        self.clamp(True)
        self.move_above("place_layer%d" % layer)
        self.move_to("ready")
        log("  放回 %s 原位" % color)
        self.move_above("pick_%s" % color)
        self.move_to("pick_%s" % color)
        self.clamp(False)
        self.move_above("pick_%s" % color)
        self.move_to("ready")
        log("  完成：第 %d 层 → %s" % (layer, color))

    # ---------- 全流程 ----------
    def build_order(self, top_color="yellow"):
        if top_color not in COLORS:
            raise ValueError("top_color 必须是 %s 之一" % COLORS)
        order = [c for c in COLORS if c != top_color] + [top_color]
        return [(c, i + 1) for i, c in enumerate(order)], order

    def run(self, top_color="yellow", skip_check=False, on_log=None):
        """叠罗汉：四周 4 块 → 中间摞成 4 层。"""
        log = on_log or print
        if not skip_check:
            self.check()
        exec_list, order = self.build_order(top_color)
        log("执行顺序（先放的在下层）：")
        for c, n in exec_list:
            log("    第 %d 层 ← %s" % (n, c))
        for c, n in exec_list:
            self.transfer(c, n, on_log=log)
        log("完成：自下而上 %s（最顶 = %s）" % ("、".join(order), top_color))
        return order

    def run_carry(self, top_color="yellow", skip_check=False, on_log=None):
        """积木搬运工：把中间摞好的 4 块搬回四周原位。

        和 run() 方向相反：
            run()       四周 pick_<色>  →  中间 place_layer<N>（自下而上）
            run_carry() 中间 place_layer<N> → 四周 pick_<色>（自上而下）
        """
        log = on_log or print
        if not skip_check:
            self.check()
        exec_list, order = self.build_order(top_color)
        log("搬运顺序（从上往下取）：")
        for c, n in reversed(exec_list):
            log("    第 %d 层（%s）→ 放回 %s 原位" % (n, c, c))
        for c, n in reversed(exec_list):
            self.transfer_back(c, n, on_log=log)
        log("完成：4 块已全部搬回四周")
        return order

    def run_inspect(self, on_log=None):
        """空手巡检：把所有示教点慢速走一遍，不夹积木。"""
        log = on_log or print
        self.check()
        order = ["ready"]
        for c in COLORS:
            if "pick_%s_above" % c in self.poses:
                order.append("pick_%s_above" % c)
            order.append("pick_%s" % c)
        for n in range(1, 5):
            if "place_layer%d_above" % n in self.poses:
                order.append("place_layer%d_above" % n)
            order.append("place_layer%d" % n)
        for name in order:
            log("  → %s %s" % (name, self.poses[name]))
            self.move_to(name)
            time.sleep(1.0)
        self.move_to("ready")
        log("巡检结束")