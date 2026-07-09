#!/usr/bin/env python3
"""Parameter search driver for the shared-folder cluster (runs on the parent PC).

Asks a sampler for parameter sets, writes them as pending jobs (one job per
instance seed), collects result.json scores, and tells the sampler. Uses
Optuna (TPE + JournalStorage) when importable, otherwise falls back to a
stdlib-only random + hill-climb sampler with the same CLI.
"""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import json
import math
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


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
        prior = p.get("prior")
        if prior is not None:
            if "center" not in prior:
                raise SystemExit(f"param {name}: prior requires 'center'")
            conf = prior.get("confidence", 0.5)
            if not (0 < conf <= 1):
                raise SystemExit(f"param {name}: prior confidence must be in (0, 1]")
            if kind == "cat" and prior["center"] not in p["choices"]:
                raise SystemExit(f"param {name}: prior center must be one of choices")
            if kind != "cat" and not (p["low"] <= prior["center"] <= p["high"]):
                raise SystemExit(f"param {name}: prior center must be within [low, high]")
    if not spec.get("command"):
        raise SystemExit("spec must define 'command'")
    if not isinstance(spec.get("instances"), list) or not spec["instances"]:
        raise SystemExit("spec must define non-empty 'instances' (list of seeds)")
    spec.setdefault("name", path.stem)
    spec.setdefault("timeout_sec", 60)
    return spec


# ---------------------------------------------------------------------------
# builtin fallback engine (stdlib only)


def clamp_numeric(value: float, p: dict) -> object:
    value = min(max(value, p["low"]), p["high"])
    if p["type"] == "int":
        return int(min(max(round(value), int(p["low"])), int(p["high"])))
    return value


def sample_random(params_spec: dict, rng: random.Random) -> dict:
    params: dict[str, object] = {}
    for name, p in params_spec.items():
        if p["type"] == "cat":
            params[name] = rng.choice(p["choices"])
        elif p.get("log"):
            params[name] = clamp_numeric(math.exp(rng.uniform(math.log(p["low"]), math.log(p["high"]))), p)
        elif p["type"] == "int":
            params[name] = rng.randint(int(p["low"]), int(p["high"]))
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
            params[name] = clamp_numeric(float(value) * math.exp(rng.gauss(0.0, 0.3)), p)
        else:
            sigma = 0.15 * (p["high"] - p["low"])
            params[name] = clamp_numeric(float(value) + rng.gauss(0.0, sigma), p)
    return params


# ---------------------------------------------------------------------------
# user-belief prior (πBO: Hvarfner et al. ICLR 2022 / PriorBand: Mallik et al. NeurIPS 2023)


def spec_has_prior(spec: dict) -> bool:
    return any(p.get("prior") for p in spec["params"].values())


def sample_from_prior(params_spec: dict, rng: random.Random) -> dict:
    """「専門家の勘」を中心としたガウス分布(catは confidence 混合)からサンプルする。

    confidence が高いほど分布が center 周辺に集中する:
    数値は sigma = (1-confidence) * 探索範囲(log指定ならlog空間)。
    """
    params: dict[str, object] = {}
    for name, p in params_spec.items():
        prior = p.get("prior")
        if not prior:
            params[name] = sample_random({name: p}, rng)[name]
            continue
        conf = float(prior.get("confidence", 0.5))
        center = prior["center"]
        if p["type"] == "cat":
            params[name] = center if rng.random() < conf else rng.choice(p["choices"])
        elif p.get("log"):
            sigma = (1.0 - conf) * (math.log(p["high"]) - math.log(p["low"]))
            params[name] = clamp_numeric(math.exp(rng.gauss(math.log(center), sigma)), p)
        else:
            sigma = (1.0 - conf) * (p["high"] - p["low"])
            params[name] = clamp_numeric(rng.gauss(float(center), sigma), p)
    return params


def maybe_enqueue_prior(engine, spec: dict, trial_index: int, args, rng: random.Random) -> bool:
    """πBO と同様に試行が進むほど事前分布の影響を β/(β+t) で減衰させる。"""
    if not getattr(args, "use_prior", False):
        return False
    p_t = args.prior_p0 * args.prior_beta / (args.prior_beta + trial_index)
    if rng.random() >= p_t:
        return False
    engine.enqueue(sample_from_prior(spec["params"], rng))
    return True


