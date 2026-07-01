# Snapshot format

A `runledger run` writes a self-contained directory so any run can be examined
after the fact.

```text
runs/
  latest -> 20260628-231500-baseline/   # symlink (or latest.txt fallback)
  20260628-231500-baseline/
    meta.json        # structured metadata (below)
    argv.txt         # one argument per line
    stdout.txt
    stderr.txt
    env.txt          # KEY=VALUE per line (allowlist by default)
    status.txt       # outcome / exit / wall, one glance
    resource.json    # best-effort rusage delta (Unix); {} elsewhere
    input.sha256     # "<sha256>  <path>" if --input was given
```

## meta.json (schema_version 1)

```json
{
  "schema_version": 1,
  "run_id": "20260628-231500-baseline",
  "name": "baseline",
  "created_at": "2026-06-28T23:15:00+09:00",
  "cwd": "/path/to/repo",
  "command": ["./build/solver", "--budget", "10"],
  "shell": false,
  "timeout_sec": 30,
  "exit_code": 0,
  "outcome": "completed",
  "wall_sec": 9.842,
  "git": { "commit": "abc1234", "dirty": true, "branch": "main" },
  "input": { "path": "input.txt", "sha256": "...", "bytes": 12345 },
  "measure": { "elapsed": 9.81, "score": 123.4, "correct": true, "source": "tune-line" },
  "tags": { "block": "128", "alpha": "0.5" }
}
```

## outcome enumeration

| outcome | meaning |
|---------|---------|
| `completed` | exit code 0 |
| `failed` | non-zero exit |
| `timeout` | killed after `--timeout` (synthetic exit 124) |
| `killed-or-incomplete` | terminated by a signal (negative return code) |
| `missing-bin` | the executable was not found (synthetic exit 127) |
| `invalid-config` | (sweep) a config row could not be used |

Only `completed` runs with a trusted measurement feed the incumbent.
A non-zero exit is never trusted as a measurement.
