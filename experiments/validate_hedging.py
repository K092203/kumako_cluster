#!/usr/bin/env python3
"""検証7: straggler hedging が固定wall時間での完了trial数を改善するか.

kumako_cluster の探索ブリッジは 1 trial = 15 seed job を投入し、全job完了を
待って trial 完了とする。最遅job(straggler/orphan)が律速するため、残り少数
jobに複製を投入して先着を採用する hedging を、離散事象シミュレーションで測る。

モデルは S=280 slot の FIFO M/G/c キュー。job実行時間は lognormal を基本に、
確率 p_straggler で10倍、確率 p_lost で grace 秒後に stale requeue される孤児
とする。hedging arm では trial 内12job以上完了後、未完了jobの実行時間が
2 * median(completed job runtime) + 30s を超えたら複製を最大1個投入する。
"""

from __future__ import annotations

import argparse
import collections
import heapq
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SLOTS = 280
PARALLEL = 32
JOBS_PER_TRIAL = 15
MU = math.log(30.0)
SIGMA = 0.25
P_LOST = 0.002
GRACE = 600.0
SIM_SECONDS = 8 * 60 * 60
QUICK_SECONDS = 60 * 60
REPS = 10
QUICK_REPS = 2
P_STRAGGLERS = (0.0, 0.02, 0.05)
ARMS = ("baseline", "hedging")


class Trial:
    __slots__ = ("trial_id", "submit_time", "jobs", "completed", "durations", "done")

    def __init__(self, trial_id, submit_time):
        self.trial_id = trial_id
        self.submit_time = submit_time
        self.jobs = []
        self.completed = 0
        self.durations = []
        self.done = False


class Job:
    __slots__ = ("trial", "index", "completed", "winner_attempt_id", "hedged", "attempts")

    def __init__(self, trial, index):
        self.trial = trial
        self.index = index
        self.completed = False
        self.winner_attempt_id = None
        self.hedged = False
        self.attempts = []


class Attempt:
    __slots__ = ("attempt_id", "job", "is_duplicate", "start_time", "active", "waste_counted")

    def __init__(self, attempt_id, job, is_duplicate):
        self.attempt_id = attempt_id
        self.job = job
        self.is_duplicate = is_duplicate
        self.start_time = None
        self.active = False
        self.waste_counted = False


def percentile(vals, p):
    if not vals:
        return None
    xs = sorted(vals)
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def sample_runtime(rng, p_straggler):
    runtime = rng.lognormvariate(MU, SIGMA)
    if rng.random() < p_straggler:
        runtime *= 10.0
    return runtime


def summarize(vals):
    if not vals:
        return {"mean": None, "sd": None, "min": None, "max": None}
    return {
        "mean": statistics.fmean(vals),
        "sd": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "min": min(vals),
        "max": max(vals),
    }


def simulate(arm, p_straggler, rep, condition_index, sim_seconds):
    rng = random.Random(rep * 1000 + condition_index)
    now = 0.0
    seq = 0
    next_trial_id = 0
    next_attempt_id = 0
    busy = 0
    total_slot_seconds = 0.0
    wasted_slot_seconds = 0.0
    duplicates_submitted = 0
    orphans = 0
    trials_completed = 0
    trial_latencies = []
    queue = collections.deque()
    events = []
    active_trials = {}
    hedge_candidates = set()
    active_attempts = {}

    def push_event(t, kind, payload):
        nonlocal seq
        heapq.heappush(events, (t, seq, kind, payload))
        seq += 1

    def new_attempt(job, is_duplicate):
        nonlocal next_attempt_id
        attempt = Attempt(next_attempt_id, job, is_duplicate)
        next_attempt_id += 1
        job.attempts.append(attempt)
        queue.append(attempt)
        return attempt

    def submit_trial(t):
        nonlocal next_trial_id
        trial = Trial(next_trial_id, t)
        next_trial_id += 1
        active_trials[trial.trial_id] = trial
        for i in range(JOBS_PER_TRIAL):
            job = Job(trial, i)
            trial.jobs.append(job)
            new_attempt(job, False)
        return trial

    def fill_slots(t):
        nonlocal busy, orphans
        while busy < SLOTS and queue:
            attempt = queue.popleft()
            job = attempt.job
            if job.completed or trial_done(job.trial):
                continue
            attempt.start_time = t
            attempt.active = True
            active_attempts[attempt.attempt_id] = attempt
            busy += 1
            if rng.random() < P_LOST:
                orphans += 1
                push_event(t + GRACE, "orphan_requeue", attempt)
            else:
                push_event(t + sample_runtime(rng, p_straggler), "job_finish", attempt)

    def trial_done(trial):
        return trial.done

    def count_waste(attempt, end_time):
        nonlocal wasted_slot_seconds
        if not attempt.waste_counted and attempt.start_time is not None:
            wasted_slot_seconds += max(0.0, end_time - attempt.start_time)
            attempt.waste_counted = True

    def finish_trial_if_ready(trial, t):
        nonlocal trials_completed
        if trial.done or trial.completed < JOBS_PER_TRIAL:
            return
        trial.done = True
        active_trials.pop(trial.trial_id, None)
        hedge_candidates.discard(trial.trial_id)
        trials_completed += 1
        trial_latencies.append(t - trial.submit_time)
        submit_trial(t)

    def maybe_hedge(t):
        nonlocal duplicates_submitted
        if arm != "hedging":
            return
        for trial_id in list(hedge_candidates):
            trial = active_trials.get(trial_id)
            if trial is None or trial.done or trial.completed < 12:
                continue
            threshold = 2.0 * statistics.median(trial.durations) + 30.0
            for job in trial.jobs:
                if job.completed or job.hedged:
                    continue
                starts = [a.start_time for a in job.attempts if a.active and a.start_time is not None]
                if starts and t - min(starts) > threshold:
                    job.hedged = True
                    new_attempt(job, True)
                    duplicates_submitted += 1

    for _ in range(PARALLEL):
        submit_trial(0.0)
    fill_slots(0.0)

    while events and events[0][0] <= sim_seconds:
        now, _, kind, attempt = heapq.heappop(events)
        if not attempt.active:
            continue
        job = attempt.job
        trial = job.trial
        attempt.active = False
        active_attempts.pop(attempt.attempt_id, None)
        busy -= 1
        total_slot_seconds += max(0.0, now - attempt.start_time)

        if kind == "job_finish":
            if job.completed:
                count_waste(attempt, now)
            else:
                job.completed = True
                job.winner_attempt_id = attempt.attempt_id
                trial.completed += 1
                trial.durations.append(now - attempt.start_time)
                if trial.completed >= 12 and not trial.done:
                    hedge_candidates.add(trial.trial_id)
                finish_trial_if_ready(trial, now)
        elif kind == "orphan_requeue":
            if attempt.is_duplicate or job.completed:
                count_waste(attempt, now)
            if not job.completed and not trial.done:
                new_attempt(job, attempt.is_duplicate)

        maybe_hedge(now)
        fill_slots(now)

    for attempt in active_attempts.values():
        elapsed = max(0.0, sim_seconds - attempt.start_time)
        total_slot_seconds += elapsed
        if attempt.job.completed and attempt.attempt_id != attempt.job.winner_attempt_id:
            count_waste(attempt, sim_seconds)
        elif attempt.is_duplicate and not attempt.job.completed:
            count_waste(attempt, sim_seconds)

    return {
        "arm": arm,
        "p_straggler": p_straggler,
        "rep": rep,
        "sim_seconds": sim_seconds,
        "trials_completed": trials_completed,
        "trial_latency_p50": percentile(trial_latencies, 0.50),
        "trial_latency_p95": percentile(trial_latencies, 0.95),
        "wasted_slot_seconds_pct": (wasted_slot_seconds / total_slot_seconds * 100.0) if total_slot_seconds else 0.0,
        "duplicates_submitted": duplicates_submitted,
        "orphans": orphans,
        "total_slot_seconds": total_slot_seconds,
        "wasted_slot_seconds": wasted_slot_seconds,
    }


