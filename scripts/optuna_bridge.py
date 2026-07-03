#!/usr/bin/env python3
"""Parameter search driver for the shared-folder cluster (runs on the parent PC).

Asks a sampler for parameter sets, writes them as pending jobs (one job per
instance seed), collects result.json scores, and tells the sampler. Uses
Optuna (TPE + JournalStorage) when importable, otherwise falls back to a
stdlib-only random + hill-climb sampler with the same CLI.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
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


# ---------------------------------------------------------------------------
# search space spec

VALID_TYPES = {"float", "int", "cat"}


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec.get("params"), dict) or not spec["params"]:
        raise SystemExit("spec must define a non-empty 'params' object")
    for name, p in spec["params"].items():
        kind = p.get("type")
        if kind not in VALID_TYPES:
            raise SystemExit(f"param {name}: type must be one of {sorted(VALID_TYPES)}")
        if kind == "cat":
            if not p.get("choices"):
                raise SystemExit(f"param {name}: cat requires non-empty 'choices'")
        else:
            if "low" not in p or "high" not in p or p["low"] >= p["high"]:
                raise SystemExit(f"param {name}: requires low < high")
            if p.get("log") and p["low"] <= 0:
                raise SystemExit(f"param {name}: log scale requires low > 0")
    if not spec.get("command"):
        raise SystemExit("spec must define 'command'")
    if not isinstance(spec.get("instances"), list) or not spec["instances"]:
        raise SystemExit("spec must define non-empty 'instances' (list of seeds)")
    spec.setdefault("name", path.stem)
    spec.setdefault("timeout_sec", 60)
    return spec


# ---------------------------------------------------------------------------
# builtin fallback engine (stdlib only)


def sample_random(params_spec: dict, rng: random.Random) -> dict:
    params: dict[str, object] = {}
    for name, p in params_spec.items():
        if p["type"] == "cat":
            params[name] = rng.choice(p["choices"])
        elif p["type"] == "int":
            params[name] = rng.randint(int(p["low"]), int(p["high"]))
        elif p.get("log"):
            params[name] = math.exp(rng.uniform(math.log(p["low"]), math.log(p["high"])))
        else:
            params[name] = rng.uniform(p["low"], p["high"])
    return params


def perturb(params_spec: dict, base: dict, rng: random.Random) -> dict:
    params: dict[str, object] = {}
    for name, p in params_spec.items():
        value = base.get(name)
        if value is None:
            params.update({name: sample_random({name: p}, rng)[name]})
            continue
        if p["type"] == "cat":
            params[name] = value if rng.random() < 0.7 else rng.choice(p["choices"])
        elif p.get("log"):
            candidate = float(value) * math.exp(rng.gauss(0.0, 0.3))
            params[name] = min(max(candidate, p["low"]), p["high"])
        else:
            sigma = 0.15 * (p["high"] - p["low"])
            candidate = float(value) + rng.gauss(0.0, sigma)
            candidate = min(max(candidate, p["low"]), p["high"])
            params[name] = int(round(candidate)) if p["type"] == "int" else candidate
    return params


class BuiltinEngine:
    """Random search + hill climb around the incumbent. Resume-safe via JSONL."""

    def __init__(self, spec: dict, direction: str, state_path: Path, seed: int | None = None):
        self.params_spec = spec["params"]
        self.direction = direction
        self.state_path = state_path
        self.rng = random.Random(seed)
        self.history: list[dict] = []
        self.next_number = 0
        if state_path.exists():
            for line in state_path.read_text(encoding="utf-8").splitlines():
                try:
                    self.history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.next_number = max((h["number"] for h in self.history), default=-1) + 1

    def better(self, a: float, b: float) -> bool:
        return a < b if self.direction == "min" else a > b

    def best(self) -> dict | None:
        scored = [h for h in self.history if h.get("score") is not None]
        if not scored:
            return None
        return min(scored, key=lambda h: h["score"]) if self.direction == "min" else max(scored, key=lambda h: h["score"])

    def ask(self) -> tuple[int, dict]:
        number = self.next_number
        self.next_number += 1
        best = self.best()
        if best is None or len(self.history) < 8 or self.rng.random() < 0.4:
            params = sample_random(self.params_spec, self.rng)
        else:
            params = perturb(self.params_spec, best["params"], self.rng)
        return number, params

    def tell(self, ref: int, params: dict, score: float | None) -> None:
        record = {"number": ref, "params": params, "score": score}
        self.history.append(record)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class OptunaEngine:
    def __init__(self, spec: dict, direction: str, journal_path: Path, study_name: str):
        import optuna

        self.optuna = optuna
        self.params_spec = spec["params"]
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        storage = self._make_storage(journal_path)
        self.study = optuna.create_study(
            study_name=study_name,
            storage=storage,
            direction="minimize" if direction == "min" else "maximize",
            load_if_exists=True,
        )

    def _make_storage(self, path: Path):
        from optuna.storages import JournalStorage

        try:
            from optuna.storages.journal import JournalFileBackend
        except ImportError:  # optuna < 4
            from optuna.storages import JournalFileStorage as JournalFileBackend
        return JournalStorage(JournalFileBackend(str(path)))

    def best(self) -> dict | None:
        try:
            trial = self.study.best_trial
        except ValueError:
            return None
        return {"number": trial.number, "params": trial.params, "score": trial.value}

    def ask(self) -> tuple[object, dict]:
        trial = self.study.ask()
        params: dict[str, object] = {}
        for name, p in self.params_spec.items():
            if p["type"] == "cat":
                params[name] = trial.suggest_categorical(name, p["choices"])
            elif p["type"] == "int":
                params[name] = trial.suggest_int(name, int(p["low"]), int(p["high"]), log=bool(p.get("log")))
            else:
                params[name] = trial.suggest_float(name, p["low"], p["high"], log=bool(p.get("log")))
        return trial, params

    def tell(self, ref: object, params: dict, score: float | None) -> None:
        if score is None:
            self.study.tell(ref, state=self.optuna.trial.TrialState.FAIL)
        else:
            self.study.tell(ref, score)


def make_engine(spec: dict, args) -> tuple[object, str]:
    state_dir = args.root / "state" / "search"
    if args.engine in ("auto", "optuna"):
        try:
            engine = OptunaEngine(spec, args.direction, state_dir / f"{spec['name']}.journal.log", spec["name"])
            return engine, "optuna"
        except ImportError:
            if args.engine == "optuna":
                raise SystemExit("optuna is not importable; install it (docs/offline_optuna.md) or use --engine builtin")
    return BuiltinEngine(spec, args.direction, state_dir / f"{spec['name']}.history.jsonl", args.seed), "builtin"


# ---------------------------------------------------------------------------
# cluster interaction


def trial_job_ids(spec: dict, trial_tag: str) -> list[str]:
    return [f"{trial_tag}-i{int(seed):04d}" for seed in spec["instances"]]


def emit_trial_jobs(root: Path, spec: dict, trial_tag: str, params: dict) -> list[str]:
    pending = root / "jobs" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    job_ids = []
    for seed, job_id in zip(spec["instances"], trial_job_ids(spec, trial_tag)):
        job_ids.append(job_id)
        if find_result(root, job_id) is not None:
            continue  # resume: 結果が既にあるジョブは再投入しない
        job = {
            "job_id": job_id,
            "seed": int(seed),
            "timeout_sec": spec["timeout_sec"],
            "command": spec["command"],
            "params": params,
            "sweep_id": trial_tag,
            "created_at": now_iso(),
        }
        for key in ("cwd", "env", "artifacts"):
            if spec.get(key):
                job[key] = spec[key]
        atomic_write_json(pending / f"{job_id}.json", job)
    return job_ids


def find_result(root: Path, job_id: str) -> dict | None:
    for path in (root / "results").glob(f"*/{job_id}/result.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def collect_scores(root: Path, job_ids: list[str]) -> tuple[list[float], int, int]:
    scores: list[float] = []
    failed = 0
    missing = 0
    for job_id in job_ids:
        result = find_result(root, job_id)
        if result is None:
            missing += 1
            continue
        measure = result.get("measure") or {}
        score = measure.get("score")
        if result.get("outcome") == "completed" and measure.get("correct") is not False and score is not None:
            scores.append(float(score))
        else:
            failed += 1
    return scores, failed, missing


def aggregate(scores: list[float], agg: str) -> float:
    if agg == "min":
        return min(scores)
    if agg == "max":
        return max(scores)
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# main loop


def write_bridge_status(root: Path, spec_name: str, engine_name: str, done: int, in_flight: int, best: dict | None) -> None:
    atomic_write_json(
        root / "status" / f"bridge-{spec_name}.json",
        {
            "worker": f"bridge-{spec_name}",
            "status": "running",
            "current_job": None,
            "message": f"engine={engine_name} trials_done={done} in_flight={in_flight} best={best['score'] if best else None}",
            "updated_at": now_iso(),
        },
    )


def run_search(root: Path, spec: dict, engine, engine_name: str, args) -> dict | None:
    in_flight: dict[str, dict] = {}
    done = 0
    stop = root / "control" / "stop_all"

    while done < args.max_trials or in_flight:
        if stop.exists():
            print("stop_all detected; finishing")
            break

        while len(in_flight) < args.parallel and done + len(in_flight) < args.max_trials:
            ref, params = engine.ask()
            number = ref.number if hasattr(ref, "number") else ref
            tag = f"{spec['name']}-t{int(number):04d}"
            job_ids = emit_trial_jobs(root, spec, tag, params)
            in_flight[tag] = {
                "ref": ref,
                "params": params,
                "job_ids": job_ids,
                "deadline": time.time() + args.trial_timeout_sec,
            }

        for tag in list(in_flight):
            state = in_flight[tag]
            scores, failed, missing = collect_scores(root, state["job_ids"])
            finished = missing == 0
            expired = time.time() > state["deadline"]
            if not finished and not failed and not expired:
                continue
            if failed or expired or len(scores) < len(state["job_ids"]):
                value = None
            else:
                value = aggregate(scores, args.agg)
            engine.tell(state["ref"], state["params"], value)
            done += 1
            best = engine.best()
            print(
                f"[{now_iso()}] trial {tag}: score={value} "
                f"(ok={len(scores)} failed={failed} missing={missing})"
                + (f" best={best['score']:.6g}" if best and best.get("score") is not None else "")
            )
            del in_flight[tag]

        best = engine.best()
        write_bridge_status(root, spec["name"], engine_name, done, len(in_flight), best)
        if best is not None:
            atomic_write_json(
                root / "state" / "search" / f"{spec['name']}.best.json",
                {
                    "engine": engine_name,
                    "agg": args.agg,
                    "direction": args.direction,
                    "trial_number": best["number"],
                    "params": best["params"],
                    "score": best["score"],
                    "updated_at": now_iso(),
                },
            )
        if done < args.max_trials or in_flight:
            time.sleep(args.poll_sec)

    return engine.best()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run parameter search over the cluster.")
    parser.add_argument("--spec", type=Path, required=True, help="search space spec JSON")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--max-trials", type=int, default=100)
    parser.add_argument("--parallel", type=int, default=32, help="max in-flight trials (TPE degrades beyond ~32)")
    parser.add_argument("--agg", choices=["mean", "min", "max"], default="mean")
    parser.add_argument("--direction", choices=["max", "min"], default="max")
    parser.add_argument("--engine", choices=["auto", "optuna", "builtin"], default="auto")
    parser.add_argument("--poll-sec", type=float, default=5.0)
    parser.add_argument("--trial-timeout-sec", type=float, default=None, help="default: job timeout*3 + 300")
    parser.add_argument("--seed", type=int, default=None, help="rng seed for builtin engine")
    args = parser.parse_args(argv)

    args.root = args.root.resolve()
    spec = load_spec(args.spec)
    if args.trial_timeout_sec is None:
        args.trial_timeout_sec = float(spec["timeout_sec"]) * 3 + 300

    engine, engine_name = make_engine(spec, args)
    print(f"search '{spec['name']}': engine={engine_name} params={list(spec['params'])} instances={len(spec['instances'])}")

    best = run_search(args.root, spec, engine, engine_name, args)
    if best is None:
        print("no successful trial")
        return 1
    print(f"best: score={best['score']} params={json.dumps(best['params'], ensure_ascii=False, sort_keys=True)}")
    print(f"details: state/search/{spec['name']}.best.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
