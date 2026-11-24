#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第四阶段 任务2：语音识别
把训练好的 WeNet 模型（offline_encoder.om）部署到 Atlas 200I DK A2 上做推理。

流程：
    wav(48kHz) -> 重采样到 16kHz -> 提取 fbank(80维) -> 补齐到 1478 帧
        -> 送进 om 模型 -> 取 ctc_log_probs -> argmax -> CTC 去重 -> 映射成文本

模型输入输出（probe_wenet.py 实测）：
    输入 : speech [1,1478,80] float32 , speech_lengths [1] int32
    输出 : ctc_log_probs [1,368,11008] float32   ← 已 log_softmax
           encoder_out_lens [1] int32

用法：
    cd /home/HwHiAiUser/speech_recognition
    export PYTHONPATH=/root/miniconda3/envs/ros-arm-env/lib/python3.10/site-packages:$PYTHONPATH
    /usr/bin/python3 speech_recognition.py
"""
import os
import sys

import numpy as np

# ---------------------------------------------------------------- 常量
SAMPLE_RATE = 16000          # 模型要求的采样率
NUM_MEL_BINS = 80            # fbank 维度（模型输入最后一维）
BLANK_ID = 0                 # <blank>
UNK_ID = 1                   # <unk>
EOS_ID = 11007               # <sos/eos>


# ---------------------------------------------------------------- 工具函数
def pad_sequence(feats, max_len, pad_value=0.0):
    """
    定义统一音频长度的方法。

    如果音频长度大于设置的长度，则截断；若小于设置的长度，则进行填充。

    :param feats:    fbank 特征，形状 [T, D]
    :param max_len:  模型允许的最大帧数（本模型 1478）
    :return:         (补齐后的 [max_len, D], 真实帧数)
    """
    feats = np.asarray(feats, dtype=np.float32)
    t, d = feats.shape
    if t >= max_len:
        return feats[:max_len].copy(), max_len

    out = np.full((max_len, d), pad_value, dtype=np.float32)
    out[:t] = feats
    return out, t


def resample(waveform, orig_sr, target_sr):
    """
    定义音频重采样方法。

    对采样率不符合模型输入的数据进行重采样（sample.wav 是 48kHz，模型要 16kHz）。

    :param waveform:  [1, N] 的 float32 tensor（取值 -1~1）
    :param orig_sr:   原始采样率
    :param target_sr: 目标采样率
    :return:          重采样后的 [1, N'] tensor
    """
    import torch
    if orig_sr == target_sr:
        return waveform
    import torchaudio
    tf = torchaudio.transforms.Resample(orig_freq=orig_sr, new_freq=target_sr)
    return tf(waveform)


def compute_fbank(waveform, sample_rate=SAMPLE_RATE, num_mel_bins=NUM_MEL_BINS):
    """
    定义提取音频 fbank 特征的方法。

    用 torchaudio 的 kaldi 兼容实现，参数与 WeNet 训练时保持一致：
      - 帧长 25ms、帧移 10ms
      - dither=0（推理时必须关掉抖动，否则每次结果都不同）
      - snip_edges=True（与 kaldi 默认一致）
      - window_type=povey（WeNet / kaldi 默认窗）

    :param waveform:      [1, N] 的 float32 tensor（取值 -1~1）
    :param sample_rate:   采样率（必须是 16000）
    :param num_mel_bins:  mel 滤波器个数（80）
    :return:              [T, num_mel_bins] 的 numpy 数组
    """
    import torchaudio.compliance.kaldi as kaldi

    feat = kaldi.fbank(
        waveform,
        num_mel_bins=num_mel_bins,
        frame_length=25,
        frame_shift=10,
        dither=0.0,
        sample_frequency=sample_rate,
        snip_edges=True,
        window_type="povey",   # WeNet / kaldi 训练默认用 povey 窗，不是 hamming
    )
    return feat.numpy()


def apply_cmvn(feats, mode="mean_only", eps=1e-5):
    """
    倒谱均值归一化 / CMVN。

    WeNet 训练时会对 fbank 做全局 CMVN，推理时必须用同一套统计量，否则
    特征分布和模型见过的不一致 —— 表现出来就是模型 100% 输出 blank、
    识别结果是空字符串。

    课程资料里没有提供 global_cmvn 文件（wenet.zip 里只有 om / wav / vocab
    三个文件），所以只能用这条音频自己的统计量来近似。

    实测（2026-11-16，sample.wav）：
        mode="none"       不归一化           → 100% blank，识别结果为空
        mode="mean_only"  只减均值            → ✅ 能识别出中文
        mode="full"       减均值再除标准差     → 100% blank（数值被压得太小）

    原因是这个模型对应的 global_cmvn 里 istd 很小（接近 1），
    所以只需要去掉直流偏移，不能把动态范围也压掉。

    :param feats: [T, D] 的 fbank
    :param mode:  "mean_only"（默认，只减均值）/ "full"（完整 CMVN）/ "none"
    :return:      [T, D] 归一化后的特征
    """
    feats = np.asarray(feats, dtype=np.float32)
    if feats.size == 0 or mode == "none":
        return feats

    mean = feats.mean(axis=0, keepdims=True)
    if mode == "mean_only":
        return (feats - mean).astype(np.float32)

    std = feats.std(axis=0, keepdims=True)
    return ((feats - mean) / (std + eps)).astype(np.float32)


def load_vocab(vocab_path):
    """
    定义加载文本映射字典的方法。

    vocab.txt 每行形如：  'A 6'      → token 是 A，编号是 6
    返回 {编号: token}，方便用 argmax 的结果直接查。

    :param vocab_path: vocab.txt 路径
    :return:           dict[int] = str
    """
    vocab = {}
    with open(vocab_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            # 从右边切一次，左边的整体（可能含空格）是 token，右边是编号
            idx = line.rfind(" ")
            if idx <= 0:
                continue
            token = line[:idx]
            try:
                vocab[int(line[idx + 1:])] = token
            except ValueError:
                continue
    return vocab


def load_wav(wav_path):
    """
    读 wav（16bit PCM 单/多声道），返回 [1, N] 的 float32 tensor（-1~1）。

    用 scipy 读，避免依赖 torchaudio 的 IO 后端（这台板子上 soundfile 没装）。
    """
    import torch
    from scipy.io import wavfile

    sr, data = wavfile.read(wav_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    else:
        data = data.astype(np.float32)
    return torch.from_numpy(data).unsqueeze(0), int(sr)


# ---------------------------------------------------------------- 推理类
class WeNetASR:
    """定义 WeNet 模型的推理功能。"""

    def __init__(self, model_path, vocab_path, device_id=0, cmvn_mode="mean_only"):
        """
        构造函数：加载文本映射字典、初始化推理模型、获取模型允许的最大音频长度。

        :param cmvn_mode: fbank 的归一化方式。课程没提供 global_cmvn 文件，
                          实测只有 "mean_only"（只减均值）能识别出文字；
                          "full"（完整 CMVN）和 "none"（不归一化）都会 100% blank。
        """
        self.vocab = load_vocab(vocab_path)
        self.eos_id = max(self.vocab.keys()) if self.vocab else EOS_ID
        self.cmvn_mode = cmvn_mode

        from ais_bench.infer.interface import InferSession
        self.session = InferSession(device_id, model_path)

        # 从模型输入形状里读最大帧数（[1, 1478, 80] -> 1478）
        self.max_len = int(self.session.get_inputs()[0].shape[1])

    def preprocess(self, wav_path):
        """
        语音预处理方法：加载音频文件、计算 fbank 特征、统一特征长度。

        :return: (speech [1, max_len, 80] float32, lengths [1] int32)
        """
        waveform, sr = load_wav(wav_path)

        if sr != SAMPLE_RATE:
            waveform = resample(waveform, sr, SAMPLE_RATE)

        feats = compute_fbank(waveform, SAMPLE_RATE)      # [T, 80]

        # WeNet 推理需要 CMVN。课程没给 global_cmvn，只能用本句统计量近似；
        # 实测只有"只减均值"能识别出来（完整 CMVN 会把数值压太小，反而全 blank）。
        # 必须在 pad 之前做，否则补进去的 0 会把统计量拉偏。
        if self.cmvn_mode != "none":
            feats = apply_cmvn(feats, mode=self.cmvn_mode)

        feats, real_len = pad_sequence(feats, self.max_len)  # [max_len, 80]

        speech = feats[np.newaxis, ...].astype(np.float32)  # [1, max_len, 80]
        lengths = np.array([real_len], dtype=np.int32)      # [1]
        return speech, lengths

    def map_batch(self, token_ids):
        """
        根据字典将推理结果映射为文本。

        CTC 解码三件事：
          1. 相邻重复的 token 合并（CTC 的规则）
          2. 去掉 <blank>
          3. 去掉 <sos/eos> / <unk>

        :param token_ids: 一维 token 编号序列
        :return:          识别出的文本
        """
        pieces = []
        prev = -1
        for tid in token_ids:
            tid = int(tid)
            if tid != prev and tid != BLANK_ID:
                if tid != UNK_ID and tid != self.eos_id and tid in self.vocab:
                    pieces.append(self.vocab[tid])
            prev = tid

        text = "".join(pieces)
        # sentencepiece 的单词边界标记，替换成空格更易读
        text = text.replace("▁", " ").strip()
        return text

    def post_process(self, ctc_log_probs, out_lens):
        """
        语音后处理：调用 map_batch() 把模型推理结果映射到文本。
        """
        n = int(out_lens[0])
        n = max(0, min(n, ctc_log_probs.shape[1]))
        ids = np.argmax(ctc_log_probs[0, :n, :], axis=-1)
        return self.map_batch(ids)

    def transcribe(self, wav_path):
        """
        语音转文本的推理调用接口：
        串联语音预处理、模型推理、语音后处理等代码逻辑。

        :param wav_path: 待识别音频路径
        :return:         识别文本
        """
        speech, lengths = self.preprocess(wav_path)
        outputs = self.session.infer([speech, lengths])

        # outputs 顺序与模型输出一致：
        #   0 encoder_out, 1 encoder_out_lens, 2 ctc_log_probs,
        #   3 beam_log_probs, 4 beam_log_probs_idx
        ctc_log_probs = outputs[2]
        out_lens = outputs[1]
        return self.post_process(ctc_log_probs, out_lens)


# ---------------------------------------------------------------- 运行样例
def main():
    base = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(base, "offline_encoder.om")
    vocab_path = os.path.join(base, "vocab.txt")
    wav_path = os.path.join(base, "sample.wav")

    for p in (model_path, vocab_path, wav_path):
        if not os.path.exists(p):
            print("[错误] 找不到文件:", p)
            return 1

    # 初始化相关参数
    print("=" * 60)
    print(" 语音识别（WeNet + Atlas 200I DK A2）")
    print("=" * 60)

    asr = WeNetASR(model_path, vocab_path, device_id=0)
    print("词表大小:", len(asr.vocab))
    print("模型最大帧数:", asr.max_len)

    # 展示样例语音
    import wave
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        dur = w.getnframes() / sr
    print("待识别音频:", os.path.basename(wav_path))
    print("  采样率 %d Hz, 时长 %.2f 秒" % (sr, dur))

    # 执行模型推理，打印识别文本
    print("\n开始推理 ...")
    text = asr.transcribe(wav_path)

    print("\n" + "=" * 60)
    print(" 识别结果:")
    print("=" * 60)
    print(text)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())