#!/usr/bin/env python3
"""Single-worker runner for the shared-folder SuperCon job cluster."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TUNE_RE = re.compile(r"#TUNE\s+(?P<body>.*)")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_worker_id(root: Path) -> str:
    env_id = os.environ.get("SUPERCON_WORKER_ID") or os.environ.get("WORKER_ID")
    if env_id:
        return env_id.strip()
    worker_id_file = root / "worker_local" / "worker_id.txt"
    if worker_id_file.exists():
        text = worker_id_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "worker01"


def update_status(root: Path, worker_id: str, status: str, current_job: str | None = None, message: str = "") -> None:
    atomic_write_json(
        root / "status" / f"{worker_id}.json",
        {
            "worker": worker_id,
            "status": status,
            "current_job": current_job,
            "message": message,
            "updated_at": now_iso(),
        },
    )


def should_stop(root: Path, worker_id: str) -> bool:
    control = root / "control"
    return (control / "stop_all").exists() or (control / f"{worker_id}.stop").exists()


def ensure_layout(root: Path) -> None:
    for rel in [
        "jobs/pending",
        "jobs/running",
        "jobs/done",
        "jobs/failed",
        "results",
        "status",
        "control",
        "repo_snapshot",
        "reviews",
        "state",
        "logs",
        "worker_local",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)


def claim_job(root: Path, worker_id: str) -> Path | None:
    pending = root / "jobs" / "pending"
    running = root / "jobs" / "running"
    for source in sorted(pending.glob("*.json")):
        target = running / f"{source.stem}--{worker_id}.json"
        try:
            os.replace(source, target)
            return target
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def read_job_with_retry(path: Path, attempts: int = 20, delay_sec: float = 0.1) -> dict:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(delay_sec)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"could not read job: {path}")


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for i in range(2, 10000):
        numbered = directory / f"{stem}-{i}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError(f"could not allocate unique path for {candidate}")


def parse_tune(stdout: str, stderr: str, trusted: bool) -> dict:
    if not trusted:
        return {"elapsed": None, "score": None, "correct": None}

    for text in [stderr, stdout]:
        for line in reversed(text.splitlines()):
            match = TUNE_RE.search(line)
            if not match:
                continue
            values: dict[str, object] = {"elapsed": None, "score": None, "correct": None}
            for part in match.group("body").split():
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                if key in {"elapsed", "score"}:
                    try:
                        values[key] = float(value)
                    except ValueError:
                        values[key] = None
                elif key == "correct":
                    values[key] = value.lower() in {"1", "true", "yes", "ok"}
            return values
    return {"elapsed": None, "score": None, "correct": None}


def copy_repo_snapshot(root: Path, local_dir: Path) -> Path:
    source = root / "repo_snapshot"
    target = local_dir / "repo"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if source.exists() and any(source.iterdir()):
        shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def run_dummy(job: dict) -> tuple[str, str, int, bool]:
    seed = int(job.get("seed", 1))
    score = float(job.get("score", 1000 + seed))
    elapsed = float(job.get("elapsed", 0.01))
    time.sleep(max(0.0, min(elapsed, 60.0)))
    stdout = f"dummy job completed: seed={seed} score={score}\n"
    stderr = f"#TUNE elapsed={elapsed:.3f} score={score:.6f} correct=1\n"
    return stdout, stderr, 0, False


def render_template(value: str, job: dict) -> str:
    values = {str(k): str(v) for k, v in job.items()}
    for key, replacement in values.items():
        value = value.replace(f"__{key}__", replacement)
    try:
        return value.format_map(values)
    except Exception:
        return value


def render_command(command: object, job: dict) -> object:
    if isinstance(command, str):
        return render_template(command, job)
    if isinstance(command, list):
        return [render_template(str(part), job) for part in command]
    return command


def run_command(job: dict, repo_dir: Path, timeout_sec: float) -> tuple[str, str, int | None, bool]:
    command = render_command(job.get("command"), job)
    if not command or command == "dummy":
        return run_dummy(job)

    cwd = repo_dir
    if job.get("cwd"):
        cwd = (repo_dir / str(job["cwd"])).resolve()

    env = os.environ.copy()
    if isinstance(job.get("env"), dict):
        env.update({str(k): str(v) for k, v in job["env"].items()})

    use_shell = isinstance(command, str)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            shell=use_shell,
        )
        return completed.stdout, completed.stderr, completed.returncode, False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        stderr += f"\nTIMEOUT after {timeout_sec} seconds\n"
        return stdout, stderr, None, True


def finish_job(root: Path, claimed_path: Path, job_id: str, failed: bool) -> None:
    bucket = root / "jobs" / ("failed" if failed else "done")
    target = unique_path(bucket, f"{job_id}.json")
    os.replace(claimed_path, target)


def process_job(root: Path, worker_id: str, local_dir: Path, claimed_path: Path) -> None:
    try:
        job = read_job_with_retry(claimed_path)
    except Exception as exc:
        job_id = claimed_path.stem
        result_dir = root / "results" / worker_id / job_id
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "error.txt").write_text(f"invalid job json: {exc}\n", encoding="utf-8")
        finish_job(root, claimed_path, job_id, failed=True)
        return

    job_id = str(job.get("job_id") or claimed_path.stem.split("--", 1)[0])
    result_dir = root / "results" / worker_id / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    update_status(root, worker_id, "running", job_id)

    started = now_iso()
    wall_start = time.perf_counter()
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    timed_out = False
    error = ""

    try:
        repo_dir = copy_repo_snapshot(root, local_dir)
        timeout_sec = float(job.get("timeout_sec", job.get("time_limit_sec", 30)))
        stdout, stderr, exit_code, timed_out = run_command(job, repo_dir, timeout_sec)
    except Exception as exc:
        error = str(exc)
        stderr += f"\nWORKER_ERROR: {error}\n"

    wall_elapsed = time.perf_counter() - wall_start
    trusted_measure = exit_code == 0 and not timed_out and not error
    measure = parse_tune(stdout, stderr, trusted=trusted_measure)
    outcome = "completed" if trusted_measure else ("timeout" if timed_out else "failed")

    (result_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (result_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    (result_dir / "status.txt").write_text(outcome + "\n", encoding="utf-8")
    atomic_write_json(
        result_dir / "meta.json",
        {
            "job": job,
            "worker": worker_id,
            "started_at": started,
            "finished_at": now_iso(),
        },
    )
    atomic_write_json(
        result_dir / "result.json",
        {
            "job_id": job_id,
            "worker": worker_id,
            "outcome": outcome,
            "exit_code": exit_code,
            "wall_elapsed": round(wall_elapsed, 6),
            "measure": measure,
            "error": error,
            "finished_at": now_iso(),
        },
    )

    finish_job(root, claimed_path, job_id, failed=outcome in {"failed", "timeout"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one SuperCon shared-folder worker.")
    parser.add_argument("--root", type=Path, default=default_root(), help="shared cluster root")
    parser.add_argument("--worker-id", default=None, help="worker id, for example worker01")
    parser.add_argument("--local-dir", type=Path, default=None, help="local working directory")
    parser.add_argument("--once", action="store_true", help="process at most one job and exit")
    parser.add_argument("--poll-sec", type=float, default=3.0, help="seconds between polls")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    ensure_layout(root)
    worker_id = args.worker_id or read_worker_id(root)
    local_dir = args.local_dir or (root / "worker_local" / worker_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    update_status(root, worker_id, "idle")

    while True:
        if should_stop(root, worker_id):
            update_status(root, worker_id, "stopped", message="control stop requested")
            return 0

        claimed = claim_job(root, worker_id)
        if claimed is None:
            update_status(root, worker_id, "idle")
            if args.once:
                return 0
            time.sleep(args.poll_sec)
            continue

        process_job(root, worker_id, local_dir, claimed)
        update_status(root, worker_id, "idle")
        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
