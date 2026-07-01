#!/usr/bin/env python3
"""Summarize results and optionally update state/incumbent.json."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_results(root: Path) -> list[dict]:
    results = []
    for path in sorted((root / "results").glob("*/*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data["_path"] = str(path)
        results.append(data)
    return results


def is_candidate(result: dict) -> bool:
    measure = result.get("measure") or {}
    return (
        result.get("outcome") == "completed"
        and measure.get("correct") is not False
        and measure.get("score") is not None
    )


def choose_best(results: list[dict], objective: str) -> dict | None:
    candidates = [r for r in results if is_candidate(r)]
    if not candidates:
        return None
    if objective == "min-elapsed":
        return min(candidates, key=lambda r: (r.get("measure") or {}).get("elapsed", float("inf")))
    return max(candidates, key=lambda r: (r.get("measure") or {}).get("score", float("-inf")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize SuperCon cluster results.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--objective", choices=["max-score", "min-elapsed"], default="max-score")
    parser.add_argument("--update-incumbent", action="store_true")
    args = parser.parse_args()

    results = load_results(args.root)
    if not results:
        print("no results yet")
        return 0

    print("job_id    worker    outcome     score       elapsed")
    print("--------  --------  ----------  ----------  ----------")
    for result in results:
        measure = result.get("measure") or {}
        score = measure.get("score")
        elapsed = measure.get("elapsed")
        print(
            f"{str(result.get('job_id', '-')):<8}  "
            f"{str(result.get('worker', '-')):<8}  "
            f"{str(result.get('outcome', '-')):<10}  "
            f"{str(score if score is not None else '-'):<10}  "
            f"{str(elapsed if elapsed is not None else '-'):<10}"
        )

    best = choose_best(results, args.objective)
    if best is None:
        print("best: none")
        return 0

    measure = best.get("measure") or {}
    print(
        "best: "
        f"job={best.get('job_id')} "
        f"worker={best.get('worker')} "
        f"score={measure.get('score')} "
        f"elapsed={measure.get('elapsed')}"
    )

    if args.update_incumbent:
        atomic_write_json(
            args.root / "state" / "incumbent.json",
            {
                "objective": args.objective,
                "job_id": best.get("job_id"),
                "worker": best.get("worker"),
                "score": measure.get("score"),
                "elapsed": measure.get("elapsed"),
                "result_path": best.get("_path"),
                "finished_at": best.get("finished_at"),
            },
        )
        print("updated state/incumbent.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
