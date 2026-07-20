"""Tests for archiving completed cluster data."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_results


def test_move_children_preserves_existing_archive_contents(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "archive"
    source.mkdir()
    (source / "result.json").write_text("first", encoding="utf-8")

    assert archive_results.move_children(source, target) == 1
    assert (target / "result.json").read_text(encoding="utf-8") == "first"

    (source / "result.json").write_text("second", encoding="utf-8")
    assert archive_results.move_children(source, target) == 1

    assert (target / "result.json").read_text(encoding="utf-8") == "first"
    assert (target / "result-2.json").read_text(encoding="utf-8") == "second"
