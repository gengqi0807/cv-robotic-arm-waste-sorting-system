"""Load environment settings shared by the project entry points."""

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent


def load_local_env() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip():
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
