#!/usr/bin/env python3
"""検証6: goodputフィードバックによる動的スロット割当 vs 静的スロット.

実際に supervise_slots.py + worker.py を起動して測る(シミュレーションではない)。

シナリオ(このマシンは4コア):
  cpu_static12 : 12スロット固定, CPUバウンドジョブ(CPU時間3.5s, timeout 8s)
                 → 過剰スロットでwall時間がtimeoutを超え全滅するはずの構成
  cpu_adapt12  : 同じ条件で --adapt (12開始, min2, max14, 30s間隔)
  cpu_static4  : 4スロット固定 (コア数=理想値を知っている場合の参照値)
  sleep_static4: 4スロット固定, sleep 2sジョブ (I/O待ち型; スロット増が効く方向)
  sleep_adapt4 : 同じ条件で --adapt (4開始, max14)

指標: goodput = 成功ジョブ数/分 (jobs/done を数える)。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
BASE = Path("/home/user/exp6")
CPU_CODE = "import time\nt=time.process_time()\nwhile time.process_time()-t<3.5: pass"


def enqueue(root: Path, kind: str, count: int):
    pending = root / "jobs" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        if kind == "cpu":
            job = {"job_id": f"j{i:05d}", "seed": i, "timeout_sec": 8,
                   "command": [sys.executable, "-c", CPU_CODE]}
        else:
            job = {"job_id": f"j{i:05d}", "seed": i, "elapsed": 2.0,
                   "timeout_sec": 30, "command": "dummy"}
        (pending / f"j{i:05d}.json").write_text(json.dumps(job))


def run_scenario(name: str, kind: str, minutes: float, slots: int, adapt: bool, count: int):
    root = BASE / name
    shutil.rmtree(root, ignore_errors=True)
    (root / "repo_snapshot").mkdir(parents=True)
    shutil.copytree(WT / "scripts", root / "scripts")  # supervisorはroot直下のscriptsを参照する
    enqueue(root, kind, count)
    cmd = [sys.executable, str(WT / "scripts" / "supervise_slots.py"), "--root", str(root),
           "--slots", str(slots), "--local-base", str(root / "local"), "--restart-delay-sec", "0.5"]
    if adapt:
        cmd += ["--adapt", "--min-slots", "2", "--max-slots", "14", "--adapt-interval-sec", "30"]
    log = (root / "supervisor.log").open("w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    time.sleep(minutes * 60)
    (root / "control").mkdir(exist_ok=True)
    (root / "control" / "stop_all").write_text("stop\n")
    try:
        proc.wait(timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(2)
    done = len(list((root / "jobs" / "done").glob("*.json")))
    failed = len(list((root / "jobs" / "failed").glob("*.json")))
    sup_files = list((root / "status").glob("*-supervisor.json"))
    sup = json.loads(sup_files[0].read_text()) if sup_files else {}
    row = {"scenario": name, "kind": kind, "minutes": minutes, "slots_start": slots,
           "adapt": adapt, "done": done, "failed": failed,
           "goodput_per_min": round(done / minutes, 1), "final_slots": sup.get("slots")}
    print(json.dumps(row))
    return row


def main():
    rows = [
        run_scenario("cpu_static12", "cpu", 5, 12, False, 900),
        run_scenario("cpu_adapt12", "cpu", 5, 12, True, 900),
        run_scenario("cpu_static4", "cpu", 5, 4, False, 900),
        run_scenario("sleep_static4", "sleep", 3, 4, False, 1500),
        run_scenario("sleep_adapt4", "sleep", 3, 4, True, 1500),
    ]
    (WT / "experiments" / "results_adapt.json").write_text(json.dumps({"rows": rows}, indent=1))


if __name__ == "__main__":
    main()
