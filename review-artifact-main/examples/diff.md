# Example: diff review (secondary)

```bash
# Inspect collected git diff and prompt
review-artifact diff --dry-run

# Run with fake backend (no API)
review-artifact diff --backend fake

# With llm
review-artifact diff --backend llm --prompt code_review --language en
```

Diff review is supported but not the primary use case. Prefer `logs` for experiment triage.
