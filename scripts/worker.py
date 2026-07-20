#!/usr/bin/env python3
"""Single-worker runner for the shared-folder SuperCon job cluster."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from _ioutil import SingleFlightTimeout, StatusHeartbeat


TUNE_RE = re.compile(r"#TUNE\s+(?P<body>.*)")

_CONTROL_FILE_CALLS = SingleFlightTimeout()

# ソルバーは1スレッド・並列度はスロット数で稼ぐ(設計書§3.3)。ジョブの env で上書き可。
THREAD_ENV_DEFAULTS = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


def command_env(root: Path, worker_id: str, job_id: str | None = None, job_env: dict | None = None) -> dict:
    env = os.environ.copy()
    for key, value in THREAD_ENV_DEFAULTS.items():
        env.setdefault(key, value)
    env["SUPERCON_ROOT"] = str(root)
    env["SUPERCON_WORKER_ID"] = worker_id
    if job_id is not None:
        env["SUPERCON_JOB_ID"] = job_id
    if job_env:
        env.update({str(k): str(v) for k, v in job_env.items()})
    return env


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
    raise SystemExit(
        "no worker id: pass --worker-id, set SUPERCON_WORKER_ID, "
        "or register this PC with start_worker_auto.bat (register_worker.py)"
    )


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
    for path in (control / "stop_all", control / f"{worker_id}.stop"):
        completed, exists, error = _CONTROL_FILE_CALLS.call(
            str(path), lambda path=path: path.exists(), timeout_sec=5.0
        )
        if not completed:
            continue
        if error is not None:
            raise error
        if exists:
            return True
    return False


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
    candidates = list(pending.glob("*.json"))
    random.shuffle(candidates)
    for source in candidates:
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


# 注意: この存在チェックから書き込みまではアトミックではない（TOCTOU）。
# job_id の一意性は呼び出し側（make_job.py / optuna_bridge.py）の ID 採番が保証する前提で、
# 同一 job_id の 2 ジョブが同時に完了することは通常の運用では起こらない。
# 将来この前提を崩す変更を加える場合は、この関数の非アトミック性を再検討すること。
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
    """Parse a trusted ``#TUNE`` record, searching stderr before stdout.

    Within each stream the last ``#TUNE`` line wins.  Consequently, if both
    streams contain one, the record from stderr takes precedence.
    """
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


def snapshot_digest(source: Path) -> str:
    # 相対パス+サイズ+mtime だけで判定し、SMB越しの全バイト読みを避ける。
    # ジョブ冒頭に毎回呼ばれるため、内容ハッシュだと280スロット全部が
    # スナップショット全体を読み直してしまう。
    digest = hashlib.sha256()
    if source.exists():
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            stat = path.stat()
            digest.update(str(path.relative_to(source)).encode("utf-8"))
            digest.update(f"\x00{stat.st_size}\x00{stat.st_mtime_ns}\x00".encode("ascii"))
    return digest.hexdigest()


def run_snapshot_setup(root: Path, local_dir: Path, target: Path, worker_id: str) -> None:
    manifest_path = target / "cluster_setup.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    command = manifest.get("setup_command")
    if not command:
        return
    timeout_sec = float(manifest.get("timeout_sec", 600))
    log_path = local_dir / "setup.log"
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now_iso()}] setup: {command}\n")
        completed = subprocess.run(
            command,
            cwd=str(target),
            env=command_env(root, worker_id),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            shell=isinstance(command, str),
        )
        log.write(completed.stdout)
        log.write(completed.stderr)
        log.write(f"[{now_iso()}] setup exit={completed.returncode}\n")
    if completed.returncode != 0:
        raise RuntimeError(f"snapshot setup failed (exit {completed.returncode}); see {log_path}")


def copy_repo_snapshot(root: Path, local_dir: Path, worker_id: str) -> Path:
    source = root / "repo_snapshot"
    target = local_dir / "repo"
    marker = local_dir / "repo.sha256"
    current = snapshot_digest(source)
    if target.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == current:
        return target
    marker.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if source.exists() and any(source.iterdir()):
        shutil.copytree(source, target, dirs_exist_ok=True)
    run_snapshot_setup(root, local_dir, target, worker_id)
    marker.write_text(current + "\n", encoding="utf-8")
    return target


def run_dummy(job: dict) -> tuple[str, str, int, bool]:
    seed = int(job.get("seed", 1))
    score = float(job.get("score", 1000 + seed))
    elapsed = float(job.get("elapsed", 0.01))
    time.sleep(max(0.0, min(elapsed, 60.0)))
    stdout = f"dummy job completed: seed={seed} score={score}\n"
    stderr = f"#TUNE elapsed={elapsed:.3f} score={score:.6f} correct=1\n"
    return stdout, stderr, 0, False


def job_cwd(job: dict, repo_dir: Path) -> Path:
    if job.get("cwd"):
        return (repo_dir / str(job["cwd"])).resolve()
    return repo_dir


def artifact_globs(job: dict) -> list[str]:
    artifacts = job.get("artifacts")
    if isinstance(artifacts, str):
        artifacts = [artifacts]
    if not isinstance(artifacts, list):
        return []
    globs = []
    for pattern in artifacts:
        pattern = str(pattern)
        if os.path.isabs(pattern) or ".." in Path(pattern).parts:
            continue
        globs.append(pattern)
    return globs


def clear_artifacts(cwd: Path, globs: list[str]) -> None:
    for pattern in globs:
        try:
            matches = list(cwd.glob(pattern))
        except (ValueError, NotImplementedError):
            continue
        for path in matches:
            if path.is_file():
                path.unlink(missing_ok=True)


def collect_artifacts(cwd: Path, globs: list[str], result_dir: Path) -> dict:
    collected: list[str] = []
    missing: list[str] = []
    for pattern in globs:
        try:
            matches = [p for p in cwd.glob(pattern) if p.is_file()]
        except (ValueError, NotImplementedError):
            matches = []
        if not matches:
            missing.append(pattern)
            continue
        for path in matches:
            rel = path.relative_to(cwd)
            dest = result_dir / "artifacts" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            collected.append(rel.as_posix())
    return {"collected": sorted(collected), "missing": missing}


def render_template(value: str, job: dict) -> str:
    values = {str(k): str(v) for k, v in job.items() if not isinstance(v, (dict, list))}
    if isinstance(job.get("params"), dict):
        values.update({str(k): str(v) for k, v in job["params"].items()})
    for key, replacement in sorted(values.items(), key=lambda item: len(item[0]), reverse=True):
        value = value.replace(f"__{key}__", replacement)
    return value


def render_command(command: object, job: dict) -> object:
    if isinstance(command, str):
        return render_template(command, job)
    if isinstance(command, list):
        return [render_template(str(part), job) for part in command]
    return command


def run_command(
    job: dict,
    repo_dir: Path,
    timeout_sec: float,
    env: dict | None = None,
    heartbeat=None,
    heartbeat_sec: float = 30.0,
) -> tuple[str, str, int | None, bool]:
    command = render_command(job.get("command"), job)
    if not command or command == "dummy":
        return run_dummy(job)

    cwd = job_cwd(job, repo_dir)

    if env is None:
        env = os.environ.copy()
        if isinstance(job.get("env"), dict):
            env.update({str(k): str(v) for k, v in job["env"].items()})

    use_shell = isinstance(command, str)
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=use_shell,
    )
    deadline = time.monotonic() + timeout_sec
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            stdout, stderr = proc.communicate()
            stderr = (stderr or "") + f"\nTIMEOUT after {timeout_sec} seconds\n"
            return stdout or "", stderr, None, True
        try:
            stdout, stderr = proc.communicate(timeout=min(remaining, heartbeat_sec))
            return stdout or "", stderr or "", proc.returncode, False
        except subprocess.TimeoutExpired:
            if heartbeat is not None:
                heartbeat()


def finish_job(root: Path, claimed_path: Path, job_id: str, failed: bool) -> None:
    bucket = root / "jobs" / ("failed" if failed else "done")
    target = unique_path(bucket, f"{job_id}.json")
    os.replace(claimed_path, target)


def process_job(
    root: Path,
    worker_id: str,
    local_dir: Path,
    claimed_path: Path,
    heartbeat: "StatusHeartbeat | None" = None,
) -> None:
    def update_job_status(status: str, current_job: str | None = None, message: str = "") -> None:
        if heartbeat is None:
            update_status(root, worker_id, status, current_job, message)
        else:
            heartbeat.update(
                {
                    "worker": worker_id,
                    "status": status,
                    "current_job": current_job,
                    "message": message,
                    "updated_at": now_iso(),
                }
            )

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
    update_job_status("running", job_id)

    started = now_iso()
    wall_start = time.perf_counter()
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    timed_out = False
    error = ""
    globs = artifact_globs(job)
    artifacts = {"collected": [], "missing": list(globs)}

    try:
        repo_dir = copy_repo_snapshot(root, local_dir, worker_id)
        timeout_sec = float(job.get("timeout_sec", job.get("time_limit_sec", 30)))
        job_heartbeat = lambda: update_job_status("running", job_id)
        job_env = job.get("env") if isinstance(job.get("env"), dict) else None
        env = command_env(root, worker_id, job_id, job_env)
        cwd = job_cwd(job, repo_dir)
        clear_artifacts(cwd, globs)
        stdout, stderr, exit_code, timed_out = run_command(
            job, repo_dir, timeout_sec, env=env, heartbeat=job_heartbeat
        )
        artifacts = collect_artifacts(cwd, globs, result_dir)
    except Exception as exc:
        error = str(exc)
        stderr += f"\nWORKER_ERROR: {error}\n"

    wall_elapsed = time.perf_counter() - wall_start
    trusted_measure = exit_code == 0 and not timed_out and not error
    measure = parse_tune(stdout, stderr, trusted=trusted_measure)
    outcome = "completed" if trusted_measure else ("timeout" if timed_out else "failed")
    warnings: list[str] = []
    if trusted_measure and measure.get("correct") is None:
        warnings.append("measure.correct not reported by solver; treated as passing by default")

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
    result = {
        "job_id": job_id,
        "worker": worker_id,
        "sweep_id": job.get("sweep_id"),
        "params": job.get("params"),
        "outcome": outcome,
        "exit_code": exit_code,
        "wall_elapsed": round(wall_elapsed, 6),
        "measure": measure,
        "artifacts": artifacts,
        "error": error,
        "finished_at": now_iso(),
    }
    if warnings:
        result["warnings"] = warnings
    atomic_write_json(result_dir / "result.json", result)

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
    # status 書き込みを専用スレッドへ隔離する。SMB がハングしてもジョブ取得と
    # control ファイルの監視を止めないため(2026-07 リハーサルの再発防止)。
    heartbeat = StatusHeartbeat(root / "status" / f"{worker_id}.json", atomic_write_json).start()
    local_dir = args.local_dir or (root / "worker_local" / worker_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    heartbeat.update(
        {
            "worker": worker_id,
            "status": "idle",
            "current_job": None,
            "message": "",
            "updated_at": now_iso(),
        }
    )

    while True:
        if should_stop(root, worker_id):
            heartbeat.close(
                final_payload={
                    "worker": worker_id,
                    "status": "stopped",
                    "current_job": None,
                    "message": "control stop requested",
                    "updated_at": now_iso(),
                }
            )
            return 0

        claimed = claim_job(root, worker_id)
        if claimed is None:
            heartbeat.update(
                {
                    "worker": worker_id,
                    "status": "idle",
                    "current_job": None,
                    "message": "",
                    "updated_at": now_iso(),
                }
            )
            if args.once:
                heartbeat.close()
                return 0
            time.sleep(args.poll_sec)
            continue

        process_job(root, worker_id, local_dir, claimed, heartbeat=heartbeat)
        heartbeat.update(
            {
                "worker": worker_id,
                "status": "idle",
                "current_job": None,
                "message": "",
                "updated_at": now_iso(),
            }
        )
        if args.once:
            heartbeat.close()
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
