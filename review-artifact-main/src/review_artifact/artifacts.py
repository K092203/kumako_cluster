"""Artifact writers for Markdown and JSON."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from review_artifact.backend import BackendResult
from review_artifact.collect import CollectionBundle
from review_artifact.findings import ParsedReview


@dataclass
class RepoInfo:
    root: str
    commit: str | None
    dirty: bool | None


@dataclass
class ArtifactPaths:
    markdown: Path | None
    json: Path | None


def get_repo_info(cwd: Path) -> RepoInfo:
    root = str(cwd.resolve())
    commit = None
    dirty = None
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if top.returncode == 0:
            root = top.stdout.strip()
            rev = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if rev.returncode == 0:
                commit = rev.stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if status.returncode == 0:
                dirty = bool(status.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return RepoInfo(root=root, commit=commit, dirty=dirty)


def artifact_basename(target: str, when: datetime | None = None) -> str:
    ts = (when or datetime.now(timezone.utc)).astimezone()
    stamp = ts.strftime("%Y%m%d-%H%M%S")
    safe_target = target.replace("/", "-").replace(" ", "_")
    return f"{stamp}-{safe_target}"


def write_artifacts(
    *,
    target: str,
    prompt_name: str,
    bundle: CollectionBundle,
    parsed: ParsedReview,
    backend_result: BackendResult,
    repo: RepoInfo,
    output_dir: Path,
    formats: list[str],
    command_label: str,
) -> ArtifactPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = artifact_basename(target)
    md_path = output_dir / f"{base}.md"
    json_path = output_dir / f"{base}.json"

    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    json_doc: dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at,
        "target": target,
        "backend": {
            "name": backend_result.backend_name,
            "command": backend_result.command,
        },
        "repo": {
            "root": repo.root,
            "commit": repo.commit,
            "dirty": repo.dirty,
        },
        "prompt": prompt_name,
        "command": command_label,
        "summary": parsed.summary,
        "findings": [f.to_dict() for f in parsed.findings],
        "open_questions": parsed.open_questions,
        "raw_output": parsed.raw_output,
        "artifacts": {},
    }

    md_text = render_markdown(
        created_at=created_at,
        target=target,
        prompt_name=prompt_name,
        command_label=command_label,
        backend_result=backend_result,
        repo=repo,
        parsed=parsed,
    )

    paths = ArtifactPaths(markdown=None, json=None)

    if "markdown" in formats:
        md_path.write_text(md_text, encoding="utf-8")
        json_doc["artifacts"]["markdown"] = str(md_path)
        paths.markdown = md_path

    if "json" in formats:
        if paths.markdown is not None:
            json_doc["artifacts"]["markdown"] = str(paths.markdown)
        json_path.write_text(json.dumps(json_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        json_doc["artifacts"]["json"] = str(json_path)
        paths.json = json_path

        # rewrite JSON with cross-references if both formats written
        if paths.markdown is not None:
            json_path.write_text(
                json.dumps(json_doc, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    return paths


def render_markdown(
    *,
    created_at: str,
    target: str,
    prompt_name: str,
    command_label: str,
    backend_result: BackendResult,
    repo: RepoInfo,
    parsed: ParsedReview,
) -> str:
    lines = [
        "# Review Artifact",
        "",
        "## Metadata",
        f"- created_at: {created_at}",
        f"- target: {target}",
        f"- prompt: {prompt_name}",
        f"- command: {command_label}",
        f"- backend: {backend_result.backend_name} ({backend_result.command})",
        f"- repo: {repo.root}",
        f"- git_commit: {repo.commit or 'n/a'}",
        f"- git_dirty: {repo.dirty if repo.dirty is not None else 'n/a'}",
        "",
        "## Summary",
        parsed.summary or "(no summary extracted)",
        "",
        "## Findings",
    ]

    if parsed.findings:
        for f in parsed.findings:
            loc = ""
            if f.file:
                loc = f" ({f.file}"
                if f.line is not None:
                    status = "verified" if f.line_verified else "unverified"
                    if f.line_relocated:
                        status += ", relocated"
                    loc += f":{f.line}, {status}"
                elif f.evidence:
                    loc += ", citation rejected: evidence not found"
                loc += ")"
            lines.append(f"- **{f.severity}**: {f.title}{loc}")
            if f.body:
                lines.append(f"  {f.body}")
            if f.evidence and f.line_verified:
                lines.append(f"  evidence: `{f.evidence}`")
    else:
        lines.append("(no structured findings extracted)")

    lines.extend(["", "## Open Questions"])
    if parsed.open_questions:
        for q in parsed.open_questions:
            lines.append(f"- {q}")
    else:
        lines.append("(none)")

    lines.extend(["", "## Raw Reviewer Output", "", parsed.raw_output.rstrip(), ""])
    return "\n".join(lines)
