> 🌐 [English](README.md) · [日本語](README.ja.md) · [中文](README.zh.md)

# runledger

**Tiny experiment run ledger for CLI programs under a time budget.**
Capture complete run snapshots, sweep configs with an **anytime cutoff**, and
always keep a **submittable best-known incumbent** — without a heavyweight
experiment platform.

```text
measure -> keep -> compare -> hold the best (in a form you can always submit)
```

- **Status:** v0.1.0 (pre-release) · MIT · pure standard library, no runtime dependencies
- **Verified:** 35 tests passing on Python 3.12 / Linux (WSL2), 2026-06-30 — see [Verification](#verification)
- **Known limitation:** not yet verified on Windows (see [Known limitations](#known-limitations))

---

## Why

Ad hoc experiment logs disappear, and when a job is killed you lose the best
result. `runledger` makes every run inspectable — stdout, stderr, env, git
state, input hash, resource usage — and **always keeps the current best** in
`state/incumbent.json`. Even if a job is preempted mid-sweep, you still have a
valid result to submit.

## How it differs

| vs | what runledger adds |
|----|---------------------|
| **Guild AI / DVC exp** | An **anytime cutoff** under a deadline + a **submittable `incumbent.json`** — not "run every trial, pick the best afterwards". |
| **hyperfine** | A config **sweep** + full **snapshots** + an **incumbent**, not just timing statistics. |
| **Optuna / GPTune / OpenTuner** | runledger is **not an optimizer**. LHS/grid/manual configs are enough; a smarter search engine is an upstream that simply emits `configs.tsv`. |

**The wedge:** deadline-bound CLI experiments that may be killed, where you must
always have the current best in a submittable form. Think HPC job runners,
competitive/optimization solver authors, and benchmark sweepers — not ML
experiment tracking.

## Three guarantees

1. **Anytime** — before each config, if the remaining time cannot finish it, the
   sweep stops cleanly and keeps everything already measured.
2. **Submittable incumbent** — `state/incumbent.json` is updated live and only
   ever moves to a strictly better, *correct* result.
3. **A non-zero exit is never trusted as a measurement** — crashes and timeouts
   can never masquerade as a good result.

All three are demonstrated in [Verification](#verification).

## Install

```bash
cd runledger
pip install -e ".[dev]"
```

Requires Python 3.11+. No third-party runtime dependencies (pytest is dev-only).

## Quickstart

```bash
# 1. Snapshot a single run
runledger run --name baseline --timeout 30 -- python3 examples/hello/solver.py --alpha 1.5 --budget 0.1

# 2. Generate configs from a search space (Latin hypercube, reproducible with --seed)
runledger gen-configs examples/hello/solver.space.tsv --n 8 --seed 1 -o configs.tsv

# 3. Sweep with an anytime cutoff; the incumbent updates live
runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 examples/hello/solver.py

# 4. (Re)compute the incumbent from a finished sweep
runledger incumbent sweeps/latest --objective max-score

# 5. Summarize a run or a sweep
runledger summary runs/latest
runledger summary sweeps/latest
```

## Commands

```bash
runledger run -- <cmd>               # execute + snapshot one command
runledger gen-configs space.tsv      # space.tsv -> configs.tsv (lhs|grid)
runledger sweep configs.tsv -- <cmd> # anytime sweep: results.csv + live incumbent
runledger incumbent <sweep-dir>      # best correct config for an objective
runledger summary <run|sweep dir>    # human-readable summary
```

Objectives: `max-score`, `min-elapsed`, `score-per-sec`.

## Directory layout

```text
runs/<timestamp>-<name>/   meta.json argv.txt stdout.txt stderr.txt env.txt status.txt resource.json input.sha256
sweeps/<round>/            configs.tsv results.csv errors.log runs/<id>_rep<NNN>/
state/incumbent.json       the always-submittable best-known result
```

`latest` is a symlink (falling back to `latest.txt` where symlinks are
unavailable). See [docs/snapshot-format.md](docs/snapshot-format.md) and
[docs/sweep-format.md](docs/sweep-format.md) for the full schemas.

## Measurement is pluggable (`--measure`)

Your program reports its result however it likes; runledger reads it via a
measure spec. **A non-zero exit is never trusted, with any kind.**

| kind | how |
|------|-----|
| `tune-line` (default) | last `#TUNE elapsed=.. score=.. correct=..` line on stderr |
| `regex` | patterns over stdout/stderr (configured in a `[measure]` table) |
| `json:PATH` | a JSON file `{"elapsed":..,"score":..,"correct":..}` |
| `file:PATH` | a plain file whose first line is the score |
| `none` | exit/wall/outcome only |

`#TUNE` is the zero-config default — **not** a requirement. See
[docs/measure-spec.md](docs/measure-spec.md).

## Verification

Captured on **Python 3.12.3 / Linux 6.18 (WSL2) / 2026-06-30**.

### Test suite — 35 passing

```text
tests/test_cli.py          3   (clean error handling: bad --measure / --objective)
tests/test_gen_configs.py  6   (space parsing, LHS/grid, pow2 rounding, dedup)
tests/test_incumbent.py    6   (objectives, correct-only, strict-improvement, no regression)
tests/test_measure.py      9   (tune-line/regex/json/file/none, non-zero not trusted)
tests/test_run.py          7   (success/failure/timeout/missing-bin, input hash, env allowlist)
tests/test_sweep.py        4   (results append, continue past failure, anytime, dup-id)
                          ---
                           35   passed in 0.74s
```

### Behavioral checks (end-to-end)

| Behavior | Command | Observed |
|----------|---------|----------|
| Snapshot + measure | `runledger run -- … solver.py` | `completed exit=0`; tune-line measure extracted; 7 files written to `runs/latest/` |
| Reproducible sweep | `gen-configs --seed 1` then `sweep` | incumbent converged to `id=003 score≈1.808`; 4 live updates over 5 configs |
| **Anytime cutoff** | `sweep --budget 100 --elapse 0.001` | `configs run: 0/1 (stopped early)`; `errors.log`: *anytime cutoff before id 000* |
| **Non-zero not trusted** | run prints `#TUNE …` then `exit 3` | `outcome=failed`; `measure: elapsed=None score=None correct=None` |
| **Clean error handling** | `--measure bogus` / `--objective bogus` | `error: unknown …` + `exit=2`, no traceback, nothing executed |
| Compiled C++ via `--bindir` | `make` → `gen-configs --bin solver` → `sweep --bindir build` | built with g++ 13.3.0; `8/8` configs run; `argv.txt` resolves to `build/solver`; incumbent `id=000 score=1.95` |

Representative output:

```console
$ runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 solver.py
sweep: sweeps/round-001
configs run: 5/5  incumbent updates: 4

$ runledger sweep one.tsv --budget 100 --elapse 0.001 -- python3 solver.py
sweep: sweeps/round-002
configs run: 0/1  incumbent updates: 0  (stopped early)
# errors.log: anytime cutoff before id 000: spent=0.0s est=100.0s elapse=0.0s

$ runledger run -- python3 -c "import sys; print('#TUNE score=999 correct=1', file=sys.stderr); sys.exit(3)"
failed  exit=3  wall=0.013s
measure[tune-line]: elapsed=None score=None correct=None    # #TUNE ignored on non-zero exit
```

The compiled [examples/cpp-solver](examples/cpp-solver) was also built and swept
end-to-end (g++ 13.3.0):

```console
$ make && runledger gen-configs solver.space.tsv --n 8 --seed 1 --bin solver -o configs.tsv
$ runledger sweep configs.tsv --bindir build --budget 1 --elapse 30 --objective max-score
sweep: sweeps/round-001
configs run: 8/8  incumbent updates: 1
$ cat sweeps/latest/runs/000_rep001/argv.txt        # binary resolved from the bin column
build/solver
--alpha
1.949822
--budget
0.075639
$ runledger incumbent sweeps/latest --objective max-score
incumbent updated: state/incumbent.json
  id=000 objective=max-score score=1.949822 elapsed=0.075639
```

CI runs the Python test suite on Python 3.11 / 3.12 / 3.13.

## Security

runledger records env vars (allowlist by default), the command line,
stdout/stderr, an input hash, and git state. The full environment is recorded
only with `--capture-env=all`; input contents are copied only with
`--copy-input`, and secret-like files are skipped. See
[docs/security.md](docs/security.md).

## Documentation

- [docs/snapshot-format.md](docs/snapshot-format.md) — run directory + meta.json
- [docs/sweep-format.md](docs/sweep-format.md) — configs.tsv, results.csv, incumbent.json, anytime
- [docs/measure-spec.md](docs/measure-spec.md) — measurement kinds
- [docs/hpc.md](docs/hpc.md) — launchers, ranks/omp, scheduler notes
- [docs/security.md](docs/security.md) — env/secret handling

Examples: [examples/hello](examples/hello) (pure Python),
[examples/cpp-solver](examples/cpp-solver) (compiled, `--bindir`),
[examples/hpc-like](examples/hpc-like) (`--launcher`, ranks/omp).

## Pairs with review-artifact

[review-artifact](https://github.com/K092203/review-artifact) runs a read-only AI
triage over the snapshots runledger produces:

```bash
runledger run -- ./solver < input.txt
review-artifact logs runs/latest      # read-only AI triage of the snapshot
```

## Known limitations

- **Windows is not yet verified.** The symlink → `latest.txt` fallback and the
  empty `resource.json` path are implemented but untested on Windows; treat
  Windows as best-effort for now.
- `resource.json` is best-effort: a `getrusage` delta on Unix, `{}` elsewhere.
  `maxrss` is a high-water mark, not a precise per-run figure.
- `gen-configs --method grid` discretizes float dimensions coarsely (lo/mid/hi);
  LHS is the primary method.

## Non-goals (v0.1)

Scheduler submit adapters (PJM/SLURM/PBS), web UI, SQLite index, cloud sync,
optimizer / Bayesian search, GitHub bot.

## License

MIT — see [LICENSE](LICENSE).
