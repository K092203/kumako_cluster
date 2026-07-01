# Example: cpp-solver

A compiled solver, swept via `--bindir` so the config's `bin` column resolves to
the built binary.

```bash
make                       # -> build/solver
runledger run -- build/solver --alpha 1.5 --budget 0.2

# sweep using bindir + the bin column from configs.tsv
runledger gen-configs ../hello/solver.space.tsv --n 8 --bin solver -o configs.tsv
runledger sweep configs.tsv --bindir build --budget 1 --elapse 30 --objective max-score
runledger incumbent sweeps/latest --objective max-score
```

Here no trailing `-- <cmd>` is given, so each run executes `build/<bin>` (from
the config's `bin` column) plus that config's `args`.