def normalize_warm_start_params(params: dict, params_spec: dict) -> Optional[dict]:
    normalized = {}
    for name, p in params_spec.items():
        if name not in params:
            return None
        value = params[name]
        if p["type"] == "cat":
            if value not in p["choices"]:
                return None
            normalized[name] = value
            continue
        try:
            normalized[name] = clamp_numeric(float(value), p)
        except (TypeError, ValueError):
            return None
    return normalized


def warm_start_key(params: dict) -> str:
    return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signed_rank_p_worse(diffs):
    """One-sided Pratt Wilcoxon signed-rank p-value for challenger < incumbent.

    diffs are challenger_score - incumbent_score. Small p means the challenger is
    significantly worse. Standard library only (works with the builtin engine).
    """
    vals = [float(d) for d in diffs if math.isfinite(float(d))]
    if not vals:
        return 1.0

    ordered = sorted(enumerate(vals), key=lambda item: abs(item[1]))
    ranks = [0.0] * len(vals)
    tie_groups = []
    i = 0
    while i < len(ordered):
        j = i + 1
        a = abs(ordered[i][1])
        while j < len(ordered) and abs(ordered[j][1]) == a:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[ordered[k][0]] = rank
        tie_groups.append(j - i)
        i = j

    nonzero = [(d, ranks[idx]) for idx, d in enumerate(vals) if d != 0.0]
    m = len(nonzero)
    if m == 0:
        return 1.0

    w_plus = sum(rank for d, rank in nonzero if d > 0.0)
    if m <= 25:
        rank2 = [int(round(rank * 2.0)) for _, rank in nonzero]
        obs = int(round(w_plus * 2.0))
        counts = {0: 1}
        for r in rank2:
            nxt = {}
            for s, c in counts.items():
                nxt[s] = nxt.get(s, 0) + c
                sr = s + r
                nxt[sr] = nxt.get(sr, 0) + c
            counts = nxt
        le = sum(c for s, c in counts.items() if s <= obs)
        return le / float(2 ** m)

    var = m * (m + 1) * (2 * m + 1) / 24.0
    var -= sum((t ** 3 - t) / 48.0 for t in tie_groups)
    if var <= 0.0:
        return 1.0
    z = (w_plus - m * (m + 1) / 4.0 + 0.5) / math.sqrt(var)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def load_warm_start_candidates(path: Path, direction: str, top_k: int) -> List[dict]:
    if not path.exists():
        raise SystemExit(f"warm-start source not found: {path}")
    try:
        if path.suffix == ".jsonl":
            rows = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("score") is None:
                    continue
                if isinstance(row.get("params"), dict):
                    rows.append(row)
            reverse = direction == "max"
            rows.sort(key=lambda row: row["score"], reverse=reverse)
            return [row["params"] for row in rows]
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"failed to read warm-start source {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"warm-start source must contain an object: {path}")
    params = data.get("params", data)
    if not isinstance(params, dict):
        raise SystemExit(f"warm-start source has no params object: {path}")
    return [params]


def enqueue_warm_starts(engine, spec: dict, paths: List[Path], top_k: int, direction: str) -> int:
    queued = 0
    seen = set()
    for path in paths:
        for params in load_warm_start_candidates(path, direction, top_k):
            normalized = normalize_warm_start_params(params, spec["params"])
            if normalized is None:
                continue
            key = warm_start_key(normalized)
            if key in seen:
                continue
            engine.enqueue(normalized)
            seen.add(key)
            queued += 1
            if queued >= top_k:
                return queued
    return queued


class BuiltinEngine:
    """Random search + hill climb around the incumbent. Resume-safe via JSONL."""

    def __init__(self, spec: dict, direction: str, state_path: Path, seed: int | None = None):
        self.params_spec = spec["params"]
        self.direction = direction
        self.state_path = state_path
        self.rng = random.Random(seed)
        self.history: list[dict] = []
        self.queue: list[dict] = []
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

    def enqueue(self, params: dict) -> None:
        self.queue.append(params)

    def ask(self) -> tuple[int, dict]:
        number = self.next_number
        self.next_number += 1
        if self.queue:
            return number, self.queue.pop(0)
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


def make_tpe_sampler(optuna, profile: str, seed: int | None = None):
    """TPE sampler for the bridge.

    profile="recommended": multivariate=True (Watanabe, arXiv:2304.11127 の
    アブレーションで推奨された多変量カーネル化) + constant_liar=True
    (本ブリッジは常に複数trialをin-flightにするため、並列ask/tellでの
    重複提案を避ける。Optuna公式が並列時に推奨)。
    profile="default": 従来どおり TPESampler() の既定値。
    """
    if profile == "default":
        return optuna.samplers.TPESampler(seed=seed)
    return optuna.samplers.TPESampler(multivariate=True, constant_liar=True, seed=seed)


