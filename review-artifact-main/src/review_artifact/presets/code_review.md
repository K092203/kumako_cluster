# Code Review Preset (secondary)

Review a git diff or source files for behavioral risk.

Focus on:
- behavioral bugs and regressions
- edge cases and concurrency issues
- security footguns
- missing tests or stale documentation

Ignore:
- style preferences
- large unrelated refactors
- cosmetic cleanup

Diff review is a secondary feature. Findings are advisory; verify file/line against provided content only.
