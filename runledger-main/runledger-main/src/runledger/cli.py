"""runledger command-line interface (§6).

Commands: run, sweep, gen-configs, incumbent, summary.

The trailing program for ``run`` and ``sweep`` is given after a literal ``--``;
everything before ``--`` is parsed as options.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runledger import incumbent as inc
from runledger import sweep as sweep_mod
from runledger.config import load_config
from runledger.gen_configs import gen_configs_file
from runledger.incumbent import Candidate
from runledger.measure import MeasureSpec
from runledger.run import run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runledger",
        description=(
            "Run CLI experiments under a time budget: complete snapshots, "
            "anytime sweeps, and a submittable best-known incumbent."
        ),
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help="Path to .runledger.toml")

    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run one command and snapshot it", parents=[common])
    run.add_argument("--out", help="runs root (default: runs/)")
    run.add_argument("--name", help="Run name (used in run-id)")
    run.add_argument("--timeout", type=float, help="Per-run timeout in seconds")
    run.add_argument("--input", help="stdin input file (hashed; copied with --copy-input)")
    run.add_argument("--copy-input", action="store_true", help="Copy the input file into the snapshot")
    run.add_argument(
        "--capture-env",
        nargs="?",
        const="allow",
        default="allow",
        choices=["allow", "all"],
        help="Env capture: allowlist (default) or 'all'",
    )
    run.add_argument("--env", action="append", default=[], help="Extra env K=V (repeatable)")
    run.add_argument("--cwd", help="Working directory for the command")
    run.add_argument("--tag", action="append", default=[], help="Metadata tag k=v (repeatable)")
    run.add_argument("--measure", help="Measure spec: tune-line|regex|json:PATH|file:PATH|none")
    run.add_argument("--shell", action="store_true", help="Run via the shell (default: argv list)")

    sweep = sub.add_parser(
        "sweep", help="Sweep configs.tsv with an anytime cutoff", parents=[common]
    )
    sweep.add_argument("configs", type=Path, help="configs.tsv path")
    sweep.add_argument("--budget", type=float, required=True, help="Per-run time budget (sec)")
    sweep.add_argument("--elapse", type=float, help="Total wall budget for the sweep (sec)")
    sweep.add_argument("--timeout", type=float, help="Per-run timeout override (default: budget)")
    sweep.add_argument(
        "--objective",
        default=None,
        choices=list(inc.OBJECTIVES),
        help="max-score|min-elapsed|score-per-sec",
    )
    sweep.add_argument("--measure", help="Measure spec (see run --measure)")
    sweep.add_argument("--launcher", help="Launcher template, e.g. 'mpirun -n {ranks}'")
    sweep.add_argument("--bindir", help="Directory holding binaries")
    sweep.add_argument("--bin-template", help="Binary path template, e.g. 'build/{bin}'")
    sweep.add_argument("--sweep-out", help="sweeps root (default: sweeps/)")
    sweep.add_argument("--state", help="state dir (default: state/)")
    sweep.add_argument("--name", help="Sweep round name (default: round-NNN)")
    sweep.add_argument("--cwd", help="Working directory for runs")

    gen = sub.add_parser(
        "gen-configs", help="Generate configs.tsv from a space.tsv", parents=[common]
    )
    gen.add_argument("space", type=Path, help="space.tsv path")
    gen.add_argument("--n", type=int, default=12, help="Number of LHS samples")
    gen.add_argument("--method", default="lhs", choices=["lhs", "grid"], help="Sampling method")
    gen.add_argument("--seed", type=int, help="Random seed (LHS)")
    gen.add_argument("--target", default="solver", help="target column value")
    gen.add_argument("--bin", default="solver", help="bin column value")
    gen.add_argument("--ranks", type=int, default=1, help="ranks column value")
    gen.add_argument("--omp", type=int, default=1, help="omp column value")
    gen.add_argument("--rep", type=int, default=1, help="rep column value")
    gen.add_argument("-o", "--output", help="Write to file instead of stdout")

    incumbent = sub.add_parser(
        "incumbent", help="Compute the incumbent from a sweep", parents=[common]
    )
    incumbent.add_argument("sweep_dir", type=Path, help="A sweeps/<round> directory")
    incumbent.add_argument(
        "--objective",
        default=None,
        choices=list(inc.OBJECTIVES),
        help="max-score|min-elapsed|score-per-sec",
    )
    incumbent.add_argument("--state", help="state dir (default: state/)")

    summary = sub.add_parser(
        "summary", help="Summarize a run or sweep directory", parents=[common]
    )
    summary.add_argument("path", type=Path, help="A runs/<id> or sweeps/<round> directory")

    return parser


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def _parse_kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" in item:
            key, value = item.split("=", 1)
            out[key] = value
    return out


def _resolve_latest(path: Path) -> Path:
    if path.is_symlink():
        return path.resolve()
    if path.name == "latest" and not path.is_dir():
        txt = path.parent / "latest.txt"
        if txt.is_file():
            return path.parent / txt.read_text(encoding="utf-8").strip()
    return path


def _build_measure(args, cfg) -> MeasureSpec | None:
    try:
        return MeasureSpec.from_cli(args.measure, cfg.measure)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _validate_objective(objective: str) -> bool:
    if objective not in inc.OBJECTIVES:
        print(
            f"error: unknown objective '{objective}'. "
            f"Choose: {', '.join(inc.OBJECTIVES)}",
            file=sys.stderr,
        )
        return False
    return True


def cmd_run(args, cfg) -> int:
    if not args.trailing:
        print("error: run needs a command after '--'", file=sys.stderr)
        return 2
    spec = _build_measure(args, cfg)
    if spec is None:
        return 2
    rec = run_once(
        args.trailing,
        out_root=Path(args.out or cfg.runs_dir),
        name=args.name,
        timeout=args.timeout,
        input_path=Path(args.input) if args.input else None,
        capture_env_mode=args.capture_env,
        extra_env=_parse_kv(args.env),
        cwd=Path(args.cwd) if args.cwd else None,
        shell=args.shell,
        measure_spec=spec,
        copy_input=args.copy_input,
        tags=_parse_kv(args.tag),
    )
    m = rec.measure
    print(f"{rec.outcome.outcome}  exit={rec.outcome.exit_code}  wall={rec.outcome.wall_sec}s")
    if m.source != "none":
        print(f"measure[{m.source}]: elapsed={m.elapsed} score={m.score} correct={m.correct}")
    print(f"snapshot: {rec.run_dir}")
    return 0 if rec.outcome.outcome == "completed" else 1


def cmd_sweep(args, cfg) -> int:
    objective = args.objective or cfg.objective
    if not _validate_objective(objective):
        return 2
    spec = _build_measure(args, cfg)
    if spec is None:
        return 2
    result = sweep_mod.run_sweep(
        args.configs,
        base_command=args.trailing or None,
        sweep_root=Path(args.sweep_out or cfg.sweeps_dir),
        state_dir=Path(args.state or cfg.state_dir),
        budget=args.budget,
        elapse=args.elapse,
        timeout=args.timeout,
        objective=objective,
        measure_spec=spec,
        launcher=args.launcher,
        bindir=args.bindir,
        bin_template=args.bin_template,
        cwd=Path(args.cwd) if args.cwd else None,
        sweep_name=args.name,
    )
    print(f"sweep: {result.sweep_dir}")
    print(
        f"configs run: {result.configs_run}/{result.configs_total}"
        f"  incumbent updates: {result.incumbent_updates}"
        f"{'  (stopped early)' if result.stopped_early else ''}"
    )
    return 0


def cmd_gen_configs(args, cfg) -> int:
    text = gen_configs_file(
        args.space,
        n=args.n,
        method=args.method,
        seed=args.seed,
        target=args.target,
        bin_name=args.bin,
        ranks=args.ranks,
        omp=args.omp,
        rep=args.rep,
    )
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_incumbent(args, cfg) -> int:
    sweep_dir = _resolve_latest(args.sweep_dir)
    objective = args.objective or cfg.objective
    if not _validate_objective(objective):
        return 2
    rows = sweep_mod.read_results(sweep_dir)
    configs, _ = sweep_mod.read_configs(sweep_dir / "configs.tsv")
    by_id = {c.id: c for c in configs}

    candidates: list[Candidate] = []
    for row in rows:
        c = by_id.get(row.id)
        if c is None:  # result with no backing config is never adopted
            continue
        candidates.append(
            Candidate(
                id=row.id,
                target=c.target,
                bin=c.bin,
                args=c.args,
                elapsed=row.elapsed,
                score=row.score,
                correct=row.correct,
                source_sweep=sweep_dir.name,
                source_run=row.run_id,
            )
        )

    best = inc.compute_best(candidates, objective=objective)
    if best is None:
        print("no correct candidate found; incumbent unchanged")
        return 0

    state_dir = Path(args.state or cfg.state_dir)
    updated = inc.update_incumbent(state_dir, best, objective=objective)
    current = inc.load_incumbent(state_dir)
    status = "updated" if updated else "unchanged (existing incumbent not improved)"
    print(f"incumbent {status}: {state_dir / 'incumbent.json'}")
    if current:
        print(
            f"  id={current['id']} objective={current['objective']} "
            f"score={current['score']} elapsed={current['elapsed']}"
        )
    return 0


def cmd_summary(args, cfg) -> int:
    path = _resolve_latest(args.path)
    meta_path = path / "meta.json"
    results_path = path / "results.csv"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        print(f"run {meta['run_id']}")
        print(f"  outcome : {meta['outcome']} (exit {meta['exit_code']})")
        print(f"  wall    : {meta['wall_sec']}s")
        print(f"  command : {' '.join(meta['command'])}")
        print(f"  measure : {meta['measure']}")
        if meta.get("git"):
            print(f"  git     : {meta['git']}")
        return 0
    if results_path.is_file():
        rows = sweep_mod.read_results(path)
        print(f"sweep {path.name}: {len(rows)} configs")
        correct = [r for r in rows if r.correct]
        print(f"  correct : {len(correct)}/{len(rows)}")
        if correct:
            best = max(correct, key=lambda r: r.score)
            print(f"  best score: id={best.id} score={best.score} elapsed={best.elapsed}")
        return 0
    print(f"error: not a run or sweep directory: {path}", file=sys.stderr)
    return 2


_HANDLERS = {
    "run": cmd_run,
    "sweep": cmd_sweep,
    "gen-configs": cmd_gen_configs,
    "incumbent": cmd_incumbent,
    "summary": cmd_summary,
}


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    head, trailing = _split_command(raw)
    parser = build_parser()
    args = parser.parse_args(head)
    args.trailing = trailing
    cfg = load_config(args.config)
    return _HANDLERS[args.cmd](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
