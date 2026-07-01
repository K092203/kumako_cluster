> 🌐 [English](README.md) · [日本語](README.ja.md) · [中文](README.zh.md)

# review-artifact

**Read-only AI triage for experiment artifacts (logs, job outputs), plus diffs
and files.** Point it at a job's logs, a diff, or any files; it saves the review
as reproducible **Markdown / JSON**. **The reviewer never edits your repo.**

```text
Triage your experiment artifacts with a read-only AI.
Point it at a job's logs (meta.json, stderr, resource), a diff, or any files,
and save the review as reproducible Markdown/JSON — the reviewer never edits your repo.
```

- **Status:** v0.1.2 · MIT · pure standard library, no runtime dependencies
- **Verified:** 24 tests (Python 3.12) + a real `codex`/gpt-5.5 end-to-end run, 2026-07-01 — see [Verification](#verification)
- **Known limitation:** the reject/relocate guard is proven by tests; a real-model hallucination has not (yet) been observed to catch (see [Known limitations](#known-limitations))

---

## Why

You ran a job; `meta.json` / `stderr` / `resource` don't immediately tell you if
it failed, timed out, or hit a bottleneck. `review-artifact` triages that with a
**read-only** AI and saves the result as an artifact — instead of letting an
agent mutate your repo. The primary use case is **experiment log triage**; diff
and file review are secondary.

## How it differs

| vs | what review-artifact adds |
|----|---------------------------|
| **fabric / llm / mods** | Target collectors (git diff, log bundle, files) + **outcome-aware JSON findings** + saved **artifact files**. Those tools stop at "text → AI → text". |
| **CodeRabbit / Copilot / PR bots** | **Local, read-only, artifact-first**, and centered on **experiment logs** — not server-side PR diffs. |

**The wedge:** you ran jobs whose `meta.json`/`stderr`/`resource` don't make the
failure or bottleneck obvious, and you want a read-only, structured triage saved
as an artifact. Diff review is supported but secondary.

## What makes the artifact trustworthy

1. **Read-only by contract** — the CLI only reads what it collects; the prompt
   tells the reviewer it cannot edit. No agent mutates your repo.
2. **Citations are evidence-checked** — LLMs hallucinate file/line. The reviewer
   must **quote the exact text** it is citing, and that quote is verified against
   the actually-collected content. A **fabricated quote is rejected**
   (`line_verified: false`), and a **real quote on the wrong line is
   auto-corrected** (`line_relocated: true`). A plain line-existence check is the
   fallback when no quote is given. Findings are always advisory.
3. **Secrets are skipped by default** — `.env`, `*.pem`, `*token*`, etc. are not
   collected unless you pass `--allow-sensitive`.

All three are demonstrated in [Verification](#verification).

## Install

```bash
cd review-artifact
pip install -e ".[dev]"
```

Requires Python 3.11+. No third-party runtime dependencies (pytest is dev-only).

## Quickstart (logs first)

```bash
# Primary: triage job artifacts with the fake backend (no API key needed)
review-artifact logs examples/sample-results --backend fake

# Inspect exactly what would be sent, without calling any AI
review-artifact logs examples/sample-results --dry-run

# With simonw/llm (install llm separately)
review-artifact logs results/latest --backend llm --language ja

# Secondary: diff / file review
review-artifact diff --dry-run
review-artifact files README.md --backend fake
```

Artifacts are written to `.review/` by default (`<ts>-<target>.md` / `.json`).

## Commands

```bash
review-artifact logs <dir>          # ★ primary: job/experiment log triage
review-artifact diff                # git diff review (secondary)
review-artifact files <paths...>    # file review
review-artifact ask "question" --files src/foo.cpp
```

## Output artifact (JSON, schema v1)

```json
{
  "schema_version": 1,
  "summary": "...",
  "findings": [
    {
      "severity": "medium",
      "title": "...",
      "body": "...",
      "file": "examples/sample-results/stderr.txt",
      "line": 2,
      "evidence": "deadline reached",
      "line_verified": true,
      "line_relocated": false,
      "confidence": "medium"
    }
  ],
  "open_questions": [],
  "raw_output": "..."
}
```

Markdown is for humans; JSON is for CI / bots / another AI. Findings are
**advisory** — file/line citations are verified against collected content when
possible, and unverified lines are nulled.

## Backends (multi-backend from v0.1)

| Backend | Description |
|---------|-------------|
| `llm` | [simonw/llm](https://github.com/simonw/llm) — **recommended default** |
| `codex` | `codex exec --sandbox read-only` |
| `custom` | any read-only command (prompt on stdin) |
| `fake` | fixed output for tests / CI (no network) |

See [docs/backends.md](docs/backends.md).

## Verification

Captured on **Python 3.12.3 / Linux 6.18 (WSL2) / 2026-07-01**.

### Line verification — existence-only vs evidence-checked

The headline feature, proven against the real `examples/sample-results/stderr.txt`
(`line 2 = "ERROR: deadline reached before completion"`). The old check only asked
"does this line exist?"; the new one asks "is the quoted evidence actually there?":

```text
case                     | BEFORE (exists?)         | AFTER (evidence-checked)
-------------------------|--------------------------|------------------------------
correct citation         | line=2 verified=True     | line=2 verified=True
right quote, wrong line  | line=5 verified=True     | line=2 verified=True relocated
FABRICATED evidence      | line=4 verified=True     | line=None verified=False
no quote (legacy)        | line=4 verified=True     | line=4 verified=True
```

The two middle rows are the wins: a real quote cited on the wrong line is
**auto-corrected**, and a fabricated quote that the old check happily "verified"
is now **rejected**. (These rows use crafted findings; see the honest caveat in
the real-LLM run below.)

### Real-LLM end-to-end (codex / gpt-5.5)

Run for real on 2026-07-01 with the `codex` backend (`codex exec --sandbox read-only`):

```bash
review-artifact logs examples/sample-results --backend codex
```

Across two real runs (the sample logs and a 60-line iterative log), gpt-5.5
produced **8 findings / 7 citations, and every citation was verbatim and on the
correct line** — cross-checked by hand against the source. The guard caused
**zero false rejections**; e.g. on the 60-line log it pinned the divergence to
`stdout.txt:41` (`iter 41: residual=nan`) among 60 near-identical lines.

**Honest caveat:** gpt-5.5 did **not** fabricate a citation in these runs, so the
*reject/relocate* path was exercised only by the deterministic tests and the
before/after table above — **not** by a real-model mistake. The real run proves
the loop works end-to-end with a real LLM and that the guard does not damage
correct output; it does **not** yet show a real hallucination being caught.

### Test suite — 24 passing

```text
tests/test_artifacts.py  2   (artifact naming, markdown + json writers)
tests/test_cli.py        3   (logs/diff/files via fake backend)
tests/test_collect.py    7   (git/file/dir collectors, size limit, binary + secret skip)
tests/test_config.py     4   (defaults, TOML override, merge)
tests/test_findings.py   8   (JSON extraction; line verification: exists / evidence
                              corroborated / relocated / fabricated-rejected)
                        ---
                         24   passed in 0.24s
```

### Behavioral checks (end-to-end, fake backend)

| Behavior | Command | Observed |
|----------|---------|----------|
| Log triage (primary) | `logs examples/sample-results --backend fake` | wrote `.review/…-logs….md` + `.json` |
| **Evidence verified** | fake cites `stderr.txt:2` evidence `"deadline reached"` | quote found at line 2 → `line_verified=true` |
| **Fabricated rejected** | fake cites a `"Segmentation fault"` not in the logs | `line=null line_verified=false`, marked *citation rejected* |
| Dry-run (no AI call) | `logs … --dry-run` | prints the exact collected bundle, calls nothing |
| **Secret guard** | `files .env --dry-run` | `skipped .env: sensitive` (needs `--allow-sensitive` to include) |
| Backend selection | `--backend bogus` | `invalid choice … (llm, codex, custom, fake)`, `exit=2` |

Representative output (the Markdown artifact from the fake backend):

```markdown
## Findings
- **medium**: Job timed out (examples/sample-results/stderr.txt:2, verified)
  stderr reports the deadline was reached before completion.
  evidence: `deadline reached`
- **low**: Claimed segfault (fabricated evidence) (examples/sample-results/stderr.txt, citation rejected: evidence not found)
  Reviewer claims a segfault, but no such text exists in the logs.
```

```console
$ review-artifact files .env --dry-run
--- COLLECTION NOTES ---
skipped .env: sensitive
```

CI runs the same suite on Python 3.12 with the fake backend (no API calls).

## Security

- Sends collected file contents to your configured AI backend.
- Skips sensitive-looking files by default (`.env`, `*.pem`, `*token*`, etc.).
- Use `--allow-sensitive` only when intentional.
- The secret guard is a helper, not a guarantee — review before sharing.
- See [docs/SECURITY.md](docs/SECURITY.md).

## Documentation

- [docs/configuration.md](docs/configuration.md) — `.review-artifact.toml`
- [docs/backends.md](docs/backends.md) — llm / codex / custom / fake
- [docs/prompts.md](docs/prompts.md) — prompt presets
- [docs/SECURITY.md](docs/SECURITY.md) — secret handling

Examples: [examples/sample-results](examples/sample-results) (fake-backend demo),
[examples/fugaku-results.toml](examples/fugaku-results.toml) (HPC config example).

## Pairs with runledger

[runledger](https://github.com/K092203/runledger) captures the run snapshots that
review-artifact triages:

```bash
runledger run -- ./solver < input.txt
review-artifact logs runs/latest      # read-only AI triage of the snapshot
```

## Known limitations

- **`codex`/gpt-5.5 verified end-to-end (2026-07-01); `llm` backend not yet run.**
  CI and automated tests use the `fake` backend (no network/API). In the real
  `codex` runs the model did not fabricate a citation, so the reject/relocate path
  is proven by deterministic tests + the before/after table, **not** yet by a
  caught real-model hallucination.
- **Verification confirms the citation, not the conclusion.** A finding can quote
  a real line and still draw a wrong inference; `line_verified` means the quote is
  real, not that the finding is correct. Findings are advisory.
- **Read-only strength depends on the backend's sandbox.** The CLI itself only
  reads, but it cannot guarantee a backend won't take actions of its own.
- **Line verification and JSON findings are best-effort.** When the reviewer's
  JSON cannot be parsed, the artifact keeps `raw_output` with `findings: []`.

## Non-goals (v0.1)

Auto-fix / auto-commit, GitHub PR comment bot, web UI / TUI, full provider
abstraction, security-audit tooling.

## License

MIT — see [LICENSE](LICENSE).
