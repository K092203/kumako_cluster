"""CLI entry point for review-artifact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_artifact.artifacts import get_repo_info, write_artifacts
from review_artifact.backend import BackendError, create_backend
from review_artifact.collect import (
    bundle_to_text,
    collect_directory_logs,
    collect_files,
    collect_git_diff,
)
from review_artifact.config import load_config
from review_artifact.findings import parse_review_output
from review_artifact.prompts import render_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-artifact",
        description=(
            "Read-only AI triage for experiment artifacts (logs, job outputs), "
            "plus diffs and files. Saves review as Markdown/JSON."
        ),
    )
    _add_shared_arguments(parser)

    sub = parser.add_subparsers(dest="command", required=True)

    logs = sub.add_parser("logs", help="Triage experiment/job logs in a directory (primary)")
    _add_shared_arguments(logs)
    logs.add_argument("directory", type=Path, help="Directory with job artifacts")

    diff = sub.add_parser("diff", help="Review git diff (secondary)")
    _add_shared_arguments(diff)

    files = sub.add_parser("files", help="Review specific files")
    _add_shared_arguments(files)
    files.add_argument("paths", nargs="+", type=Path, help="Files to review")

    ask = sub.add_parser("ask", help="Free-form question with optional files")
    _add_shared_arguments(ask)
    ask.add_argument("question", help="Question for the reviewer")
    ask.add_argument("--files", nargs="*", type=Path, default=[], help="Optional context files")

    return parser


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to .review-artifact.toml (default: search upward)",
    )
    parser.add_argument(
        "--backend",
        choices=["llm", "codex", "custom", "fake"],
        help="Backend adapter (default: from config or llm)",
    )
    parser.add_argument(
        "--backend-command",
        help="Override backend command (e.g. 'llm -m gpt-4o')",
    )
    parser.add_argument(
        "--prompt",
        help="Prompt preset: log_analysis, experiment_analysis, code_review",
    )
    parser.add_argument(
        "--language",
        choices=["ja", "en"],
        help="Reviewer language (default: ja)",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=["markdown", "json"],
        help="Output format (repeatable). Default: markdown,json",
    )
    parser.add_argument(
        "--out",
        dest="out_dir",
        help="Output directory (default: .review)",
    )
    parser.add_argument(
        "--include",
        action="append",
        dest="includes",
        help="Include file name for logs command (repeatable)",
    )
    parser.add_argument(
        "--allow-sensitive",
        action="store_true",
        help="Allow collecting sensitive-looking files (.env, *.pem, etc.)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print collected bundle and prompt without calling backend",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print rendered prompt before calling backend",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    cfg = cfg.merge_cli(
        language=args.language,
        backend=args.backend,
        backend_command=args.backend_command,
        out_dir=args.out_dir,
        formats=args.formats,
    )

    cwd = Path.cwd()
    target, prompt_name, bundle = _resolve_command(args, cfg, cwd)
    bundle_text = bundle_to_text(bundle)

    prompt = render_prompt(
        prompt_name,
        language=cfg.language,
        bundle_text=bundle_text,
        user_question=getattr(args, "question", None),
    )

    if args.dry_run:
        print("=== COLLECTED BUNDLE ===")
        print(bundle_text)
        print("\n=== PROMPT ===")
        print(prompt)
        return 0

    if args.print_prompt:
        print(prompt)
        print("---")

    if not bundle.files and not bundle_text.strip():
        print("warning: nothing collected; proceeding with empty bundle", file=sys.stderr)

    try:
        backend = create_backend(cfg.backend)
        result = backend.run(prompt, cwd)
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parsed = parse_review_output(result.output, bundle)
    repo = get_repo_info(cwd)
    command_label = _command_label(args)

    paths = write_artifacts(
        target=target,
        prompt_name=prompt_name,
        bundle=bundle,
        parsed=parsed,
        backend_result=result,
        repo=repo,
        output_dir=Path(cfg.outputs.dir),
        formats=cfg.outputs.formats,
        command_label=command_label,
    )

    if paths.markdown:
        print(f"wrote {paths.markdown}")
    if paths.json:
        print(f"wrote {paths.json}")
    return 0


def _resolve_command(args, cfg, cwd: Path):
    if args.command == "logs":
        includes = args.includes
        target_cfg = cfg.targets.get("results_latest")
        if includes is None and target_cfg and target_cfg.include:
            includes = target_cfg.include
        prompt = args.prompt or (target_cfg.prompt if target_cfg else None) or "experiment_analysis"
        bundle = collect_directory_logs(
            args.directory,
            include=includes,
            max_file_bytes=cfg.max_file_bytes,
            max_total_bytes=cfg.max_total_bytes,
            allow_sensitive=args.allow_sensitive,
            cwd=cwd,
        )
        target = f"logs-{args.directory}"
        return target, prompt, bundle

    if args.command == "diff":
        target_cfg = cfg.targets.get("diff")
        collect_cmds = target_cfg.collect if target_cfg and target_cfg.collect else None
        prompt = args.prompt or (target_cfg.prompt if target_cfg else None) or "code_review"
        bundle = collect_git_diff(cwd, collect_commands=collect_cmds)
        return "diff", prompt, bundle

    if args.command == "files":
        prompt = args.prompt or "code_review"
        bundle = collect_files(
            args.paths,
            max_file_bytes=cfg.max_file_bytes,
            max_total_bytes=cfg.max_total_bytes,
            allow_sensitive=args.allow_sensitive,
            cwd=cwd,
        )
        target = "files-" + "-".join(str(p) for p in args.paths[:3])
        return target, prompt, bundle

    if args.command == "ask":
        prompt = args.prompt or "log_analysis"
        if args.files:
            bundle = collect_files(
                args.files,
                max_file_bytes=cfg.max_file_bytes,
                max_total_bytes=cfg.max_total_bytes,
                allow_sensitive=args.allow_sensitive,
                cwd=cwd,
            )
        else:
            from review_artifact.collect import CollectionBundle

            bundle = CollectionBundle(target="ask")
        return "ask", prompt, bundle

    raise SystemExit(f"unknown command: {args.command}")


def _command_label(args) -> str:
    if args.command == "logs":
        return f"review-artifact logs {args.directory}"
    if args.command == "diff":
        return "review-artifact diff"
    if args.command == "files":
        return "review-artifact files " + " ".join(str(p) for p in args.paths)
    if args.command == "ask":
        parts = [f'review-artifact ask "{args.question}"']
        if args.files:
            parts.append("--files " + " ".join(str(p) for p in args.files))
        return " ".join(parts)
    return "review-artifact"


if __name__ == "__main__":
    raise SystemExit(main())