class OptunaEngine:
    def __init__(self, spec: dict, direction: str, journal_path: Path, study_name: str,
                 tpe_profile: str = "recommended", seed: int | None = None):
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
            sampler=make_tpe_sampler(optuna, tpe_profile, seed),
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

    def enqueue(self, params: dict) -> None:
        self.study.enqueue_trial(params)

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
            engine = OptunaEngine(spec, args.direction, state_dir / f"{spec['name']}.journal.log", spec["name"],
                                  tpe_profile=args.tpe_profile, seed=args.seed)
            return engine, "optuna"
        except ImportError:
            if args.engine == "optuna":
                raise SystemExit("optuna is not importable; install it (docs/offline_optuna.md) or use --engine builtin")
    return BuiltinEngine(spec, args.direction, state_dir / f"{spec['name']}.history.jsonl", args.seed), "builtin"


# ---------------------------------------------------------------------------
# cluster interaction


def trial_job_ids(spec: dict, trial_tag: str, seeds: list[int] | None = None) -> list[str]:
    selected = spec["instances"] if seeds is None else seeds
    return [f"{trial_tag}-i{int(seed):04d}" for seed in selected]


def make_job_payload(spec: dict, trial_tag: str, params: dict, seed: int, job_id: str) -> dict:
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
    return job


def emit_trial_jobs(root: Path, spec: dict, trial_tag: str, params: dict, seeds: list[int] | None = None) -> list[str]:
    pending = root / "jobs" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    job_ids = []
    selected = spec["instances"] if seeds is None else seeds
    for seed, job_id in zip(selected, trial_job_ids(spec, trial_tag, selected)):
        job_ids.append(job_id)
        if find_result(root, job_id) is not None:
            continue  # resume: 結果が既にあるジョブは再投入しない
        atomic_write_json(pending / f"{job_id}.json", make_job_payload(spec, trial_tag, params, int(seed), job_id))
    return job_ids


def emit_hedge_job(root: Path, spec: dict, trial_tag: str, params: dict, seed: int, original_job_id: str) -> str:
    hedge_id = original_job_id + "-h1"
    if find_result(root, hedge_id) is None:
        pending = root / "jobs" / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        atomic_write_json(pending / f"{hedge_id}.json", make_job_payload(spec, trial_tag, params, int(seed), hedge_id))
    return hedge_id


