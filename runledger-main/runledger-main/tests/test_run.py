import sys

from runledger import config as C
from runledger.measure import MeasureSpec
from runledger.run import run_once

PY = sys.executable


def test_run_success(tmp_path):
    rec = run_once([PY, "-c", "print('hi')"], out_root=tmp_path / "runs", name="ok", cwd=tmp_path)
    assert rec.outcome.outcome == C.OUTCOME_COMPLETED
    assert rec.outcome.exit_code == 0
    assert (rec.run_dir / "meta.json").is_file()
    assert (rec.run_dir / "stdout.txt").read_text().strip() == "hi"
    latest = tmp_path / "runs" / "latest"
    assert latest.is_symlink() or (tmp_path / "runs" / "latest.txt").is_file()


def test_run_failure_untrusted_measure(tmp_path):
    code = "import sys; print('#TUNE elapsed=1 score=9 correct=1', file=sys.stderr); sys.exit(3)"
    rec = run_once(
        [PY, "-c", code], out_root=tmp_path / "runs", measure_spec=MeasureSpec(), cwd=tmp_path
    )
    assert rec.outcome.outcome == C.OUTCOME_FAILED
    assert rec.outcome.exit_code == 3
    # non-zero exit must not be trusted as a measurement
    assert rec.measure.score is None
    assert rec.measure.elapsed is None


def test_run_timeout(tmp_path):
    rec = run_once(
        [PY, "-c", "import time; time.sleep(5)"],
        out_root=tmp_path / "runs",
        timeout=0.3,
        cwd=tmp_path,
    )
    assert rec.outcome.outcome == C.OUTCOME_TIMEOUT
    assert rec.outcome.exit_code == C.EXIT_TIMEOUT


def test_run_missing_bin(tmp_path):
    rec = run_once(
        ["definitely-not-a-real-binary-xyz"], out_root=tmp_path / "runs", cwd=tmp_path
    )
    assert rec.outcome.outcome == C.OUTCOME_MISSING_BIN
    assert rec.outcome.exit_code == C.EXIT_MISSING_BIN


def test_input_hash_and_stdin(tmp_path):
    inp = tmp_path / "input.txt"
    inp.write_text("hello")
    rec = run_once(
        [PY, "-c", "import sys; print(sys.stdin.read())"],
        out_root=tmp_path / "runs",
        input_path=inp,
        cwd=tmp_path,
    )
    assert rec.meta["input"]["bytes"] == 5
    assert (rec.run_dir / "input.sha256").is_file()
    assert rec.outcome.stdout.strip() == "hello"


def test_env_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "4")
    monkeypatch.setenv("MY_PRIVATE_THING", "topsecret")
    rec = run_once([PY, "-c", "print(1)"], out_root=tmp_path / "runs", cwd=tmp_path)
    env_txt = (rec.run_dir / "env.txt").read_text()
    assert "OMP_NUM_THREADS=4" in env_txt
    assert "MY_PRIVATE_THING" not in env_txt


def test_capture_env_all(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_PRIVATE_THING", "visible-now")
    rec = run_once(
        [PY, "-c", "print(1)"],
        out_root=tmp_path / "runs",
        capture_env_mode="all",
        cwd=tmp_path,
    )
    assert "MY_PRIVATE_THING=visible-now" in (rec.run_dir / "env.txt").read_text()