def build_summary(rows, reps):
    out = {"conditions": {}, "paired_trials_completed": {}}
    for p in P_STRAGGLERS:
        for arm in ARMS:
            key = f"{arm}:p_straggler={p:g}"
            group = [r for r in rows if r["arm"] == arm and r["p_straggler"] == p]
            out["conditions"][key] = {
                "trials_completed": summarize([r["trials_completed"] for r in group]),
                "trial_latency_p50": summarize([r["trial_latency_p50"] for r in group]),
                "trial_latency_p95": summarize([r["trial_latency_p95"] for r in group]),
                "wasted_slot_seconds_pct": summarize([r["wasted_slot_seconds_pct"] for r in group]),
                "duplicates_submitted": summarize([r["duplicates_submitted"] for r in group]),
                "orphans": summarize([r["orphans"] for r in group]),
                "reps": reps,
            }
        base = sorted([r for r in rows if r["arm"] == "baseline" and r["p_straggler"] == p], key=lambda r: r["rep"])
        hedge = sorted([r for r in rows if r["arm"] == "hedging" and r["p_straggler"] == p], key=lambda r: r["rep"])
        diffs = [h["trials_completed"] - b["trials_completed"] for b, h in zip(base, hedge)]
        lo, hi = boot_ci(diffs)
        out["paired_trials_completed"][f"p_straggler={p:g}"] = {
            "baseline_mean": statistics.fmean([r["trials_completed"] for r in base]),
            "hedging_mean": statistics.fmean([r["trials_completed"] for r in hedge]),
            "paired_diff_mean": statistics.fmean(diffs),
            "paired_diff_ci95": [lo, hi],
            "wins": sum(x > 0 for x in diffs),
            "losses": sum(x < 0 for x in diffs),
            "reps": reps,
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="REPS=2, sim=1h の短縮実行")
    args = parser.parse_args()

    reps = QUICK_REPS if args.quick else REPS
    sim_seconds = QUICK_SECONDS if args.quick else SIM_SECONDS
    rows = []
    for p_index, p_straggler in enumerate(P_STRAGGLERS):
        for arm in ARMS:
            for rep in range(reps):
                row = simulate(arm, p_straggler, rep, p_index, sim_seconds)
                rows.append(row)
            group = [r for r in rows if r["arm"] == arm and r["p_straggler"] == p_straggler]
            line = {
                "arm": arm,
                "p_straggler": p_straggler,
                "trials_completed_mean": statistics.fmean(r["trials_completed"] for r in group),
                "latency_p50_mean": statistics.fmean(r["trial_latency_p50"] for r in group),
                "latency_p95_mean": statistics.fmean(r["trial_latency_p95"] for r in group),
                "wasted_slot_seconds_pct_mean": statistics.fmean(r["wasted_slot_seconds_pct"] for r in group),
                "duplicates_submitted_mean": statistics.fmean(r["duplicates_submitted"] for r in group),
                "orphans_mean": statistics.fmean(r["orphans"] for r in group),
                "reps": reps,
            }
            print(json.dumps(line, sort_keys=True))

    out = {"quick": args.quick, "rows": rows, "summary": build_summary(rows, reps)}
    (ROOT / "experiments" / "results_hedging.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
