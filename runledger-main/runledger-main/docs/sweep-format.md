# Sweep format

`runledger sweep` runs each config `rep` times, aggregates the repetitions,
appends a row to `results.csv` immediately, and updates the incumbent live.

```text
sweeps/
  latest -> round-001/
  round-001/
    configs.tsv      # a copy of the configs that were swept
    results.csv      # one row per config, appended as it finishes
    errors.log       # malformed rows, missing bins, anytime cutoffs
    runs/
      000_rep001/    # a full snapshot per repetition
      000_rep002/
      001_rep001/
state/
  incumbent.json
```

## configs.tsv (tab-separated)

```text
id	target	bin	ranks	omp	rep	args
000	solver	solver	1	4	3	--block 128 --alpha 0.5
001	solver	solver	1	4	3	--block 256 --alpha 0.9
```

- `ranks` / `omp` / `rep` are sanitized to positive integers (default 1).
- `omp` is exported as `OMP_NUM_THREADS` for each run.
- duplicate `id`s are detected and skipped (logged to `errors.log`).
- generate this file with `runledger gen-configs` or write it by hand.

## results.csv

```csv
id,elapsed,correct,score,exit_code,rep_done,run_id,notes
000,0.502463,1,2816.997184,0,3,000_rep002,ok
001,20.000000,0,0,124,1,001_rep001,timeout
```

Aggregation:
- `elapsed` = median across repetitions (trusted measurements only),
- `score` = score of the representative (median-elapsed) repetition,
- `correct` = AND of all repetitions (and all must have completed),
- a non-zero exit is not trusted; timeout / missing-bin / invalid-config get a
  finite penalty row so the sweep keeps going.

## anytime cutoff

Before each config, if `spent + budget*rep + margin > elapse`, the sweep stops
and keeps everything measured so far. This is the whole point: a killed sweep
still leaves a valid `results.csv` and a valid incumbent.

## incumbent.json (schema_version 1)

```json
{
  "schema_version": 1,
  "id": "000",
  "objective": "max-score",
  "target": "solver",
  "bin": "solver",
  "args": "--block 128 --alpha 0.5",
  "elapsed": 0.502463,
  "score": 2816.997184,
  "correct": true,
  "source_sweep": "round-001",
  "source_run": "000_rep002",
  "updated_at": "2026-06-28T23:20:00+09:00"
}
```

Update rules: only `correct` candidates; replace only on a **strict** objective
improvement; atomic write; a result with no backing config is never adopted.

Objectives: `max-score`, `min-elapsed`, `score-per-sec`.
