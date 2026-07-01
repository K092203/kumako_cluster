"""Backend adapters: llm, codex, custom, fake."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from review_artifact.config import BackendConfig


@dataclass
class BackendResult:
    output: str
    command: str
    backend_name: str
    exit_code: int = 0
    error: str | None = None


class BackendError(Exception):
    pass


class Backend:
    name: str = "base"

    def __init__(self, config: BackendConfig):
        self.config = config

    def run(self, prompt: str, cwd: Path) -> BackendResult:
        raise NotImplementedError


class FakeBackend(Backend):
    """Returns fixed output for tests and dry CI runs."""

    name = "fake"

    def __init__(self, config: BackendConfig, output: str | None = None):
        super().__init__(config)
        self._output = output or _DEFAULT_FAKE_OUTPUT

    def run(self, prompt: str, cwd: Path) -> BackendResult:
        return BackendResult(
            output=self._output,
            command="fake",
            backend_name=self.name,
            exit_code=0,
        )


class LlmBackend(Backend):
    name = "llm"

    def run(self, prompt: str, cwd: Path) -> BackendResult:
        cmd_parts = shlex.split(self.config.command)
        if not cmd_parts:
            raise BackendError("backend command is empty")
        return _run_subprocess(
            cmd_parts,
            prompt=prompt,
            cwd=cwd,
            backend_name=self.name,
            prompt_stdin=self.config.prompt_stdin,
            timeout=self.config.timeout,
        )


class CodexBackend(Backend):
    name = "codex"

    def run(self, prompt: str, cwd: Path) -> BackendResult:
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            prompt,
        ]
        return _run_subprocess(
            cmd,
            prompt=None,
            cwd=cwd,
            backend_name=self.name,
            prompt_stdin=False,
            timeout=self.config.timeout,
            display_command="codex exec --sandbox read-only --skip-git-repo-check <prompt>",
        )


class CustomBackend(Backend):
    name = "custom"

    def run(self, prompt: str, cwd: Path) -> BackendResult:
        cmd_parts = shlex.split(self.config.command)
        if not cmd_parts:
            raise BackendError("custom backend command is empty")
        return _run_subprocess(
            cmd_parts,
            prompt=prompt,
            cwd=cwd,
            backend_name=self.name,
            prompt_stdin=self.config.prompt_stdin,
            timeout=self.config.timeout,
        )


def _run_subprocess(
    cmd: list[str],
    *,
    prompt: str | None,
    cwd: Path,
    backend_name: str,
    prompt_stdin: bool,
    timeout: int,
    display_command: str | None = None,
) -> BackendResult:
    executable = cmd[0]
    if not _command_exists(executable):
        raise BackendError(
            f"backend '{backend_name}' requires '{executable}' on PATH. "
            f"Install it or choose another backend with --backend."
        )

    try:
        result = subprocess.run(
            cmd,
            # Empty (not inherited) stdin when not piping the prompt, so CLIs that
            # read stdin (e.g. codex exec) get EOF instead of blocking.
            input=prompt if prompt_stdin else "",
            text=True,
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendError(
            f"backend '{backend_name}' timed out after {timeout}s"
        ) from exc

    output = result.stdout
    if result.stderr.strip():
        output = f"{output}\n{result.stderr}".strip() if output else result.stderr.strip()

    if result.returncode != 0 and not output:
        raise BackendError(
            f"backend '{backend_name}' failed (exit {result.returncode}): "
            f"{result.stderr.strip() or 'no output'}"
        )

    return BackendResult(
        output=output,
        command=display_command or " ".join(shlex.quote(p) for p in cmd),
        backend_name=backend_name,
        exit_code=result.returncode,
        error=result.stderr.strip() or None,
    )


def _command_exists(name: str) -> bool:
    if os.sep in name or (os.altsep and os.altsep in name):
        return Path(name).is_file()
    path = os.environ.get("PATH", "")
    for directory in path.split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return True
    return False


def create_backend(
    config: BackendConfig,
    *,
    fake_output: str | None = None,
) -> Backend:
    name = config.name.lower()
    if name == "fake":
        return FakeBackend(config, output=fake_output)
    if name == "llm":
        return LlmBackend(config)
    if name == "codex":
        return CodexBackend(config)
    if name == "custom":
        return CustomBackend(config)
    raise BackendError(
        f"unknown backend '{config.name}'. Choose: llm, codex, custom, fake"
    )


_DEFAULT_FAKE_OUTPUT = """## Summary
Sample job appears to have timed out before completion.

## Findings
- **medium**: Job did not reach completed status; stderr shows deadline reached.
- **low**: A second finding cites evidence that is not in the logs (to demonstrate rejection).

## Open Questions
- Was the walltime limit sufficient for this input size?

```json
{
  "summary": "Sample job appears to have timed out before completion.",
  "findings": [
    {
      "severity": "medium",
      "title": "Job timed out",
      "body": "stderr reports the deadline was reached before completion.",
      "file": "examples/sample-results/stderr.txt",
      "line": 2,
      "evidence": "deadline reached",
      "confidence": "medium"
    },
    {
      "severity": "low",
      "title": "Claimed segfault (fabricated evidence)",
      "body": "Reviewer claims a segfault, but no such text exists in the logs.",
      "file": "examples/sample-results/stderr.txt",
      "line": 4,
      "evidence": "Segmentation fault (core dumped)",
      "confidence": "low"
    }
  ],
  "open_questions": ["Was the walltime limit sufficient for this input size?"]
}
```
"""
