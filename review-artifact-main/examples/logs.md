# Example: logs triage workflow

```bash
# Primary use case — experiment/job log triage
review-artifact logs results/latest --backend llm --language ja

# Dry-run to inspect bundle before sending to AI
review-artifact logs results/latest --dry-run

# CI / local test without API
review-artifact logs examples/sample-results --backend fake
```

Expected inputs in the log directory:

- `meta.json` — job metadata
- `build.log` — build output
- `stdout.txt` / `stderr.txt` — runtime output
- `resource.txt` — resource usage
- `status.txt` — completion status
