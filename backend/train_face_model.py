from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import cv2
import numpy as np

from project_config import ROOT_DIR

DATA_DIR = ROOT_DIR / "data" / "faces"
MODEL_FILENAME = "face_embeddings.npz"
DETECTOR_FILENAME = "face_detection_yunet_2023mar.onnx"
FEATURE_MODEL_FILENAME = "face_recognition_sface_2021dec_int8.onnx"
TRIAL_DISTANCE = 0.45
REAL_DATA_ERROR = "未检测到真实采集数据，请先按 PHOTO_GUIDE.md 采集照片"
MIN_TRAIN_IMAGES = 5
MIN_TEST_IMAGES = 10


def _image_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ]
    )


class NoFaceError(ValueError):
    """A face sample must contain exactly one detectable face."""


def normalize_feature(feature: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(feature))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("人脸特征无效。")
    return feature / norm


class FaceFeatures:
    def __init__(self, model_dir: Path):
        detector_path = model_dir / DETECTOR_FILENAME
        feature_path = model_dir / FEATURE_MODEL_FILENAME
        for path in (detector_path, feature_path):
            if not path.is_file():
                raise FileNotFoundError(f"缺少 {path.name}，请按 PHOTO_GUIDE.md 准备 OpenCV 模型。")
        if not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"):
            raise RuntimeError("当前 OpenCV 缺少 YuNet 或 SFace 支持。")
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), 0.85)
        self.extractor = cv2.FaceRecognizerSF.create(str(feature_path), "")

    def extract(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        scale = min(1.0, 720 / max(height, width))
        if scale < 1.0:
            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                               interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(image)
        minimum_side = max(32, 0.12 * min(width, height))
        candidates = [] if faces is None else [face for face in faces if min(face[2:4]) >= minimum_side]
        if len(candidates) != 1:
            raise NoFaceError("图片中未检测到单张人脸。")
        face = self.extractor.alignCrop(image, candidates[0])
        return normalize_feature(self.extractor.feature(face).reshape(-1))


def read_image(image_path: Path) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(image_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取人脸图像：{image_path}")
    return image


def _load_manifest(data_dir: Path, operator_ids: list[str] | None = None) -> list[dict[str, Any]]:
    manifest_path = data_dir / "manifest.json"
    if not data_dir.exists():
        raise FileNotFoundError(f"{REAL_DATA_ERROR}（未找到目录：{data_dir}）")
    if not manifest_path.exists():
        raise FileNotFoundError(f"{REAL_DATA_ERROR}（缺少 {manifest_path.name}）")

    try:
        with manifest_path.open("r", encoding="utf-8-sig") as handle:
            raw_manifest = json.load(handle)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive guards
        raise ValueError(f"{REAL_DATA_ERROR}（manifest.json 格式错误）") from exc

    if not isinstance(raw_manifest, dict):
        raise ValueError(f"{REAL_DATA_ERROR}（manifest.json 必须是对象）")

    entries = raw_manifest.get("operators", [])
    if not isinstance(entries, list):
        raise ValueError(f"{REAL_DATA_ERROR}（manifest.json 中 operators 必须是数组）")

    selected = set(operator_ids or [])
    normalized: list[dict[str, Any]] = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{REAL_DATA_ERROR}（operators 各项必须是对象）")
        operator_id = str(entry.get("operator_id") or "").strip()
        if selected and operator_id not in selected:
            continue
        if not operator_id or Path(operator_id).name != operator_id or operator_id in {".", ".."}:
            raise ValueError(f"{REAL_DATA_ERROR}（operator_id 无效）")
        if operator_id.casefold() in seen:
            raise ValueError(f"{REAL_DATA_ERROR}（{operator_id} 重复登记）")
        seen.add(operator_id.casefold())
        if entry.get("agreed") is not True:
            raise ValueError(f"{REAL_DATA_ERROR}（{operator_id} 缺少本人授权记录）")
        name = str(entry.get("name") or "").strip()
        agreed_at = str(entry.get("agreed_at") or "").strip()
        train_count = int(entry.get("train_count") or 0)
        test_count = int(entry.get("test_count") or 0)
        if not name or not agreed_at:
            raise ValueError(f"{REAL_DATA_ERROR}（{operator_id} 缺少姓名或授权确认日期）")
        if train_count < MIN_TRAIN_IMAGES or test_count < MIN_TEST_IMAGES:
            raise ValueError(f"{REAL_DATA_ERROR}（{operator_id} 的训练集至少 {MIN_TRAIN_IMAGES} 张，测试集至少 {MIN_TEST_IMAGES} 张）")
        normalized.append({
            "operator_id": operator_id,
            "name": name,
            "agreed_at": agreed_at,
            "train_count": train_count,
            "test_count": test_count,
        })

    missing = selected - {record["operator_id"] for record in normalized}
    if missing:
        raise ValueError(f"{REAL_DATA_ERROR}（清单缺少：{', '.join(sorted(missing))}）")
    if not normalized:
        raise ValueError(f"{REAL_DATA_ERROR}（manifest.json 中未记录任何 operator_id）")

    return normalized


def validate_real_face_dataset(data_dir: Path = DATA_DIR, operator_ids: list[str] | None = None) -> list[dict[str, Any]]:
    manifest = _load_manifest(data_dir, operator_ids)
    samples: dict[bytes, Path] = {}
    for record in manifest:
        operator_id = record["operator_id"]
        operator_dir = data_dir / operator_id
        train_dir = operator_dir / "train"
        test_dir = operator_dir / "test"
        if not operator_dir.exists():
            raise FileNotFoundError(f"{REAL_DATA_ERROR}（{operator_dir} 不存在）")
        if not train_dir.is_dir() or not test_dir.is_dir():
            raise FileNotFoundError(f"{REAL_DATA_ERROR}（{operator_id} 缺少 train/ 或 test/ 目录）")
        train_files = _image_files(train_dir)
        test_files = _image_files(test_dir)
        if len(train_files) != record["train_count"] or len(test_files) != record["test_count"]:
            raise ValueError(f"{REAL_DATA_ERROR}（{operator_id} 的训练或测试照片数量与 manifest.json 不一致）")
        for path in train_files + test_files:
            digest = sha256(path.read_bytes()).digest()
            if digest in samples:
                previous = samples[digest].relative_to(data_dir)
                raise ValueError(f"检测到重复照片：{previous} 和 {path.relative_to(data_dir)}")
            samples[digest] = path
    return manifest


class FaceRecognizer:
    def __init__(self, model_dir: Path = DATA_DIR, model_path: Path | None = None):
        model_path = model_path or model_dir / MODEL_FILENAME
        if not model_path.is_file():
            raise FileNotFoundError("缺少本地登记模型，请先运行 python -m backend.train_face_model。")
        with np.load(model_path, allow_pickle=False) as archive:
            self.operator_ids = archive["operator_ids"].tolist()
            self.vectors = archive["vectors"]
        if (self.vectors.shape != (len(self.operator_ids), 128) or not self.operator_ids
                or any(not isinstance(value, str) or not value for value in self.operator_ids)
                or len(set(self.operator_ids)) != len(self.operator_ids)
                or not np.allclose(np.linalg.norm(self.vectors, axis=1), 1, atol=1e-4)):
            raise ValueError("本地登记模型格式无效，请重新训练。")
        self.features = FaceFeatures(model_dir)

    def predict(self, image: np.ndarray) -> tuple[str, float]:
        scores = self.vectors @ self.features.extract(image)
        best = int(np.argmax(scores))
        return self.operator_ids[best], 1.0 - float(np.clip(scores[best], -1, 1))


def train_model(
    data_dir: Path = DATA_DIR, *, operator_ids: list[str] | None = None,
    model_path: Path | None = None,
) -> dict[str, str | int]:
    manifest = validate_real_face_dataset(data_dir, operator_ids)
    model_path = model_path or data_dir / MODEL_FILENAME
    feature_extractor = FaceFeatures(data_dir)
    registry_ids = []
    vectors = []
    sample_count = 0
    for record in manifest:
        operator_id = record["operator_id"]
        train_dir = data_dir / operator_id / "train"
        samples = []
        for image_path in _image_files(train_dir):
            try:
                samples.append(feature_extractor.extract(read_image(image_path)))
            except NoFaceError as exc:
                raise NoFaceError(f"{image_path.relative_to(data_dir)}：{exc}") from exc
        vectors.append(normalize_feature(np.mean(samples, axis=0)))
        registry_ids.append(operator_id)
        sample_count += len(samples)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=model_path.parent, suffix=".npz", delete=False) as handle:
        staged_path = Path(handle.name)
        try:
            np.savez_compressed(handle, operator_ids=np.asarray(registry_ids), vectors=np.stack(vectors))
        except Exception:
            handle.close()
            staged_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(staged_path, model_path)
    finally:
        staged_path.unlink(missing_ok=True)
    return {"trained_samples": sample_count, "person_count": len(registry_ids), "model_path": str(model_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="用 SFace 生成本地人员登记模型。")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="保存真实采集照片的目录")
    parser.add_argument("--operator-id", action="append", help="只训练指定组员，可重复。默认训练清单中的所有组员。")
    parser.add_argument("--model-path", type=Path, help="输出 face_embeddings.npz 的位置，默认在数据目录内")
    args = parser.parse_args()

    try:
        summary = train_model(args.data_dir, operator_ids=args.operator_id, model_path=args.model_path)
    except (OSError, RuntimeError, ValueError, cv2.error) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"训练完成：{summary['trained_samples']} 张图像，{summary['person_count']} 人")
    print(f"模型：{summary['model_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
