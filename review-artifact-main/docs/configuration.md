# Configuration

review-artifact reads `.review-artifact.toml` from the current directory or any parent.

## Top-level keys

| Key | Default | Description |
|-----|---------|-------------|
| `language` | `ja` | Reviewer prompt language (`ja` or `en`) |

## `[backend]`

| Key | Default | Description |
|-----|---------|-------------|
| `name` | `llm` | `llm`, `codex`, `custom`, or `fake` |
| `command` | `llm` | Command for `llm` / `custom` backends |
| `prompt_stdin` | `true` | Send prompt on stdin |
| `timeout` | `300` | Backend timeout in seconds |

## `[outputs]`

| Key | Default | Description |
|-----|---------|-------------|
| `dir` | `.review` | Artifact output directory |
| `formats` | `["markdown", "json"]` | Output formats |

## `[targets.*]`

Named presets for common workflows. Example:

```toml
[targets.results_latest]
kind = "directory"
path = "results/latest"
prompt = "experiment_analysis"
include = ["meta.json", "stderr.txt"]
```

See `examples/review-artifact.toml` and `examples/fugaku-results.toml`.
