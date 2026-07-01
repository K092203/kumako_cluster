# Measurement spec (`--measure`)

`runledger` does not dictate how your program reports its result. A measure spec
decides how `elapsed` / `score` / `correct` are read from each run.

**Hard rule:** a non-zero exit is never trusted as a good measurement, with any
kind. Crashes and timeouts must not masquerade as results.

## Kinds

### `tune-line` (default)

The last line on stderr starting with `#TUNE`:

```text
#TUNE elapsed=1.234 score=567.8 correct=1
```

Zero-config and backward-compatible, but **not required** — it is just the
default. Use any other kind to avoid instrumenting your program.

### `regex`

Patterns over stdout or stderr. Configure in a `[measure]` table and select with
`--measure regex` (or set it as the default kind in config):

```toml
[measure]
kind = "regex"
stream = "stdout"
elapsed = 'time:\s*([0-9.]+)'
score   = 'best=([0-9.]+)'
correct = 'OK'          # a match means correct=1
```

The first capture group is used (or the whole match if there are no groups).

### `json:PATH`

```bash
runledger run --measure json:result.json -- ./solver
```

reads `{"elapsed":.., "score":.., "correct":..}` from `PATH` (relative to the
run's working directory).

### `file:PATH`

A plain file whose first line is the score.

### `none`

Take no measurement; drive sweep/incumbent by exit/wall/outcome only.

## Where specs come from

- CLI: `--measure tune-line|regex|json:PATH|file:PATH|none`
- Config: a `[measure]` table in `.runledger.toml` (required for `regex`
  patterns).

CLI selection wins for the kind; regex patterns come from the table.
