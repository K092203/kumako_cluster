#!/usr/bin/env python3
"""Assign a stable worker id to the current PC."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def machine_name() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown-host"


def user_name() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_status(root: Path, worker_id: str, message: str) -> None:
    atomic_write_json(
        root / "status" / f"{worker_id}.json",
        {
            "worker": worker_id,
            "status": "idle",
            "current_job": None,
            "message": message,
            "updated_at": now_iso(),
        },
    )


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def valid_worker_id(text: str, max_workers: int) -> bool:
    if not text.startswith("worker"):
        return False
    try:
        number = int(text.removeprefix("worker"))
    except ValueError:
        return False
    return 1 <= number <= max_workers


def reuse_existing_local_id(root: Path, local_base: Path, max_workers: int) -> str | None:
    id_file = local_base / "worker_id.txt"
    if not id_file.exists():
        return None
    worker_id = id_file.read_text(encoding="utf-8").strip()
    if not valid_worker_id(worker_id, max_workers):
        raise SystemExit(f"invalid local worker_id.txt: {worker_id}")

    registry_path = root / "workers" / f"{worker_id}.json"
    if not registry_path.exists():
        atomic_write_json(
            registry_path,
            {
                "worker": worker_id,
                "computer": machine_name(),
                "user": user_name(),
                "local_base": str(local_base),
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "source": "local-id",
            },
        )
    write_status(root, worker_id, "registered from local worker_id.txt")
    return worker_id


def reuse_existing_registry(root: Path, local_base: Path) -> str | None:
    current_computer = machine_name().lower()
    current_user = user_name().lower()
    for path in sorted((root / "workers").glob("worker*.json")):
        data = read_json(path)
        if not data:
            continue
        if str(data.get("computer", "")).lower() == current_computer and str(data.get("user", "")).lower() == current_user:
            worker_id = str(data.get("worker") or path.stem)
            (local_base / "worker_id.txt").write_text(worker_id + "\n", encoding="utf-8")
            data["updated_at"] = now_iso()
            data["local_base"] = str(local_base)
            atomic_write_json(path, data)
            write_status(root, worker_id, "registered from shared registry")
            return worker_id
    return None


def claim_new_worker(root: Path, local_base: Path, max_workers: int) -> str:
    registry = root / "workers"
    registry.mkdir(parents=True, exist_ok=True)
    local_base.mkdir(parents=True, exist_ok=True)

    for number in range(1, max_workers + 1):
        worker_id = f"worker{number:02d}"
        path = registry / f"{worker_id}.json"
        data = {
            "worker": worker_id,
            "computer": machine_name(),
            "user": user_name(),
            "local_base": str(local_base),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "source": "auto-claim",
        }
        try:
            with path.open("x", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            (local_base / "worker_id.txt").write_text(worker_id + "\n", encoding="utf-8")
            write_status(root, worker_id, "auto-registered")
            return worker_id
        except FileExistsError:
            continue

    raise SystemExit(f"no free worker id in worker01..worker{max_workers:02d}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign the next available worker id to this PC.")
    parser.add_argument("--root", type=Path, default=default_root(), help="shared cluster root")
    parser.add_argument("--local-base", type=Path, default=Path(r"C:\supercon-worker"), help="local worker base")
    parser.add_argument("--max-workers", type=int, default=21, help="maximum worker count")
    parser.add_argument("--print-id", action="store_true", help="print only the worker id to stdout")
    args = parser.parse_args()

    root = args.root.resolve()
    for rel in ["workers", "status"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    args.local_base.mkdir(parents=True, exist_ok=True)

    worker_id = (
        reuse_existing_local_id(root, args.local_base, args.max_workers)
        or reuse_existing_registry(root, args.local_base)
        or claim_new_worker(root, args.local_base, args.max_workers)
    )

    if args.print_id:
        print(worker_id)
    else:
        print(f"{worker_id} is ready on {machine_name()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
