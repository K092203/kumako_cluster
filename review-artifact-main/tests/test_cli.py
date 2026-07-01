"""Integration tests with fake backend."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EXAMPLES = ROOT / "examples"


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-m", "review_artifact.cli", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_logs_fake_backend(tmp_path):
    out = tmp_path / "review"
    result = _run_cli(
        "logs",
        str(EXAMPLES / "sample-results"),
        "--backend",
        "fake",
        "--out",
        str(out),
        "--language",
        "en",
    )
    assert result.returncode == 0, result.stderr
    json_files = list(out.glob("*.json"))
    assert json_files
    doc = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert doc["backend"]["name"] == "fake"
    assert doc["schema_version"] == 1


def test_diff_dry_run():
    result = _run_cli("diff", "--dry-run")
    assert result.returncode == 0
    assert "COLLECTED BUNDLE" in result.stdout
    assert "PROMPT" in result.stdout


def test_files_fake_backend(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Hello\n")
    out = tmp_path / "review"
    result = _run_cli(
        "files",
        str(readme),
        "--backend",
        "fake",
        "--out",
        str(out),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert list(out.glob("*.md"))
