# Security & safety

A snapshot can capture sensitive data: environment variables, command lines,
stdout/stderr, input contents, and git state. runledger defaults conservatively.

## Environment variables

Captured to `env.txt` with an **allowlist by default** — only well-known,
non-secret keys are recorded:

- exact: `CUDA_VISIBLE_DEVICES`, `SLURM_JOB_ID`, `PJM_JOBID`,
  `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`
- prefixes: `OMP_`, `SLURM_`, `PJM_`, `MV2_`, `UCX_`, `OMPI_`

The full environment is recorded only with `runledger run --capture-env=all`.

## Input files

By default only the **hash** of `--input` is stored (`input.sha256`). The file
content is copied into the snapshot only with `--copy-input`, and even then
files matching secret-like patterns are skipped:

```
.env  *.pem  *.key  id_rsa  *token*  *secret*  .netrc  credentials.json
```

## stdout / stderr

These are saved verbatim. If your program echoes secrets, they will land in
`stdout.txt` / `stderr.txt`. Review before sharing a snapshot.

## What runledger does not do

- It does not transmit anything off the machine.
- It does not run a shell unless you pass `--shell`.
- The command after `--` is executed as an argv list (no shell quoting
  surprises) by default.
