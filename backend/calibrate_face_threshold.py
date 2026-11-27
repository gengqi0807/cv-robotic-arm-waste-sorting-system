from __future__ import annotations

import argparse
from math import inf, isfinite, nextafter
from pathlib import Path

import cv2

from project_config import ROOT_DIR
from .train_face_model import DATA_DIR, FaceRecognizer, REAL_DATA_ERROR, _image_files, validate_real_face_dataset
from .verify_face_recognition import evaluate_samples, known_samples, percentile


DEFAULT_UNKNOWN_DIR = ROOT_DIR / "data" / "faces_unknown_test"


def main() -> int:
    parser = argparse.ArgumentParser(description="标定 SFace 余弦距离阈值。")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="训练集、采集清单与模型所在目录")
    parser.add_argument("--known-dir", type=Path, help="独立登记人员校准集，默认使用数据目录的 test")
    parser.add_argument("--operator-id", action="append", help="限制参与校准的登记人员，可重复")
    parser.add_argument("--unknown-dir", type=Path, default=DEFAULT_UNKNOWN_DIR, help="未登记人员图片目录")
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()

    try:
        records = validate_real_face_dataset(args.data_dir, args.operator_id)
        operator_ids = [record["operator_id"] for record in records]
        unknown_images = _image_files(args.unknown_dir)
        if len(unknown_images) < 50:
            raise ValueError(f"{REAL_DATA_ERROR}（未登记校准需至少 50 张，目前 {len(unknown_images)} 张）")
        recognizer = FaceRecognizer(args.data_dir, args.model_path)
        if set(recognizer.operator_ids) != set(operator_ids):
            raise ValueError("模型登记人员与本次校准名单不一致。")
        known = evaluate_samples(known_samples(args.known_dir or args.data_dir, operator_ids), recognizer)
        unknown = evaluate_samples(((path, None) for path in unknown_images), recognizer)
    except (OSError, RuntimeError, ValueError, cv2.error) as exc:
        print(f"ERROR: {exc}")
        return 1

    candidates = {nextafter(distance, inf) for expected, predicted, distance, _ in known
                  if expected == predicted and 0 <= distance < 1}
    if not candidates:
        print("ERROR: 已登记样本中没有分类正确的匹配距离，无法校准阈值。")
        return 1

    for group, rows in (("已登记", known), ("未登记", unknown)):
        values = [distance for _, _, distance, _ in rows if isfinite(distance)]
        if values:
            print(f"{group}人员有限距离 {len(values)}/{len(rows)}，P50={percentile(values, 50):.2f}，P95={percentile(values, 95):.2f}")

    results = [
        (threshold,
         sum(expected == predicted and distance < threshold for expected, predicted, distance, _ in known),
         sum(predicted is None or distance >= threshold for _, predicted, distance, _ in unknown))
        for threshold in candidates
    ]
    best, known_ok, unknown_ok = max(results, key=lambda row: (
        min(row[1] / len(known), row[2] / len(unknown)), row[2], row[1], -row[0],
    ))
    print(f"候选阈值：{best}，登记人员正确接受 {known_ok}/{len(known)}，未登记拒绝 {unknown_ok}/{len(unknown)}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
