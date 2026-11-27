#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语音控制：录音 → 识别 → 匹配命令

依赖：
    - arecord（板子自带 alsa-utils）
    - speech_recognition.py（WeNet ASR，在 ~/speech_recognition 下）
    - offline_encoder.om / vocab.txt

注意：
    摄像头和 USB 声卡只能同时用一个（USB 供电不足）。
    录音前必须让 main.py 释放摄像头，录完再打开。
    这个文件只负责“录音+识别+匹配”，摄像头切换由 main.py 控制。
"""
import os
import re
import subprocess
import sys
import time

WAV_PATH = "/tmp/voice_cmd.wav"
SAMPLE_RATE = 16000
CHANNELS = 1
DURATION = 5
ALSA_DEVICE = "plughw:0,0"          # 板子上 USB 声卡是 card 0

# ASR 模型位置（板子上）
ASR_DIR = "/home/HwHiAiUser/speech_recognition"
ASR_MODEL = os.path.join(ASR_DIR, "offline_encoder.om")
ASR_VOCAB = os.path.join(ASR_DIR, "vocab.txt")

# 你说的 → 动作名（和 main.py 的 handle_action 对应）
COMMAND_MAP = [
    (["叠罗汉", "跌罗汉", "叠积木"], "stack"),
    (["搬运工", "搬运", "搬回去", "搬回"], "carry"),
    (["跳舞", "跳个舞"], "dance"),
    (["摆动", "摆一摆", "摇一摇"], "wave"),
    (["蜂鸣器", "响一声", "叫一声"], "buzzer"),
    (["读舵机", "舵机位置", "读角度"], "read_servo"),
    (["语音识别"], "asr"),
    (["停止", "结束", "停下", "别动"], "finish"),
    (["分拣", "开始分拣", "捡垃圾"], "start"),
]


def record(duration=DURATION, device=ALSA_DEVICE, path=WAV_PATH):
    """用 arecord 录一段 wav。返回 True/False。"""
    cmd = [
        "arecord", "-D", device,
        "-f", "S16_LE", "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS), "-d", str(duration), path,
    ]
    print("[语音] 录音 %d 秒（%s）..." % (duration, device), flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 15)
        if r.returncode != 0:
            print("[语音] arecord 失败:", r.stderr[-300:], flush=True)
            return False
        if not os.path.isfile(path) or os.path.getsize(path) < 1000:
            print("[语音] 录音文件为空", flush=True)
            return False
        print("[语音] 录音完成: %s (%.1f KB)" % (path, os.path.getsize(path) / 1024.0), flush=True)
        return True
    except subprocess.TimeoutExpired:
        print("[语音] arecord 超时", flush=True)
        return False
    except Exception as e:
        print("[语音] arecord 异常:", e, flush=True)
        return False


_ASR = None


def load_asr():
    """延迟加载 ASR（第一次调用会加载 75MB 模型，约 2~5 秒）"""
    global _ASR
    if _ASR is not None:
        return _ASR
    if not os.path.isdir(ASR_DIR):
        print("[语音] ASR 目录不存在:", ASR_DIR, flush=True)
        return None
    if ASR_DIR not in sys.path:
        sys.path.insert(0, ASR_DIR)
    try:
        from speech_recognition import WeNetASR
        print("[语音] 加载 ASR 模型...", flush=True)
        _ASR = WeNetASR(ASR_MODEL, ASR_VOCAB)
        print("[语音] ASR 就绪", flush=True)
        return _ASR
    except Exception as e:
        print("[语音] ASR 加载失败:", e, flush=True)
        return None


def transcribe(path=WAV_PATH):
    asr = load_asr()
    if asr is None:
        return ""
    try:
        text = asr.transcribe(path)
        print("[语音] 识别结果: %r" % text, flush=True)
        return (text or "").strip()
    except Exception as e:
        print("[语音] 识别失败:", e, flush=True)
        return ""


def match_command(text):
    """把识别出的文字匹配成动作名。返回 (action, keyword) 或 (None, None)"""
    if not text:
        return None, None
    # 去掉空格/标点
    clean = re.sub(r"[\s，。、！？,.!?]", "", text)
    for keywords, action in COMMAND_MAP:
        for kw in keywords:
            if kw in clean:
                return action, kw
    return None, None


def listen_once(duration=DURATION):
    """完整流程：录音 → 识别 → 匹配。返回 dict"""
    result = {
        "ok": False,
        "text": "",
        "action": None,
        "keyword": None,
        "error": None,
    }
    if not record(duration):
        result["error"] = "录音失败"
        return result
    text = transcribe()
    result["text"] = text
    if not text:
        result["error"] = "没识别出文字"
        return result
    action, kw = match_command(text)
    result["action"] = action
    result["keyword"] = kw
    if action is None:
        result["error"] = "没匹配到命令（说的可能是：%s）" % text
        return result
    result["ok"] = True
    return result


if __name__ == "__main__":
    # 单独测试：录 5 秒并打印识别结果
    print("=" * 60)
    print("  语音控制测试")
    print("  请说：叠罗汉 / 搬运 / 跳舞 / 分拣 / 停止")
    print("=" * 60)
    r = listen_once()
    print()
    print("结果:", r)