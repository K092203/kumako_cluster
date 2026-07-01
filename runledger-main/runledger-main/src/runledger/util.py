"""Small shared helpers: atomic writes, JSON, number coercion."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """Write text via a temp file + os.replace for crash-safe updates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path: Path, obj: Any, *, atomic: bool = False) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    if atomic:
        atomic_write_text(path, text)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "ok", "y", "correct"}:
        return True
    if s in {"0", "false", "no", "n", "wrong", "incorrect"}:
        return False
    return None


def median(values: list[float]) -> float:
    """Median of a non-empty list (caller guarantees non-empty)."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
