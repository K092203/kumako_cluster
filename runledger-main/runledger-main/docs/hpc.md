# HPC usage

`runledger` is scheduler-agnostic. It does not submit jobs; it runs commands and
records them. You typically invoke `runledger sweep` *inside* an allocation (the
node(s) you already hold) and let the anytime cutoff respect the wall limit.

## Launchers and binaries

```bash
# Launch each run through a templated launcher; {ranks}, {omp}, {bin} expand
runledger sweep configs.tsv \
  --launcher "mpirun -n {ranks}" \
  --bin-template "build/{bin}" \
  --budget 10 --elapse 1800 --objective min-elapsed
```

Command assembly precedence for each config:

1. `--launcher` template (if any), then
2. `--bin-template` → else the trailing `-- <cmd>` → else `--bindir`/`bin` → else `bin`, then
3. the config's `args`.

`omp` is exported as `OMP_NUM_THREADS`; `ranks`/`omp`/`bin` are available to the
templates.

## Anytime under a wall limit

Set `--elapse` to the remaining wall time of your allocation. Before each config
`runledger` checks `spent + budget*rep + margin` and stops early rather than
starting work it cannot finish — so a preempted job still leaves a valid
`results.csv` and `state/incumbent.json`.

## Scheduler submit (not in core)

PJM/SLURM/PBS submission is intentionally **out of scope** for v0.1. runledger
runs where it is started. Submit wrappers may arrive later as examples/adapters;
they are not part of the core tool.

## Resource capture

`resource.json` is best-effort: a `getrusage(RUSAGE_CHILDREN)` delta on Unix
(`user_sec`, `sys_sec`, `maxrss`), and `{}` on platforms without the `resource`
module. Treat `maxrss` as a high-water mark, not a precise per-run figure.
