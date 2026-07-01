# Example: hello

A pure-Python stochastic "solver" — no build step.

```bash
# single snapshot
runledger run --name baseline -- python3 solver.py --alpha 1.5 --budget 0.1

# sweep a generated space with an anytime cutoff
runledger gen-configs solver.space.tsv --n 8 --seed 1 -o configs.tsv
runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 solver.py
runledger incumbent sweeps/latest --objective max-score
```

## Without `#TUNE` (regex measure)

`regex_solver.py` prints plain lines and never emits a `#TUNE` line, to show that
instrumentation is optional. Pair it with `regex.runledger.toml`:

```bash
runledger run --config regex.runledger.toml --measure regex -- python3 regex_solver.py --alpha 1.2 --budget 0.1
```
