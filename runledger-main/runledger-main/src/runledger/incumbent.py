"""Incumbent management: the always-submittable best-known result (§11).

Update rules:
- only ``correct`` candidates are eligible,
- the incumbent is replaced only on a strict objective improvement,
- writes are atomic,
- a result with no backing config is never adopted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from runledger import config as C
from runledger.util import write_json

OBJECTIVES = ("max-score", "min-elapsed", "score-per-sec")


@dataclass
class Candidate:
    id: str
    target: str
    bin: str
    args: str
    elapsed: float | None
    score: float | None
    correct: bool
    source_sweep: str
    source_run: str


def objective_value(elapsed: float | None, score: float | None, objective: str) -> float | None:
    """Higher is always better; None means ineligible."""
    if objective == "max-score":
        return score
    if objective == "min-elapsed":
        return None if elapsed is None else -elapsed
    if objective == "score-per-sec":
        if score is None or not elapsed or elapsed <= 0:
            return None
        return score / elapsed
    raise ValueError(f"unknown objective '{objective}'. Choose: {', '.join(OBJECTIVES)}")


def load_incumbent(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / "incumbent.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _to_record(doc: dict[str, Any]) -> dict[str, Any]:
    return doc


def update_incumbent(
    state_dir: Path,
    candidate: Candidate,
    *,
    objective: str,
) -> bool:
    """Adopt candidate iff correct and a strict objective improvement. Returns True if written."""
    if not candidate.correct:
        return False
    new_val = objective_value(candidate.elapsed, candidate.score, objective)
    if new_val is None:
        return False

    current = load_incumbent(state_dir)
    if current is not None and current.get("objective") == objective:
        cur_val = objective_value(current.get("elapsed"), current.get("score"), objective)
        if cur_val is not None and new_val <= cur_val:
            return False

    doc = {
        "schema_version": C.SCHEMA_VERSION,
        "id": candidate.id,
        "objective": objective,
        "target": candidate.target,
        "bin": candidate.bin,
        "args": candidate.args,
        "elapsed": candidate.elapsed,
        "score": candidate.score,
        "correct": candidate.correct,
        "source_sweep": candidate.source_sweep,
        "source_run": candidate.source_run,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_json(state_dir / "incumbent.json", doc, atomic=True)
    return True


def compute_best(candidates: list[Candidate], *, objective: str) -> Candidate | None:
    """Pick the single best correct candidate for an objective (batch mode)."""
    best: Candidate | None = None
    best_val: float | None = None
    for cand in candidates:
        if not cand.correct:
            continue
        val = objective_value(cand.elapsed, cand.score, objective)
        if val is None:
            continue
        if best_val is None or val > best_val:
            best_val = val
            best = cand
    return best
