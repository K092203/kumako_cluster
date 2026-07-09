"""Tests for the adaptive-slot logic in supervise_slots (#14, goodput feedback)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from supervise_slots import adapt_slots, scan_outcomes  # noqa: E402


def test_adapt_slots_no_samples_holds_steady():
    # 完了/失敗が1件も無い区間ではスロット数を変えない
    assert adapt_slots(10, 0, 0, 2, 14) == 10


def test_adapt_slots_additive_increase_when_clean():
    # 失敗ゼロなら +1 して上限を探る
    assert adapt_slots(8, 40, 0, 2, 14) == 9


def test_adapt_slots_respects_max():
    assert adapt_slots(14, 40, 0, 2, 14) == 14


def test_adapt_slots_multiplicative_decrease_on_high_failure():
    # 失敗率>10%(過負荷=timeout)なら乗法的に減らす: min(desired-1, int(desired*0.75))
    assert adapt_slots(12, 0, 12, 2, 14) == 9  # int(12*0.75)=9
    assert adapt_slots(8, 5, 5, 2, 14) == 6  # int(8*0.75)=6, 50%失敗


def test_adapt_slots_respects_min():
    assert adapt_slots(3, 0, 10, 2, 14) == 2


def test_adapt_slots_low_failure_still_increases():
    # 失敗率がちょうど10%(境界)なら過負荷とみなさず増加側
    assert adapt_slots(10, 9, 1, 2, 14) == 11


def _write_status(root: Path, worker: str, job: str, outcome: str) -> None:
    d = root / "results" / worker / job
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.txt").write_text(outcome + "\n", encoding="utf-8")


def test_scan_outcomes_counts_only_this_pc_and_after_since(tmp_path: Path):
    base = "worker03"
    _write_status(tmp_path, f"{base}-s01", "jobA", "completed")
    _write_status(tmp_path, f"{base}-s02", "jobB", "timeout")
    _write_status(tmp_path, f"{base}-s01", "jobC", "completed")
    # 別PCのスロットは数えない
    _write_status(tmp_path, "worker09-s01", "jobD", "completed")

    succ, fail, latest = scan_outcomes(tmp_path, base, since=0.0)
    assert succ == 2
    assert fail == 1
    assert latest > 0.0


def test_scan_outcomes_ignores_before_since(tmp_path: Path):
    base = "worker03"
    _write_status(tmp_path, f"{base}-s01", "old", "completed")
    # 全ステータスの mtime を過去に固定
    old = time.time() - 1000
    for p in tmp_path.rglob("status.txt"):
        import os

        os.utime(p, (old, old))
    succ, fail, _ = scan_outcomes(tmp_path, base, since=time.time())
    assert succ == 0 and fail == 0