def find_result(root: Path, job_id: str) -> dict | None:
    for path in (root / "results").glob(f"*/{job_id}/result.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


class ResultIndex:
    def __init__(self, root: Path):
        self.root = root
        self.cache = {}
        self.seen_done = set()
        self.seen_failed = set()

    def _match_job_id(self, stem: str, wanted: set) -> Optional[str]:
        if stem in wanted:
            return stem
        head, sep, tail = stem.rpartition("-")
        if sep and tail.isdigit() and head in wanted:
            return head
        return None

    def poll(self, wanted: set) -> None:
        for bucket, seen in (("done", self.seen_done), ("failed", self.seen_failed)):
            directory = self.root / "jobs" / bucket
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not entry.name.endswith(".json") or entry.name in seen:
                            continue
                        job_id = self._match_job_id(entry.name[:-5], wanted)
                        if job_id is None:
                            continue
                        if job_id not in self.cache:
                            result = find_result(self.root, job_id)
                            if result is None:
                                continue
                            self.cache[job_id] = result
                        seen.add(entry.name)
            except FileNotFoundError:
                continue

    def get(self, job_id: str) -> Optional[dict]:
        return self.cache.get(job_id)


def resolve_result(root: Path, job_id: str, index=None, aliases: dict[str, list[str]] | None = None) -> Optional[dict]:
    result = index.get(job_id) if index is not None else find_result(root, job_id)
    if result is not None:
        return result
    for alias in (aliases or {}).get(job_id, []):
        result = index.get(alias) if index is not None else find_result(root, alias)
        if result is not None:
            return result
    return None


def collect_scores(root: Path, job_ids: list[str], index=None,
                   aliases: dict[str, list[str]] | None = None) -> tuple[list[float], int, int]:
    scores: list[float] = []
    failed = 0
    missing = 0
    for job_id in job_ids:
        result = resolve_result(root, job_id, index, aliases)
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


def collect_seed_scores(root: Path, job_ids: list[str], seed_by_job: dict[str, int], index=None,
                        aliases: dict[str, list[str]] | None = None) -> tuple[dict[int, float], int, int]:
    scores: dict[int, float] = {}
    failed = 0
    missing = 0
    for job_id in job_ids:
        result = resolve_result(root, job_id, index, aliases)
        if result is None:
            missing += 1
            continue
        measure = result.get("measure") or {}
        score = measure.get("score")
        if result.get("outcome") == "completed" and measure.get("correct") is not False and score is not None:
            scores[seed_by_job[job_id]] = float(score)
        else:
            failed += 1
    return scores, failed, missing


def aggregate(scores: list[float], agg: str) -> float:
    if agg == "min":
        return min(scores)
    if agg == "max":
        return max(scores)
    return sum(scores) / len(scores)


def is_better(value: float, incumbent: float, direction: str) -> bool:
    return value < incumbent if direction == "min" else value > incumbent


def is_worse(value: float, incumbent: float, direction: str) -> bool:
    return value > incumbent if direction == "min" else value < incumbent


def deterministic_race_order(instances: list[int], tag: str) -> list[int]:
    order = [int(seed) for seed in instances]
    random.Random("race:" + tag).shuffle(order)
    return order


def median(values: list[float]) -> float:
    xs = sorted(values)
    mid = len(xs) // 2
    if len(xs) % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def should_hedge_job(elapsed: float, samples: list[float], factor: float, min_wait_sec: float) -> bool:
    if len(samples) < 3:
        return False
    return elapsed > factor * median(samples) + min_wait_sec


def hedge_missing_jobs(root: Path, spec: dict, tag: str, state: dict, args, now: float, hedge_stats: dict) -> None:
    if not getattr(args, "hedge", False) or state.get("told"):
        return
    if hedge_stats.get("disabled"):
        return
    submitted = state["submitted_job_ids"]
    if not submitted:
        return
    missing = [job_id for job_id in submitted if job_id not in state["resolved_jobs"]]
    if not missing:
        return
    if len(missing) > math.ceil(float(args.hedge_remaining_frac) * len(submitted)):
        return
    if len(state["durations"]) < 3:
        return
    if hedge_stats["duplicates"] / max(1, hedge_stats["submitted"]) > float(args.hedge_max_frac):
        hedge_stats["disabled"] = True
        if not hedge_stats.get("notified"):
            print(f"hedge disabled: duplicate ratio exceeded {args.hedge_max_frac:g}")
            hedge_stats["notified"] = True
        return

    for job_id in missing:
        if job_id in state["hedged_jobs"]:
            continue
        start = state["job_started_at"].get(job_id)
        if start is None:
            continue
        if not should_hedge_job(now - start, state["durations"], float(args.hedge_factor), float(args.hedge_min_wait_sec)):
            continue
        hedge_id = emit_hedge_job(root, spec, tag, state["params"], state["seed_by_job"][job_id], job_id)
        state["aliases"].setdefault(job_id, []).append(hedge_id)
        state["alias_to_original"][hedge_id] = job_id
        state["hedged_jobs"].add(job_id)
        state["job_started_at"][hedge_id] = now
        state["session_jobs"].add(hedge_id)
        hedge_stats["duplicates"] += 1
        hedge_stats["submitted"] += 1
        if hedge_stats["duplicates"] / max(1, hedge_stats["submitted"]) > float(args.hedge_max_frac):
            hedge_stats["disabled"] = True
            if not hedge_stats.get("notified"):
                print(f"hedge disabled: duplicate ratio exceeded {args.hedge_max_frac:g}")
                hedge_stats["notified"] = True
            break


def note_resolved_durations(state: dict, result_index: ResultIndex, now: float) -> None:
    for job_id in list(state["submitted_job_ids"]):
        if job_id in state["resolved_jobs"]:
            continue
        result = result_index.get(job_id)
        winner_id = job_id
        if result is None:
            for alias in state["aliases"].get(job_id, []):
                result = result_index.get(alias)
                if result is not None:
                    winner_id = alias
                    break
        if result is None:
            continue
        state["resolved_jobs"].add(job_id)
        start = state["job_started_at"].get(winner_id)
        if start is not None and winner_id in state["session_jobs"]:
            state["durations"].append(max(0.0, now - start))


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
    result_index = ResultIndex(root)
    prior_rng = random.Random(args.seed)
    done = 0
    stop = root / "control" / "stop_all"
    instances = [int(seed) for seed in spec["instances"]]
    race_enabled = bool(getattr(args, "race", False))
    if race_enabled and args.agg == "max":
        print("warning: --agg max is incompatible with --race; disabling racing")
        race_enabled = False
    race_min_startup = int(math.ceil(len(instances) / 3.0))
    configured_startup = int(getattr(args, "race_startup", 0) or race_min_startup)
    race_startup = min(len(instances), max(configured_startup, race_min_startup))
    incumbent: dict | None = None
    hedge_stats = {"submitted": 0, "duplicates": 0, "disabled": False, "notified": False}

    def add_submitted_jobs(state: dict, seeds: list[int], submitted_at: float) -> list[str]:
        if [int(seed) for seed in seeds] == instances:
            job_ids = emit_trial_jobs(root, spec, state["tag"], state["params"])
        else:
            job_ids = emit_trial_jobs(root, spec, state["tag"], state["params"], seeds=seeds)
        for seed, job_id in zip(seeds, job_ids):
            state["seed_by_job"][job_id] = int(seed)
            state["job_by_seed"][int(seed)] = job_id
            if job_id not in state["job_ids"]:
                state["job_ids"].append(job_id)
            if job_id not in state["submitted_job_ids"]:
                state["submitted_job_ids"].append(job_id)
            if find_result(root, job_id) is None:
                state["job_started_at"][job_id] = submitted_at
                state["session_jobs"].add(job_id)
                hedge_stats["submitted"] += 1
        return job_ids

    while done < args.max_trials or in_flight:
        if stop.exists():
            print("stop_all detected; finishing")
            break

        while len(in_flight) < args.parallel and done + len(in_flight) < args.max_trials:
            maybe_enqueue_prior(engine, spec, done + len(in_flight), args, prior_rng)
            ref, params = engine.ask()
            number = ref.number if hasattr(ref, "number") else ref
            tag = f"{spec['name']}-t{int(number):04d}"
            order = deterministic_race_order(instances, tag) if race_enabled else list(instances)
            initial_seeds = order[:race_startup] if race_enabled else list(instances)
            state = {
                "tag": tag,
                "ref": ref,
                "params": params,
                "job_ids": [],
                "submitted_job_ids": [],
                "seed_by_job": {},
                "job_by_seed": {},
                "order": order,
                "remaining_seeds": order[len(initial_seeds):] if race_enabled else [],
                "race_active": race_enabled,
                "deadline": time.time() + args.trial_timeout_sec,
                "aliases": {},
                "alias_to_original": {},
                "hedged_jobs": set(),
                "job_started_at": {},
                "session_jobs": set(),
                "resolved_jobs": set(),
                "durations": [],
                "told": False,
            }
            add_submitted_jobs(state, initial_seeds, time.time())
            in_flight[tag] = state

        now = time.time()
        wanted = set()
        for state in in_flight.values():
            wanted.update(state["submitted_job_ids"])
            wanted.update(state["alias_to_original"])
        result_index.poll(wanted)

        for state in in_flight.values():
            note_resolved_durations(state, result_index, now)

        for tag in list(in_flight):
            state = in_flight[tag]
            scores_by_seed, failed, missing = collect_seed_scores(
                root, state["submitted_job_ids"], state["seed_by_job"], result_index, state["aliases"]
            )
            finished = missing == 0
            now = time.time()

            if state["race_active"] and failed:
                engine.tell(state["ref"], state["params"], None)
                done += 1
                print(
                    f"[{now_iso()}] trial {tag}: score=None "
                    f"(ok={len(scores_by_seed)} failed={failed} missing={missing})"
                )
                state["told"] = True
                del in_flight[tag]
                continue

            if (
                state["race_active"]
                and state["remaining_seeds"]
                and incumbent is not None
                and len(scores_by_seed) >= race_startup
            ):
                diffs = [scores_by_seed[s] - incumbent["scores"][s] for s in scores_by_seed if s in incumbent["scores"]]
                if args.direction == "min":
                    diffs = [-d for d in diffs]
                partial_scores = list(scores_by_seed.values())
                partial_value = aggregate(partial_scores, args.agg) if partial_scores else None
                if partial_value is not None:
                    p_value = signed_rank_p_worse(diffs)
                    if p_value < float(getattr(args, "race_p", 0.05)) and is_worse(partial_value, incumbent["value"], args.direction):
                        engine.tell(state["ref"], state["params"], partial_value)
                        done += 1
                        best = engine.best()
                        print(
                            f"[{now_iso()}] pruned {tag} p={p_value:.6g} "
                            f"after {len(scores_by_seed)}/{len(instances)} seeds"
                        )
                        print(
                            f"[{now_iso()}] trial {tag}: score={partial_value} "
                            f"(ok={len(scores_by_seed)} failed={failed} missing={missing})"
                            + (f" best={best['score']:.6g}" if best and best.get("score") is not None else "")
                        )
                        state["told"] = True
                        del in_flight[tag]
                        continue

            if state["race_active"] and state["remaining_seeds"] and finished and not failed:
                add_submitted_jobs(state, list(state["remaining_seeds"]), time.time())
                state["remaining_seeds"] = []
                scores_by_seed, failed, missing = collect_seed_scores(
                    root, state["submitted_job_ids"], state["seed_by_job"], result_index, state["aliases"]
                )
                finished = missing == 0

            hedge_missing_jobs(root, spec, tag, state, args, now, hedge_stats)

            if missing and now > state["deadline"]:
                for job_id in state["submitted_job_ids"]:
                    if resolve_result(root, job_id, result_index, state["aliases"]) is None:
                        result = resolve_result(root, job_id, None, state["aliases"])
                        if result is not None:
                            result_index.cache[job_id] = result
                scores_by_seed, failed, missing = collect_seed_scores(
                    root, state["submitted_job_ids"], state["seed_by_job"], result_index, state["aliases"]
                )
                finished = missing == 0
            expired = now > state["deadline"]
            if not finished and not failed and not expired:
                continue
            scores = list(scores_by_seed.values())
            if failed or expired or len(scores) < len(state["job_ids"]):
                value = None
            else:
                value = aggregate(scores, args.agg)
            engine.tell(state["ref"], state["params"], value)
            if value is not None and len(scores_by_seed) == len(instances):
                if incumbent is None or is_better(value, incumbent["value"], args.direction):
                    incumbent = {"value": value, "scores": dict(scores_by_seed)}
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
    parser.add_argument("--seed", type=int, default=None, help="rng seed for the sampler")
    parser.add_argument("--tpe-profile", choices=["recommended", "default"], default="recommended",
                        help="TPE設定: recommended=multivariate+constant_liar (arXiv:2304.11127), default=Optuna既定値")
    parser.add_argument("--no-prior", action="store_true", help="specの prior 指定を無視する")
    parser.add_argument("--prior-p0", type=float, default=0.9, help="初回trialで事前分布から引く確率")
    parser.add_argument("--prior-beta", type=float, default=None, help="減衰スケール (default: max_trials/4)")
    parser.add_argument("--warm-start-from", type=Path, action="append", default=[],
                        help="過去の state/search/*.best.json または *.history.jsonl から初期候補をenqueue")
    parser.add_argument("--warm-start-top-k", type=int, default=5,
                        help="history.jsonl から読み込む上位候補数")
    parser.add_argument("--race", action="store_true", help="Wilcoxonゲート付きシードレーシングを有効化する")
    parser.add_argument("--race-p", type=float, default=0.05, help="racingの片側Wilcoxon p値しきい値")
    parser.add_argument("--race-startup", type=int, default=0, help="racingの初期評価seed数(0=ceil(n/3))")
    parser.add_argument("--hedge", action="store_true", help="残り少数の遅いジョブに複製を投入する")
    parser.add_argument("--hedge-factor", type=float, default=2.0, help="hedgeしきい値のmedian倍率")
    parser.add_argument("--hedge-min-wait-sec", type=float, default=30.0, help="hedgeしきい値に足す最小待機秒")
    parser.add_argument("--hedge-remaining-frac", type=float, default=0.2, help="hedge対象にする未着ジョブ比率")
    parser.add_argument("--hedge-max-frac", type=float, default=0.10, help="複製数/投入数がこの比率を超えたら停止")
    args = parser.parse_args(argv)

    args.root = args.root.resolve()
    spec = load_spec(args.spec)
    if args.trial_timeout_sec is None:
        args.trial_timeout_sec = float(spec["timeout_sec"]) * 3 + 300

    if args.prior_beta is None:
        args.prior_beta = max(1.0, args.max_trials / 4)
    args.use_prior = spec_has_prior(spec) and not args.no_prior

    engine, engine_name = make_engine(spec, args)
    if args.warm_start_top_k < 1:
        raise SystemExit("--warm-start-top-k must be >= 1")
    if args.warm_start_from:
        queued = enqueue_warm_starts(engine, spec, args.warm_start_from, args.warm_start_top_k, args.direction)
        print(f"warm-start: {queued}件 enqueue した")
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
