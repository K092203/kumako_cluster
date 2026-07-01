"""Anytime config sweep with incremental results and live incumbent (§5.2, §10).

For each config we run ``rep`` repetitions, aggregate them, append a row to
results.csv immediately (so a killed sweep keeps what it measured), and update
the incumbent on the spot. Before each config we check the remaining time
budget and break early ("anytime") rather than starting work we cannot finish.
"""

from __future__ import annotations

import csv
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from runledger import config as C
from runledger import incumbent as inc
from runledger import run as run_mod
from runledger import snapshot
from runledger.incumbent import Candidate
from runledger.measure import MeasureSpec
from runledger.util import median

RESULTS_HEADER = ["id", "elapsed", "correct", "score", "exit_code", "rep_done", "run_id", "notes"]
CONFIG_COLUMNS = ["id", "target", "bin", "ranks", "omp", "rep", "args"]
ANYTIME_MARGIN_SEC = 2.0


@dataclass
class SweepConfig:
    id: str
    target: str
    bin: str
    ranks: int
    omp: int
    rep: int
    args: str


@dataclass
class ResultRow:
    id: str
    elapsed: float
    correct: bool
    score: float
    exit_code: int
    rep_done: int
    run_id: str
    notes: str

    def as_csv(self) -> list[str]:
        return [
            self.id,
            f"{self.elapsed:.6f}",
            "1" if self.correct else "0",
            f"{self.score:.6f}",
            str(self.exit_code),
            str(self.rep_done),
            self.run_id,
            self.notes,
        ]


@dataclass
class SweepResult:
    sweep_dir: Path
    rows: list[ResultRow] = field(default_factory=list)
    configs_total: int = 0
    configs_run: int = 0
    incumbent_updates: int = 0
    stopped_early: bool = False


def _to_pos_int(value: str, default: int = 1) -> int:
    try:
        n = int(value)
        return n if n >= 1 else default
    except (TypeError, ValueError):
        return default


def read_configs(path: Path) -> tuple[list[SweepConfig], list[str]]:
    """Parse a tab-separated configs.tsv. Returns (configs, warnings)."""
    configs: list[SweepConfig] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()

    text = path.read_text(encoding="utf-8")
    reader = csv.reader(text.splitlines(), delimiter="\t")
    for lineno, row in enumerate(reader, start=1):
        if not row or not row[0].strip() or row[0].startswith("#"):
            continue
        if row[0].strip() == "id":  # header
            continue
        if len(row) < 7:
            warnings.append(f"line {lineno}: malformed config (need 7 columns), skipped")
            continue
        cid = row[0].strip()
        if cid in seen_ids:
            warnings.append(f"duplicate id '{cid}' skipped")
            continue
        seen_ids.add(cid)
        configs.append(
            SweepConfig(
                id=cid,
                target=row[1].strip(),
                bin=row[2].strip(),
                ranks=_to_pos_int(row[3].strip()),
                omp=_to_pos_int(row[4].strip()),
                rep=_to_pos_int(row[5].strip()),
                args=row[6].strip(),
            )
        )
    return configs, warnings


def build_command(
    cfg: SweepConfig,
    *,
    base_command: list[str] | None,
    launcher: str | None,
    bindir: str | None,
    bin_template: str | None,
) -> list[str]:
    parts: list[str] = []
    if launcher:
        parts += launcher.format(ranks=cfg.ranks, omp=cfg.omp, bin=cfg.bin).split()
    if bin_template:
        parts.append(bin_template.format(bin=cfg.bin, ranks=cfg.ranks))
    elif base_command:
        parts += list(base_command)
    elif bindir:
        parts.append(str(Path(bindir) / cfg.bin))
    else:
        parts.append(cfg.bin)
    if cfg.args:
        parts += shlex.split(cfg.args)
    return parts


