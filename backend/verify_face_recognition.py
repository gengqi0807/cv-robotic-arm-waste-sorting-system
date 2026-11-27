from __future__ import annotations

import argparse
from math import ceil, inf, isfinite
import statistics
from pathlib import Path

import cv2

from project_config import ROOT_DIR
from .train_face_model import (
    DATA_DIR,
    FaceRecognizer,
    NoFaceError,
    REAL_DATA_ERROR,
    TRIAL_DISTANCE,
    _image_files,
    read_image,
    validate_real_face_dataset,
)

DEFAULT_UNKNOWN_DIR = ROOT_DIR / "data" / "faces_unknown_test"


def known_samples(directory: Path, operator_ids: list[str]) -> list[tuple[Path, str]]:
    samples = []
    for operator_id in operator_ids:
        images = _image_files(directory / operator_id / "test")
        if not images:
            raise FileNotFoundError(f"{REAL_DATA_ERROR}（{operator_id} 缺少测试照片）")
        samples.extend((path, operator_id) for path in images)
    return samples


def evaluate_samples(samples, recognizer: FaceRecognizer) -> list[tuple[str | None, str | None, float, float]]:
    results = []
    for path, expected in samples:
        start = cv2.getTickCount()
        try:
            operator_id, distance = recognizer.predict(read_image(path))
        except NoFaceError:
            operator_id, distance = None, inf
        elapsed_ms = (cv2.getTickCount() - start) * 1000 / cv2.getTickFrequency()
        results.append((expected, operator_id, float(distance), elapsed_ms))
    return results


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values_sorted = sorted(values)
    index = max(0, ceil(pct / 100 * len(values_sorted)) - 1)
    return float(values_sorted[index])


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 SFace 识别的准确率、拒识率和处理时延。")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--operator-id", action="append", help="只验证指定组员，可重复")
    parser.add_argument("--known-only", action="store_true", help="只输出已登记人员的阶段测试结果")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--threshold", type=float, help="余弦距离阈值，距离小于此值才接受")
    parser.add_argument("--known-dir", type=Path)
    parser.add_argument("--unknown-dir", type=Path, default=DEFAULT_UNKNOWN_DIR)
    args = parser.parse_args()

    try:
        threshold = args.threshold
        if threshold is None and args.known_only:
            threshold = TRIAL_DISTANCE
        if threshold is None or not isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("请提供 0 到 1 之间的余弦距离阈值（大于 0）。")
        records = validate_real_face_dataset(args.data_dir, args.operator_id)
        operator_ids = [record["operator_id"] for record in records]
        recognizer = FaceRecognizer(args.data_dir, args.model_path)
        if set(recognizer.operator_ids) != set(operator_ids):
            raise ValueError("模型登记人员与本次验证名单不一致。")
        unknown_images = [] if args.known_only else _image_files(args.unknown_dir)
        if not args.known_only and len(unknown_images) < 50:
            raise ValueError(f"{REAL_DATA_ERROR}（未登记测试需至少 50 张，目前 {len(unknown_images)} 张）")
        known = evaluate_samples(known_samples(args.known_dir or args.data_dir, operator_ids), recognizer)
        unknown = evaluate_samples(((path, None) for path in unknown_images), recognizer)
    except (OSError, RuntimeError, ValueError, cv2.error) as exc:
        print(f"ERROR: {exc}")
        return 1

    known_total = len(known)
    known_correct = sum(expected == predicted and distance < threshold for expected, predicted, distance, _ in known)
    known_rate = known_correct / known_total * 100
    for operator_id in operator_ids:
        results = [row for row in known if row[0] == operator_id]
        correct = sum(expected == predicted and distance < threshold for expected, predicted, distance, _ in results)
        best_matches = sum(expected == predicted for expected, predicted, _, _ in results)
        print(f"[{operator_id}] 最佳候选正确 {best_matches}/{len(results)}，正确接受 {correct}/{len(results)}")
    best_matches = sum(expected == predicted for expected, predicted, _, _ in known)
    no_face = sum(predicted is None for _, predicted, _, _ in known)
    print(f"[known_candidates] 最佳候选正确 {best_matches}/{known_total}，无有效人脸 {no_face} 张")
    print(f"[known_accuracy] 阈值 {threshold:g}，正确接受 {known_correct}/{known_total}，准确率 {known_rate:.2f}%")
    p95_ms = percentile([duration for _, _, _, duration in known + unknown], 95)
    print(f"[p95_latency] 图片读取、检测和识别 P95={p95_ms:.2f}ms")
    if args.known_only:
        print("SUMMARY: KNOWN ONLY（未测试未登记拒识，供阶段检查）")
        return 0

    unknown_total = len(unknown)
    unknown_correct = sum(distance >= threshold for _, _, distance, _ in unknown)
    unknown_rate = (unknown_correct / unknown_total * 100.0) if unknown_total else 0.0
    known_passed = known_total >= 50 and known_correct * 50 >= known_total * 49
    unknown_passed = unknown_total >= 50 and unknown_correct * 50 >= unknown_total * 49

    print("=== 人脸识别验证 ===")
    print(f"[known_accuracy] 预期：≥49/50；实际：{known_correct}/{known_total}；准确率={known_rate:.2f}%")
    print(f"[known_accuracy] 通过：{'YES' if known_passed else 'NO'}")
    if known_total < 50:
        print(f"已登记测试样本距离正式验收还差 {50 - known_total} 张。")
    print(f"[unknown_accuracy] 预期：≥49/50；实际：{unknown_correct}/{unknown_total}；拒绝率={unknown_rate:.2f}%")
    print(f"[unknown_accuracy] 通过：{'YES' if unknown_passed else 'NO'}")
    print(f"[p95_latency] 预期：95% ≤ 1000ms；实际：P95={p95_ms:.2f}ms")
    print(f"[p95_latency] 通过：{'YES' if p95_ms <= 1000.0 else 'NO'}")
    for group, results in (("known", known), ("unknown", unknown)):
        distances = [distance for _, _, distance, _ in results if isfinite(distance)]
        if distances:
            print(f"[{group}_distance_summary] min={min(distances):.2f} max={max(distances):.2f} mean={statistics.fmean(distances):.2f}")

    passed = known_passed and unknown_passed and p95_ms <= 1000.0
    print(f"SUMMARY: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
