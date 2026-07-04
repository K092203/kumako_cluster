#!/usr/bin/env python3
"""Run and restart multiple worker slots on one PC."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_base_worker_id(root: Path, local_base: Path, max_workers: int) -> str:
    cmd = [
        sys.executable,
        str(root / "scripts" / "register_worker.py"),
        "--root",
        str(root),
        "--local-base",
        str(local_base),
        "--max-workers",
        str(max_workers),
        "--print-id",
    ]
    completed = subprocess.run(cmd, text=True, capture_output=True, check=True)
    return completed.stdout.strip().splitlines()[-1]


def should_stop(root: Path, base_worker_id: str) -> bool:
    control = root / "control"
    return (
        (control / "stop_all").exists()
        or (control / f"{base_worker_id}.stop").exists()
        or (control / f"{base_worker_id}.slots.stop").exists()
    )


def launch_worker(root: Path, base_worker_id: str, slot_number: int, local_base: Path) -> subprocess.Popen:
    slot_id = f"{base_worker_id}-s{slot_number:02d}"
    local_dir = local_base / slot_id
    local_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(root / "scripts" / "worker.py"),
        "--root",
        str(root),
        "--worker-id",
        slot_id,
        "--local-dir",
        str(local_dir),
    ]
    log_dir = root / "logs" / "supervisor" / base_worker_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{slot_id}.log"
    log = log_path.open("a", encoding="utf-8")
    log.write(f"\n[{now_iso()}] starting {' '.join(cmd)}\n")
    log.flush()
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervise worker slots on one PC.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--slots", type=int, default=14)
    parser.add_argument("--max-workers", type=int, default=21)
    parser.add_argument("--local-base", type=Path, default=Path(r"C:\supercon-worker"))
    parser.add_argument("--restart-delay-sec", type=float, default=2.0)
    args = parser.parse_args()

    root = args.root.resolve()
    base_worker_id = read_base_worker_id(root, args.local_base, args.max_workers)
    supervisor_status = root / "status" / f"{base_worker_id}-supervisor.json"
    procs: dict[int, subprocess.Popen] = {}

    print(f"{base_worker_id}: supervising {args.slots} slots")
    try:
        while True:
            if should_stop(root, base_worker_id):
                atomic_write_json(
                    supervisor_status,
                    {
                        "worker": f"{base_worker_id}-supervisor",
                        "status": "stopping",
                        "current_job": None,
                        "message": "stop requested",
                        "slots": args.slots,
                        "updated_at": now_iso(),
                    },
                )
                break

            restarted = 0
            alive = 0
            for slot in range(1, args.slots + 1):
                proc = procs.get(slot)
                if proc is None or proc.poll() is not None:
                    if proc is not None:
                        time.sleep(args.restart_delay_sec)
                    procs[slot] = launch_worker(root, base_worker_id, slot, args.local_base)
                    restarted += 1
                else:
                    alive += 1

            atomic_write_json(
                supervisor_status,
                {
                    "worker": f"{base_worker_id}-supervisor",
                    "status": "running",
                    "current_job": None,
                    "message": f"alive_slots={alive} restarted={restarted}",
                    "slots": args.slots,
                    "updated_at": now_iso(),
                },
            )
            time.sleep(5)
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        time.sleep(1)
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
        atomic_write_json(
            supervisor_status,
            {
                "worker": f"{base_worker_id}-supervisor",
                "status": "stopped",
                "current_job": None,
                "message": "all slots stopped",
                "slots": args.slots,
                "updated_at": now_iso(),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
