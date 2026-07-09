"""Tests for launcher_agent's supervisor command wiring (--adapt passthrough)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from launcher_agent import build_supervisor_cmd, request_signature  # noqa: E402

ROOT = Path("/cluster")
LOCAL = Path("/local")


def test_cmd_without_adapt_is_plain():
    cmd = build_supervisor_cmd(ROOT, {"slots": 14}, max_workers=21, local_base=LOCAL)
    assert "--adapt" not in cmd
    assert "--slots" in cmd and "14" in cmd
    assert cmd[cmd.index("--slots") + 1] == "14"


def test_cmd_with_adapt_flag_only():
    cmd = build_supervisor_cmd(ROOT, {"slots": 12, "adapt": True}, max_workers=21, local_base=LOCAL)
    assert "--adapt" in cmd
    # 明示指定が無ければ min/max/interval は付けない(supervisor側の既定に委ねる)
    assert "--min-slots" not in cmd
    assert "--max-slots" not in cmd


def test_cmd_with_adapt_and_bounds():
    req = {"slots": 12, "adapt": True, "min_slots": 2, "max_slots": 14, "adapt_interval_sec": 30}
    cmd = build_supervisor_cmd(ROOT, req, max_workers=21, local_base=LOCAL)
    assert cmd[cmd.index("--min-slots") + 1] == "2"
    assert cmd[cmd.index("--max-slots") + 1] == "14"
    assert cmd[cmd.index("--adapt-interval-sec") + 1] == "30"


def test_signature_changes_when_adapt_toggles():
    base = {"_path": "/c/start_slots_all.json", "slots": 14}
    adapted = {"_path": "/c/start_slots_all.json", "slots": 14, "adapt": True}
    # 同じファイルの上書きでも adapt の有無で署名が変わる → 再起動される
    assert request_signature(base) != request_signature(adapted)


def test_signature_stable_for_same_content():
    req = {"_path": "/c/start_slots_all.json", "slots": 14, "adapt": True}
    assert request_signature(dict(req)) == request_signature(dict(req))
