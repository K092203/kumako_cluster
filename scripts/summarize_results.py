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


def sweep_metric(result: dict, objective: str) -> float | None:
    if not is_candidate(result):
        return None
    measure = result.get("measure") or {}
    value = measure.get("elapsed") if objective == "min-elapsed" else measure.get("score")
    return float(value) if value is not None else None


def aggregate_sweeps(results: list[dict], objective: str, agg: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for result in results:
        sweep_id = result.get("sweep_id")
        if sweep_id:
            groups.setdefault(str(sweep_id), []).append(result)

    rows = []
    for sweep_id, items in sorted(groups.items()):
        values = [v for v in (sweep_metric(r, objective) for r in items) if v is not None]
        row = {
            "sweep_id": sweep_id,
            "n": len(items),
            "ok": len(values),
            "params": next((r.get("params") for r in items if r.get("params")), None),
            "mean": sum(values) / len(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }
        row["agg_score"] = row[agg]
        rows.append(row)
    return rows


def choose_best_sweep(rows: list[dict], objective: str) -> dict | None:
    candidates = [r for r in rows if r["agg_score"] is not None]
    if not candidates:
        return None
    if objective == "min-elapsed":
        return min(candidates, key=lambda r: r["agg_score"])
    return max(candidates, key=lambda r: r["agg_score"])


def fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def summarize_by_sweep(root: Path, results: list[dict], objective: str, agg: str, update_incumbent: bool) -> int:
    rows = aggregate_sweeps(results, objective, agg)
    if not rows:
        print("no sweep results yet (jobs need params/sweep_id)")
        return 0

    print(f"sweep_id      n     ok    mean        min         max         params")
    print("------------  ----  ----  ----------  ----------  ----------  ------")
    for row in rows:
        params = json.dumps(row["params"], ensure_ascii=False, sort_keys=True) if row["params"] else "-"
        print(
            f"{row['sweep_id']:<12}  {row['n']:<4}  {row['ok']:<4}  "
            f"{fmt(row['mean']):<10}  {fmt(row['min']):<10}  {fmt(row['max']):<10}  {params}"
        )

    best = choose_best_sweep(rows, objective)
    if best is None:
        print("best sweep: none")
        return 0

    print(
        f"best sweep ({agg} {objective}): {best['sweep_id']} "
        f"agg_score={fmt(best['agg_score'])} n={best['n']} ok={best['ok']} "
        f"params={json.dumps(best['params'], ensure_ascii=False, sort_keys=True) if best['params'] else '-'}"
    )

    if update_incumbent:
        atomic_write_json(
            root / "state" / "incumbent.json",
            {
                "mode": "sweep",
                "objective": objective,
                "agg": agg,
                "sweep_id": best["sweep_id"],
                "params": best["params"],
                "score": best["agg_score"],
                "n": best["n"],
                "ok": best["ok"],
            },
        )
        print("updated state/incumbent.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize SuperCon cluster results.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--objective", choices=["max-score", "min-elapsed"], default="max-score")
    parser.add_argument("--update-incumbent", action="store_true")
    parser.add_argument("--by-sweep", action="store_true", help="aggregate results per sweep_id (parameter set)")
    parser.add_argument("--agg", choices=["mean", "min", "max"], default="mean", help="aggregation for --by-sweep ranking")
    args = parser.parse_args(argv)

    results = load_results(args.root)
    if not results:
        print("no results yet")
        return 0

    if args.by_sweep:
        return summarize_by_sweep(args.root, results, args.objective, args.agg, args.update_incumbent)

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
