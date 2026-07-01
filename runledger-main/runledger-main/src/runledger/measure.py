"""Pluggable measurement extraction (§9).

A measure spec decides how to read elapsed/score/correct from a run's output.
Kinds: tune-line (default), regex, json, file, none.

Hard rule: a non-zero exit code is never trusted as a good measurement
(crash/timeout must not masquerade as a result).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runledger.util import to_bool, to_float

TUNE_LINE = "tune-line"
REGEX = "regex"
JSON = "json"
FILE = "file"
NONE = "none"

VALID_KINDS = {TUNE_LINE, REGEX, JSON, FILE, NONE}


@dataclass
class MeasureSpec:
    kind: str = TUNE_LINE
    stream: str = "stderr"  # tune-line / regex: which stream to scan
    elapsed_re: str | None = None
    score_re: str | None = None
    correct_re: str | None = None
    path: str | None = None  # json / file: where to read

    @classmethod
    def from_cli(cls, spec: str | None, table: dict[str, Any] | None = None) -> "MeasureSpec":
        """Build from a CLI string and/or a config [measure] table.

        Compact CLI forms: "tune-line", "none", "regex", "json:result.json",
        "file:score.txt". Regex patterns come from the [measure] table.
        """
        table = dict(table or {})
        if spec is None:
            kind = str(table.get("kind", TUNE_LINE))
        elif ":" in spec and spec.split(":", 1)[0] in {JSON, FILE}:
            kind, rest = spec.split(":", 1)
            table["path"] = rest
        else:
            kind = spec
        if kind not in VALID_KINDS:
            raise ValueError(
                f"unknown measure kind '{kind}'. Choose: {', '.join(sorted(VALID_KINDS))}"
            )
        return cls(
            kind=kind,
            stream=str(table.get("stream", "stderr")),
            elapsed_re=table.get("elapsed"),
            score_re=table.get("score"),
            correct_re=table.get("correct"),
            path=table.get("path"),
        )


@dataclass
class MeasureResult:
    elapsed: float | None = None
    score: float | None = None
    correct: bool | None = None
    source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed": self.elapsed,
            "score": self.score,
            "correct": self.correct,
            "source": self.source,
        }


def extract_measure(
    spec: MeasureSpec,
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    base: Path,
) -> MeasureResult:
    """Extract a measurement, refusing to trust non-zero exits."""
    if spec.kind == NONE:
        return MeasureResult(source=NONE)
    if exit_code != 0:
        # Untrusted: record what we *would* have parsed by, but no values.
        return MeasureResult(source=spec.kind)
    if spec.kind == TUNE_LINE:
        return _extract_tune_line(stdout, stderr, spec)
    if spec.kind == REGEX:
        return _extract_regex(stdout, stderr, spec)
    if spec.kind == JSON:
        return _extract_json(spec, base)
    if spec.kind == FILE:
        return _extract_file(spec, base)
    return MeasureResult(source=NONE)


def _pick_stream(stdout: str, stderr: str, stream: str) -> str:
    return stdout if stream == "stdout" else stderr


def _extract_tune_line(stdout: str, stderr: str, spec: MeasureSpec) -> MeasureResult:
    text = _pick_stream(stdout, stderr, spec.stream)
    last: str | None = None
    for line in text.splitlines():
        if line.strip().startswith("#TUNE"):
            last = line.strip()
    res = MeasureResult(source=TUNE_LINE)
    if last is None:
        return res
    for token in last[len("#TUNE"):].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key == "elapsed":
            res.elapsed = to_float(value)
        elif key == "score":
            res.score = to_float(value)
        elif key == "correct":
            res.correct = to_bool(value)
    return res


def _extract_regex(stdout: str, stderr: str, spec: MeasureSpec) -> MeasureResult:
    text = _pick_stream(stdout, stderr, spec.stream)
    res = MeasureResult(source=REGEX)
    if spec.elapsed_re:
        res.elapsed = _first_group_float(spec.elapsed_re, text)
    if spec.score_re:
        res.score = _first_group_float(spec.score_re, text)
    if spec.correct_re:
        res.correct = re.search(spec.correct_re, text) is not None
    return res


def _first_group_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return to_float(match.group(1) if match.groups() else match.group(0))


def _resolve(spec_path: str, base: Path) -> Path:
    p = Path(spec_path)
    return p if p.is_absolute() else base / p


def _extract_json(spec: MeasureSpec, base: Path) -> MeasureResult:
    res = MeasureResult(source=JSON)
    if not spec.path:
        return res
    try:
        data = json.loads(_resolve(spec.path, base).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return res
    if isinstance(data, dict):
        res.elapsed = to_float(data.get("elapsed"))
        res.score = to_float(data.get("score"))
        if "correct" in data:
            res.correct = to_bool(data.get("correct"))
    return res


def _extract_file(spec: MeasureSpec, base: Path) -> MeasureResult:
    res = MeasureResult(source=FILE)
    if not spec.path:
        return res
    try:
        text = _resolve(spec.path, base).read_text(encoding="utf-8").strip()
    except OSError:
        return res
    if text:
        res.score = to_float(text.splitlines()[0].strip())
    return res
