# Backends

v0.1 supports four backends:

## `llm` (default, recommended)

Uses [simonw/llm](https://github.com/simonw/llm). Configure model via TOML or CLI:

```toml
[backend]
name = "llm"
command = "llm -m gpt-4o"
```

## `codex`

Uses Codex CLI in read-only sandbox mode. review-artifact runs:

```bash
codex exec --sandbox read-only --skip-git-repo-check <prompt>
```

`--skip-git-repo-check` lets it run outside a git repository, and the prompt is
passed as an argument with empty stdin (codex otherwise blocks on "Reading
additional input from stdin…"). Requires `codex login`. Verified end-to-end with
gpt-5.5 (see the README "Verification" section).

## `custom`

Any read-only command that accepts prompt on stdin:

```toml
[backend]
name = "custom"
command = "my-reviewer --readonly"
prompt_stdin = true
```

## `fake`

Returns fixed output for tests and CI. No external AI call.

```bash
review-artifact logs examples/sample-results --backend fake
```

## Read-only guarantee

This CLI only **reads** files you point it at. Actual sandbox strength depends on the backend you choose. See [SECURITY.md](SECURITY.md).
