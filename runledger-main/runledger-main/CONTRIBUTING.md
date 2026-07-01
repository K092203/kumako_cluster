# Contributing

Thanks for your interest in runledger.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Scope (v0.1)

runledger stays small: local command execution, TSV sweeps with an anytime
cutoff, and incumbent management. Please keep these out of the core:

- scheduler submit adapters (PJM/SLURM/PBS) — see `docs/hpc.md`
- optimizers / Bayesian search — the search engine is a pluggable upstream that
  emits `configs.tsv`; the tune layer stays unchanged
- web UI, SQLite index, cloud sync

## Guidelines

- Standard library only for runtime code (pytest is the only dev dependency).
- Add or update tests for behavior changes; keep `pytest` green.
- A non-zero exit must never be trusted as a measurement — preserve that rule.
