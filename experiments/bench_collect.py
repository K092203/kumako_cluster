"""Benchmark result polling cost for optuna_bridge.find_result.

This creates a synthetic cluster root and compares the current polling shape
(find_result for every in-flight job on every poll) with a flat done/failed
index that only calls find_result for newly completed in-flight jobs.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from optuna_bridge import find_result  # noqa: E402


POLL_COUNT = 50
WORKER_COUNT = 20
IN_FLIGHT_DONE = 80
IN_FLIGHT_MISSING = 400
MISSING = object()


Counts = Dict[str, int]
Result = Dict[str, Any]


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")


def result_doc(job_id: str) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "outcome": "completed",
        "measure": {"score": 1.0, "correct": True},
    }


def job_doc(job_id: str) -> Dict[str, Any]:
    return {"job_id": job_id, "command": ["true"], "params": {}}


def write_completed_job(root: Path, worker_id: str, job_id: str) -> None:
    write_json(root / "results" / worker_id / job_id / "result.json", result_doc(job_id))
    write_json(root / "jobs" / "done" / (job_id + ".json"), job_doc(job_id))


def setup_root(old_results: int) -> Tuple[Path, List[str]]:
    root = Path(tempfile.mkdtemp(prefix="bench-collect-"))
    for rel in ("jobs/done", "jobs/failed", "results"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    workers = ["worker%02d" % i for i in range(1, WORKER_COUNT + 1)]
    for worker_id in workers:
        (root / "results" / worker_id).mkdir(parents=True, exist_ok=True)

    for k in range(old_results):
        job_id = "old-t%04d-i%04d" % (k, k % 15)
        write_completed_job(root, workers[k % WORKER_COUNT], job_id)

    job_ids: List[str] = []
    for k in range(IN_FLIGHT_DONE):
        job_id = "new-t%04d-i%04d" % (k, k % 15)
        job_ids.append(job_id)
        write_completed_job(root, workers[(old_results + k) % WORKER_COUNT], job_id)
    for k in range(IN_FLIGHT_MISSING):
        job_ids.append("live-t%04d-i%04d" % (k, k % 15))
    return root, job_ids


def accepted_score(result: Result) -> Optional[float]:
    measure = result.get("measure") or {}
    score = measure.get("score")
    if result.get("outcome") == "completed" and measure.get("correct") is not False and score is not None:
        return float(score)
    return None


def current_poll(root: Path, job_ids: List[str]) -> List[float]:
    scores: List[float] = []
    for job_id in job_ids:
        result = find_result(root, job_id)
        if result is None:
            continue
        score = accepted_score(result)
        if score is not None:
            scores.append(score)
    return scores


class ResultIndex:
    def __init__(self, root: Path, in_flight: Iterable[str]) -> None:
        self.root = root
        self.in_flight: Set[str] = set(in_flight)
        self.cache: Dict[str, object] = {}

    def resolve_job_id(self, stem: str) -> Optional[str]:
        if stem in self.in_flight:
            return stem
        head, sep, tail = stem.rpartition("-")
        if sep and tail.isdigit() and head in self.in_flight:
            return head
        return None

    def poll(self) -> List[float]:
        newly_visible: Set[str] = set()
        for bucket in ("done", "failed"):
            directory = self.root / "jobs" / bucket
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not entry.name.endswith(".json"):
                            continue
                        job_id = self.resolve_job_id(entry.name[:-5])
                        if job_id is not None and job_id not in self.cache:
                            newly_visible.add(job_id)
            except FileNotFoundError:
                continue

        for job_id in newly_visible:
            result = find_result(self.root, job_id)
            self.cache[job_id] = result if result is not None else MISSING

        scores: List[float] = []
        for job_id in self.in_flight:
            cached = self.cache.get(job_id)
            if cached is None or cached is MISSING:
                continue
            score = accepted_score(cached)  # type: ignore[arg-type]
            if score is not None:
                scores.append(score)
        return scores


class OsCounter:
    def __init__(self) -> None:
        self.counts: Counts = {"scandir": 0, "stat": 0, "lstat": 0, "open": 0}
        self._orig_scandir = os.scandir
        self._orig_stat = os.stat
        self._orig_lstat = os.lstat
        self._orig_open = os.open

    def __enter__(self) -> "OsCounter":
        def scandir(path: Any = None) -> Any:
            self.counts["scandir"] += 1
            if path is None:
                return self._orig_scandir()
            return self._orig_scandir(path)

        def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
            self.counts["stat"] += 1
            return self._orig_stat(path, *args, **kwargs)

        def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
            self.counts["lstat"] += 1
            return self._orig_lstat(path, *args, **kwargs)

        def open_(path: Any, flags: int, mode: int = 0o777, *args: Any, **kwargs: Any) -> int:
            self.counts["open"] += 1
            return self._orig_open(path, flags, mode, *args, **kwargs)

        os.scandir = scandir  # type: ignore[assignment]
        os.stat = stat  # type: ignore[assignment]
        os.lstat = lstat  # type: ignore[assignment]
        os.open = open_  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        os.scandir = self._orig_scandir  # type: ignore[assignment]
        os.stat = self._orig_stat  # type: ignore[assignment]
        os.lstat = self._orig_lstat  # type: ignore[assignment]
        os.open = self._orig_open  # type: ignore[assignment]


def count_ops(counts: Counts) -> int:
    return sum(counts.values())


def measure(fn: Callable[[], List[float]]) -> Tuple[List[Counts], List[float], List[int]]:
    counts_by_poll: List[Counts] = []
    ops_by_poll: List[int] = []
    ms_by_poll: List[float] = []
    found_by_poll: List[int] = []
    for _ in range(POLL_COUNT):
        with OsCounter() as counter:
            start = time.perf_counter()
            scores = fn()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        counts_by_poll.append(dict(counter.counts))
        ops_by_poll.append(count_ops(counter.counts))
        ms_by_poll.append(elapsed_ms)
        found_by_poll.append(len(scores))
    return counts_by_poll, ms_by_poll, found_by_poll


def average(values: List[float]) -> float:
    return sum(values) / len(values)


def summarize(counts_by_poll: List[Counts], ms: List[float]) -> Dict[str, Any]:
    ops = [count_ops(counts) for counts in counts_by_poll]
    avg_by_op = {
        name: average([float(counts[name]) for counts in counts_by_poll])
        for name in ("scandir", "stat", "lstat", "open")
    }
    total_by_op = {
        name: sum(counts[name] for counts in counts_by_poll)
        for name in ("scandir", "stat", "lstat", "open")
    }
    return {
        "avg_ops_per_poll": average([float(v) for v in ops]),
        "avg_ops_per_poll_by_name": avg_by_op,
        "avg_wall_ms_per_poll": average(ms),
        "total_ops": float(sum(ops)),
        "total_ops_by_name": total_by_op,
        "total_wall_ms": sum(ms),
    }


def run_case(old_results: int) -> Dict[str, Any]:
    root, job_ids = setup_root(old_results)
    try:
        current_counts, current_ms, current_found = measure(lambda: current_poll(root, job_ids))
        index = ResultIndex(root, job_ids)
        indexed_counts, indexed_ms, indexed_found = measure(index.poll)
        assert current_found == indexed_found, (current_found, indexed_found)
        current_summary = summarize(current_counts, current_ms)
        indexed_summary = summarize(indexed_counts, indexed_ms)
        ratio = current_summary["avg_ops_per_poll"] / indexed_summary["avg_ops_per_poll"]
        return {
            "old_results": old_results,
            "polls": POLL_COUNT,
            "in_flight": len(job_ids),
            "in_flight_done": IN_FLIGHT_DONE,
            "in_flight_missing": IN_FLIGHT_MISSING,
            "found_each_poll": current_found[0],
            "current": current_summary,
            "indexed": indexed_summary,
            "ratio_current_ops_over_indexed_ops": ratio,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    cases = [run_case(old_results) for old_results in (500, 3000)]
    out = {
        "description": "result polling benchmark: current find_result per job vs done/failed indexed cache",
        "polls": POLL_COUNT,
        "operation_counts": ["os.scandir", "os.stat", "os.lstat", "os.open"],
        "cases": cases,
    }
    out_path = ROOT / "experiments" / "results_collect_bench.json"
    out_path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for case in cases:
        print(
            "R={old_results} current_ops={current_ops:.1f} indexed_ops={indexed_ops:.1f} "
            "ratio={ratio:.2f} current_ms={current_ms:.3f} indexed_ms={indexed_ms:.3f}".format(
                old_results=case["old_results"],
                current_ops=case["current"]["avg_ops_per_poll"],
                indexed_ops=case["indexed"]["avg_ops_per_poll"],
                ratio=case["ratio_current_ops_over_indexed_ops"],
                current_ms=case["current"]["avg_wall_ms_per_poll"],
                indexed_ms=case["indexed"]["avg_wall_ms_per_poll"],
            )
        )
    print("wrote %s" % out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