def _args_tags(args: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    tokens = shlex.split(args)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key = tok[2:]
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                tags[key] = tokens[i + 1]
                i += 2
                continue
            tags[key] = "true"
        i += 1
    return tags


def aggregate(
    cfg: SweepConfig,
    records: list[run_mod.RunRecord],
    *,
    penalty_elapsed: float,
) -> ResultRow:
    rep_done = len(records)
    elapsed_vals = [r.measure.elapsed for r in records if r.measure.elapsed is not None]
    all_completed = rep_done > 0 and all(
        r.outcome.outcome == C.OUTCOME_COMPLETED for r in records
    )
    all_correct = rep_done > 0 and all(bool(r.measure.correct) for r in records)
    correct = all_completed and all_correct and bool(elapsed_vals)

    if elapsed_vals:
        med = median(elapsed_vals)
        rep_rec = min(
            (r for r in records if r.measure.elapsed is not None),
            key=lambda r: abs((r.measure.elapsed or 0.0) - med),
        )
        score = rep_rec.measure.score if rep_rec.measure.score is not None else 0.0
        notes = "ok" if correct else "incorrect"
        return ResultRow(
            id=cfg.id,
            elapsed=med,
            correct=correct,
            score=score,
            exit_code=rep_rec.outcome.exit_code,
            rep_done=rep_done,
            run_id=rep_rec.run_id,
            notes=notes,
        )

    # No trusted measurement: finite penalty row.
    last = records[-1] if records else None
    notes = last.outcome.outcome if last else "no-run"
    return ResultRow(
        id=cfg.id,
        elapsed=penalty_elapsed,
        correct=False,
        score=0.0,
        exit_code=last.outcome.exit_code if last else C.EXIT_TIMEOUT,
        rep_done=rep_done,
        run_id=last.run_id if last else "",
        notes=notes,
    )


def _next_round_name(sweep_root: Path) -> str:
    nums: list[int] = []
    if sweep_root.exists():
        for p in sweep_root.glob("round-*"):
            tail = p.name.split("-", 1)[1]
            if tail.isdigit():
                nums.append(int(tail))
    nxt = (max(nums) + 1) if nums else 1
    return f"round-{nxt:03d}"


def run_sweep(
    configs_path: Path,
    *,
    base_command: list[str] | None = None,
    sweep_root: Path,
    state_dir: Path,
    budget: float,
    elapse: float | None = None,
    timeout: float | None = None,
    objective: str = "max-score",
    measure_spec: MeasureSpec | None = None,
    launcher: str | None = None,
    bindir: str | None = None,
    bin_template: str | None = None,
    cwd: Path | None = None,
    sweep_name: str | None = None,
) -> SweepResult:
    cwd = (cwd or Path.cwd()).resolve()
    per_run_timeout = timeout if timeout is not None else budget
    penalty_elapsed = float(per_run_timeout or budget)

    sweep_dir = sweep_root / (sweep_name or _next_round_name(sweep_root))
    runs_dir = sweep_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    configs, warnings = read_configs(configs_path)
    (sweep_dir / "configs.tsv").write_text(
        configs_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    errors_log = sweep_dir / "errors.log"
    for w in warnings:
        _append_line(errors_log, w)

    results_path = sweep_dir / "results.csv"
    with results_path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(RESULTS_HEADER)

    result = SweepResult(sweep_dir=sweep_dir, configs_total=len(configs))
    start = time.monotonic()

    for cfg in configs:
        if elapse is not None:
            spent = time.monotonic() - start
            estimate = budget * cfg.rep
            if spent + estimate + ANYTIME_MARGIN_SEC > elapse:
                _append_line(
                    errors_log,
                    f"anytime cutoff before id {cfg.id}: "
                    f"spent={spent:.1f}s est={estimate:.1f}s elapse={elapse:.1f}s",
                )
                result.stopped_early = True
                break

        command = build_command(
            cfg,
            base_command=base_command,
            launcher=launcher,
            bindir=bindir,
            bin_template=bin_template,
        )
        tags = _args_tags(cfg.args)

        records: list[run_mod.RunRecord] = []
        for r in range(1, cfg.rep + 1):
            if elapse is not None and time.monotonic() - start > elapse:
                _append_line(errors_log, f"anytime cutoff mid-reps at id {cfg.id} rep {r}")
                result.stopped_early = True
                break
            rec = run_mod.run_once(
                command,
                out_root=runs_dir,
                run_id=f"{cfg.id}_rep{r:03d}",
                name=cfg.id,
                timeout=per_run_timeout,
                extra_env={"OMP_NUM_THREADS": str(cfg.omp)},
                cwd=cwd,
                measure_spec=measure_spec,
                tags=tags,
                update_latest=False,
            )
            records.append(rec)
            if rec.outcome.outcome == C.OUTCOME_MISSING_BIN:
                _append_line(errors_log, f"id {cfg.id}: missing bin for command {command}")
                break

        if not records:
            continue

        row = aggregate(cfg, records, penalty_elapsed=penalty_elapsed)
        _append_result_row(results_path, row)
        result.rows.append(row)
        result.configs_run += 1

        candidate = Candidate(
            id=cfg.id,
            target=cfg.target,
            bin=cfg.bin,
            args=cfg.args,
            elapsed=row.elapsed,
            score=row.score,
            correct=row.correct,
            source_sweep=sweep_dir.name,
            source_run=row.run_id,
        )
        if inc.update_incumbent(state_dir, candidate, objective=objective):
            result.incumbent_updates += 1

    snapshot.update_latest(sweep_root, sweep_dir.name)
    return result


def _append_result_row(results_path: Path, row: ResultRow) -> None:
    with results_path.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(row.as_csv())
        fh.flush()


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_results(sweep_dir: Path) -> list[ResultRow]:
    """Read results.csv back into ResultRow objects (for the incumbent command)."""
    path = sweep_dir / "results.csv"
    rows: list[ResultRow] = []
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    for rec in reader:
        rows.append(
            ResultRow(
                id=rec["id"],
                elapsed=float(rec["elapsed"]),
                correct=rec["correct"] == "1",
                score=float(rec["score"]),
                exit_code=int(rec["exit_code"]),
                rep_done=int(rec["rep_done"]),
                run_id=rec["run_id"],
                notes=rec["notes"],
            )
        )
    return rows
