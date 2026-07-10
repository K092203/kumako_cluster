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

from _ioutil import StatusHeartbeat


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


def scan_outcomes(root: Path, base_worker_id: str, since: float) -> tuple[int, int, float]:
    """このPCのスロットが since 以降に出した (成功数, 失敗数, 最新mtime) を数える。"""
    succ = fail = 0
    latest = since
    for status_file in (root / "results").glob(f"{base_worker_id}-s*/*/status.txt"):
        try:
            mtime = status_file.stat().st_mtime
            if mtime <= since:
                continue
            outcome = status_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        latest = max(latest, mtime)
        if outcome == "completed":
            succ += 1
        else:
            fail += 1
    return succ, fail, latest


def adapt_slots(desired: int, succ: int, fail: int, min_slots: int, max_slots: int) -> int:
    """goodput(成功ジョブ完了数)に基づくAIMD制御 (Pollux, OSDI 2021 の
    goodput駆動リソース再割当の最小形)。

    失敗率(主にtimeout)が10%を超えたら乗法的減少(過負荷 → wall時間が
    timeoutを超えている)、クリーンに回っていれば加法的増加で上限を探る。
    """
    total = succ + fail
    if not total:
        return desired
    if fail / total > 0.10:
        return max(min_slots, min(desired - 1, int(desired * 0.75)))
    return min(max_slots, desired + 1)


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
    parser.add_argument("--adapt", action="store_true",
                        help="goodputフィードバックでスロット数を動的に増減する")
    parser.add_argument("--min-slots", type=int, default=2)
    parser.add_argument("--max-slots", type=int, default=None, help="default: --slots の値")
    parser.add_argument("--adapt-interval-sec", type=float, default=60.0)
    args = parser.parse_args()
    if args.max_slots is None:
        args.max_slots = args.slots

    root = args.root.resolve()
    base_worker_id = read_base_worker_id(root, args.local_base, args.max_workers)
    supervisor_status = root / "status" / f"{base_worker_id}-supervisor.json"
    # status 書き込みを専用スレッドへ隔離する。SMB がハングしても監視ループと
    # 停止処理(finally)が無限に待たされないため(2026-07 リハーサルの再発防止)。
    heartbeat = StatusHeartbeat(supervisor_status, atomic_write_json).start()
    procs: dict[int, subprocess.Popen] = {}

    desired = args.slots
    last_adapt = time.time()
    last_scan = time.time()
    goodput = None

    def slot_stop_file(slot: int) -> Path:
        return root / "control" / f"{base_worker_id}-s{slot:02d}.stop"

    print(f"{base_worker_id}: supervising {args.slots} slots" + (" (adaptive)" if args.adapt else ""))
    try:
        while True:
            if should_stop(root, base_worker_id):
                heartbeat.update(
                    {
                        "worker": f"{base_worker_id}-supervisor",
                        "status": "stopping",
                        "current_job": None,
                        "message": "stop requested",
                        "slots": args.slots,
                        "updated_at": now_iso(),
                    }
                )
                break

            if args.adapt and time.time() - last_adapt >= args.adapt_interval_sec:
                succ, fail, last_scan = scan_outcomes(root, base_worker_id, last_scan)
                desired = adapt_slots(desired, succ, fail, args.min_slots, args.max_slots)
                goodput = succ / (args.adapt_interval_sec / 60.0)
                last_adapt = time.time()
                print(f"[{now_iso()}] adapt: succ={succ} fail={fail} goodput={goodput:.1f}/min -> slots={desired}")

            restarted = 0
            alive = 0
            for slot in range(1, desired + 1):
                proc = procs.get(slot)
                if proc is None or proc.poll() is not None:
                    if proc is not None:
                        time.sleep(args.restart_delay_sec)
                    slot_stop_file(slot).unlink(missing_ok=True)
                    procs[slot] = launch_worker(root, base_worker_id, slot, args.local_base)
                    restarted += 1
                else:
                    alive += 1
            # 縮退: desired を超えるスロットは stop ファイルで現ジョブ完了後に止める
            for slot in [n for n in procs if n > desired]:
                proc = procs[slot]
                if proc.poll() is None:
                    stop = slot_stop_file(slot)
                    if not stop.exists():
                        stop.write_text(now_iso() + "\n", encoding="utf-8")
                    alive += 1
                else:
                    slot_stop_file(slot).unlink(missing_ok=True)
                    del procs[slot]

            heartbeat.update(
                {
                    "worker": f"{base_worker_id}-supervisor",
                    "status": "running",
                    "current_job": None,
                    "message": f"alive_slots={alive} restarted={restarted} desired={desired}"
                    + (f" goodput={goodput:.1f}/min" if goodput is not None else ""),
                    "slots": desired,
                    "updated_at": now_iso(),
                }
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
        # 最終書き込みも心拍経由。SMB がハングしても join で打ち切られ、
        # プロセスが "stopping" のまま無限に居座らない(worker12 の再発防止)。
        heartbeat.close(
            final_payload={
                "worker": f"{base_worker_id}-supervisor",
                "status": "stopped",
                "current_job": None,
                "message": "all slots stopped",
                "slots": args.slots,
                "updated_at": now_iso(),
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
