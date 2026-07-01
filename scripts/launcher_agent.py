#!/usr/bin/env python3
"""Watch shared control files and start a slot supervisor on this PC."""

from __future__ import annotations

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


def register(root: Path, local_base: Path, max_workers: int) -> str:
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


def read_start_request(root: Path, base_worker_id: str) -> dict | None:
    control = root / "control"
    candidates = [control / f"{base_worker_id}.start_slots.json", control / "start_slots_all.json"]
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            data["_path"] = str(path)
            return data
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch control files and launch slot supervisor.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--local-base", type=Path, default=Path(r"C:\supercon-worker"))
    parser.add_argument("--max-workers", type=int, default=21)
    parser.add_argument("--poll-sec", type=float, default=3.0)
    args = parser.parse_args()

    root = args.root.resolve()
    base_worker_id = register(root, args.local_base, args.max_workers)
    status_path = root / "status" / f"{base_worker_id}-launcher.json"
    supervisor: subprocess.Popen | None = None
    last_request_path = ""

    print(f"{base_worker_id}: launcher agent ready")
    while True:
        if (root / "control" / "stop_all").exists() or (root / "control" / f"{base_worker_id}.launcher.stop").exists():
            if supervisor is not None and supervisor.poll() is None:
                supervisor.terminate()
            atomic_write_json(
                status_path,
                {
                    "worker": f"{base_worker_id}-launcher",
                    "status": "stopped",
                    "current_job": None,
                    "message": "stop requested",
                    "updated_at": now_iso(),
                },
            )
            return 0

        req = read_start_request(root, base_worker_id)
        if req is not None:
            slots = int(req.get("slots", 14))
            req_path = str(req.get("_path", ""))
            if supervisor is None or supervisor.poll() is not None or req_path != last_request_path:
                if supervisor is not None and supervisor.poll() is None:
                    supervisor.terminate()
                    time.sleep(1)
                cmd = [
                    sys.executable,
                    str(root / "scripts" / "supervise_slots.py"),
                    "--root",
                    str(root),
                    "--slots",
                    str(slots),
                    "--max-workers",
                    str(args.max_workers),
                    "--local-base",
                    str(args.local_base),
                ]
                supervisor = subprocess.Popen(cmd)
                last_request_path = req_path

        atomic_write_json(
            status_path,
            {
                "worker": f"{base_worker_id}-launcher",
                "status": "running",
                "current_job": None,
                "message": "watching control files",
                "supervisor_alive": supervisor is not None and supervisor.poll() is None,
                "updated_at": now_iso(),
            },
        )
        time.sleep(args.poll_sec)


if __name__ == "__main__":
    raise SystemExit(main())
