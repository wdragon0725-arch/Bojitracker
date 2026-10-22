"""Load local .env settings without adding a third-party dependency."""

from __future__ import annotations

import os
import re
from pathlib import Path


ALLOWED_KEYS = {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "BOJI_PORT", "BOJI_DB_PATH"}
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def load_env(path: Path) -> None:
    """Use .env defaults while preserving explicitly supplied process variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in ALLOWED_KEYS or not KEY_PATTERN.fullmatch(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)
