#!/usr/bin/env python3
"""Create pending jobs for the shared-folder cluster."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def existing_job_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    for bucket in ["pending", "running", "done", "failed"]:
        for path in (root / "jobs" / bucket).glob("*.json"):
            ids.add(path.stem.split("--", 1)[0])
    for path in (root / "results").glob("*/*/result.json"):
        ids.add(path.parent.name)
    return ids


def coerce_value(text: str) -> object:
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text


def parse_params(items: list[str]) -> dict:
    params: dict[str, object] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"invalid --param (expected key=value): {item}")
        key, value = item.split("=", 1)
        params[key] = coerce_value(value)
    return params


def sweep_id_for(params: dict) -> str:
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def find_start(prefix: str, count: int, used: set[str]) -> int:
    number = 1
    while True:
        if all(f"{prefix}{number + offset:03d}" not in used for offset in range(count)):
            return number
        number += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create SuperCon cluster job JSON files.")
    parser.add_argument("--root", type=Path, default=default_root(), help="shared cluster root")
    parser.add_argument("--count", type=int, default=1, help="number of jobs to create")
    parser.add_argument("--prefix", default="job", help="job id prefix")
    parser.add_argument("--start", type=int, default=None, help="first job number and seed")
    parser.add_argument("--solver", default="dummy", help="solver label")
    parser.add_argument("--case", default="case001", help="case label")
    parser.add_argument("--timeout-sec", type=float, default=30.0, help="job timeout")
    parser.add_argument("--dummy-elapsed", type=float, default=0.01, help="elapsed value for dummy jobs")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="output file glob (relative to job cwd) to collect into results/, repeatable",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="solver parameter key=value (repeatable); becomes job params and __key__ template value",
    )
    parser.add_argument("--sweep-id", default=None, help="explicit sweep id (default: hash of params)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="optional command after --")
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    command_value: list[str] | str = command if command else "dummy"

    params = parse_params(args.param)
    pending = args.root / "jobs" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    used = existing_job_ids(args.root)
    start = args.start if args.start is not None else find_start(args.prefix, args.count, used)

    created: list[str] = []
    for offset in range(args.count):
        number = start + offset
        job_id = f"{args.prefix}{number:03d}"
        if job_id in used:
            raise SystemExit(f"job already exists outside pending too: {job_id}")
        path = pending / f"{job_id}.json"
        if path.exists():
            raise SystemExit(f"job already exists: {path}")
        job = {
            "job_id": job_id,
            "solver": args.solver,
            "case": args.case,
            "seed": number,
            "elapsed": args.dummy_elapsed,
            "timeout_sec": args.timeout_sec,
            "command": command_value,
            "created_at": now_iso(),
        }
        if args.artifact:
            job["artifacts"] = list(args.artifact)
        if params or args.sweep_id:
            job["params"] = params
            job["sweep_id"] = args.sweep_id or sweep_id_for(params)
        atomic_write_json(path, job)
        created.append(job_id)

    print(f"created {len(created)} job(s): " + ", ".join(created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
