"""Target collectors: git, files, directory logs."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SENSITIVE_PATTERNS = (
    ".env",
    "*.pem",
    "*.key",
    "id_rsa",
    "*token*",
    "*secret*",
    "*.p12",
    "credentials.json",
    ".netrc",
)

DEFAULT_LOG_INCLUDES = [
    "meta.json",
    "build.log",
    "stdout.txt",
    "stderr.txt",
    "resource.txt",
    "status.txt",
]


@dataclass
class CollectedFile:
    path: str
    content: str
    truncated: bool = False
    skipped_reason: str | None = None


@dataclass
class CollectionBundle:
    target: str
    files: list[CollectedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def total_bytes(self) -> int:
        return sum(len(f.content.encode("utf-8", errors="replace")) for f in self.files)

    def file_paths(self) -> set[str]:
        return {f.path for f in self.files if f.skipped_reason is None}


def is_sensitive(path: str, allow_sensitive: bool = False) -> bool:
    if allow_sensitive:
        return False
    name = Path(path).name
    for pattern in SENSITIVE_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pattern.lower()):
            return True
        if fnmatch.fnmatch(path.lower(), pattern.lower()):
            return True
    return False


def is_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data[:8192]:
        return True
    text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
    nontext = data[:8192].translate(None, text_chars)
    return len(nontext) / max(len(data[:8192]), 1) > 0.30


def read_file_limited(
    path: Path,
    *,
    max_file_bytes: int,
    allow_sensitive: bool = False,
    display_path: str | None = None,
) -> CollectedFile:
    rel = display_path or str(path)
    if is_sensitive(rel, allow_sensitive):
        return CollectedFile(path=rel, content="", skipped_reason="sensitive")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return CollectedFile(path=rel, content="", skipped_reason=str(exc))

    if is_binary(raw):
        return CollectedFile(path=rel, content="", skipped_reason="binary")

    truncated = len(raw) > max_file_bytes
    if truncated:
        raw = raw[:max_file_bytes]

    text = raw.decode("utf-8", errors="replace")
    if truncated:
        text = f"--- FILE: {rel} (truncated, first {max_file_bytes // 1024}KB) ---\n{text}"
    return CollectedFile(path=rel, content=text, truncated=truncated)


def collect_files(
    paths: list[Path],
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    allow_sensitive: bool = False,
    cwd: Path | None = None,
) -> CollectionBundle:
    bundle = CollectionBundle(target="files")
    total = 0
    base = cwd or Path.cwd()

    for path in paths:
        resolved = path if path.is_absolute() else base / path
        if not resolved.exists():
            bundle.notes.append(f"missing: {path}")
            continue

        if resolved.is_dir():
            bundle.notes.append(f"skipped directory (use logs): {path}")
            continue

        try:
            rel = str(resolved.relative_to(base))
        except ValueError:
            rel = str(resolved)

        if total >= max_total_bytes:
            bundle.notes.append(f"total size limit reached; skipped {rel}")
            continue

        collected = read_file_limited(
            resolved,
            max_file_bytes=max_file_bytes,
            allow_sensitive=allow_sensitive,
            display_path=rel,
        )
        if collected.skipped_reason:
            bundle.notes.append(f"skipped {rel}: {collected.skipped_reason}")
            continue

        size = len(collected.content.encode("utf-8", errors="replace"))
        if total + size > max_total_bytes:
            remaining = max_total_bytes - total
            if remaining > 0:
                collected.content = collected.content[:remaining]
                collected.truncated = True
                bundle.notes.append(f"truncated {rel} to fit total limit")
            else:
                bundle.notes.append(f"total size limit reached; skipped {rel}")
                continue

        total += len(collected.content.encode("utf-8", errors="replace"))
        bundle.files.append(collected)

    return bundle


def collect_directory_logs(
    directory: Path,
    *,
    include: list[str] | None = None,
    max_file_bytes: int,
    max_total_bytes: int,
    allow_sensitive: bool = False,
    cwd: Path | None = None,
) -> CollectionBundle:
    base = cwd or Path.cwd()
    resolved = directory if directory.is_absolute() else base / directory
    includes = include or DEFAULT_LOG_INCLUDES

    bundle = CollectionBundle(target="logs")
    if not resolved.is_dir():
        bundle.notes.append(f"directory not found: {directory}")
        return bundle

    try:
        dir_label = str(resolved.relative_to(base))
    except ValueError:
        dir_label = str(resolved)

    total = 0
    for pattern in includes:
        candidate = resolved / pattern
        if not candidate.is_file():
            bundle.notes.append(f"missing include: {dir_label}/{pattern}")
            continue

        rel = f"{dir_label}/{pattern}"
        if total >= max_total_bytes:
            bundle.notes.append(f"total size limit reached; skipped {rel}")
            continue

        collected = read_file_limited(
            candidate,
            max_file_bytes=max_file_bytes,
            allow_sensitive=allow_sensitive,
            display_path=rel,
        )
        if collected.skipped_reason:
            bundle.notes.append(f"skipped {rel}: {collected.skipped_reason}")
            continue

        size = len(collected.content.encode("utf-8", errors="replace"))
        if total + size > max_total_bytes:
            remaining = max_total_bytes - total
            if remaining > 0:
                collected.content = collected.content[:remaining]
                collected.truncated = True
            else:
                bundle.notes.append(f"total size limit reached; skipped {rel}")
                continue

        total += len(collected.content.encode("utf-8", errors="replace"))
        bundle.files.append(collected)

    return bundle


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"(git unavailable: {exc})"
    if result.returncode != 0 and not result.stdout and not result.stderr:
        return f"(git failed: {result.stderr.strip() or 'unknown error'})"
    output = result.stdout
    if result.stderr.strip():
        output = f"{output}\n{result.stderr}".strip()
    return output


def collect_git_diff(
    cwd: Path | None = None,
    collect_commands: list[str] | None = None,
) -> CollectionBundle:
    base = cwd or Path.cwd()
    commands = collect_commands or [
        "git status --short",
        "git diff --stat",
        "git diff",
    ]
    bundle = CollectionBundle(target="diff")

    for cmd in commands:
        parts = cmd.split()
        if not parts or parts[0] != "git":
            bundle.notes.append(f"skipped unsupported collect command: {cmd}")
            continue
        output = _run_git(parts[1:], base)
        bundle.files.append(
            CollectedFile(
                path=cmd,
                content=f"--- COMMAND: {cmd} ---\n{output}\n",
            )
        )

    return bundle


def bundle_to_text(bundle: CollectionBundle) -> str:
    sections: list[str] = []
    for f in bundle.files:
        if f.skipped_reason:
            continue
        sections.append(f"--- FILE: {f.path} ---\n{f.content.rstrip()}\n")
    if bundle.notes:
        sections.append("--- COLLECTION NOTES ---\n" + "\n".join(bundle.notes))
    return "\n".join(sections)
