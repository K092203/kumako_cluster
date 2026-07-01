# Security

## What this tool does

1. **Collects** files, git diffs, or log directories you specify.
2. **Sends** their contents to an external AI backend (llm, codex, or custom).
3. **Saves** the review as local Markdown/JSON artifacts.

It does **not** modify your repository.

## Risks

- Private code, logs, or secrets may be sent to third-party AI providers.
- The destination depends entirely on your backend configuration.
- Secret guard (skipping `.env`, `*.pem`, `*token*`, etc.) is a **best-effort helper**, not a guarantee against leakage.
- Findings are advisory; line numbers may be wrong even after verification.

## Recommendations

- Use `--dry-run` to inspect what will be sent before calling a backend.
- Do not use `--allow-sensitive` unless you understand the exposure.
- Prefer local or trusted backends for sensitive workloads.
- Treat `.review/` artifacts as potentially sensitive if source logs were sensitive.

## Reporting

If you discover a security issue, please report it responsibly to the project maintainers.
