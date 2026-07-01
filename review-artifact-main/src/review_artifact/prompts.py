"""Prompt rendering from presets."""

from __future__ import annotations

from pathlib import Path

PRESET_NAMES = {
    "log_analysis",
    "experiment_analysis",
    "code_review",
}

COMMON_INSTRUCTION_EN = """You are a read-only reviewer. You cannot edit files; do not propose edits as if you can.
Only reference files and lines that were actually provided to you. If unsure, set file/line to null.
Focus on failure cause, risks, regressions, unclear assumptions, missing tests, and next actions.
Return concise findings with severity and evidence, plus a JSON findings block at the end.

Output format:
1. Markdown sections: ## Summary, ## Findings, ## Open Questions
2. A fenced ```json block with this schema:
{
  "summary": "short summary",
  "findings": [
    {
      "severity": "low|medium|high|critical|info",
      "title": "short title",
      "body": "evidence and explanation",
      "file": "path or null",
      "line": 123 or null,
      "evidence": "exact text copied verbatim from the cited line, or null",
      "confidence": "low|medium|high"
    }
  ],
  "open_questions": ["question 1"]
}

IMPORTANT: Do not invent file paths or line numbers. For every file/line you cite,
you MUST include `evidence`: text copied verbatim from that line of the provided
content. If you cannot copy exact text, set file, line, and evidence to null.
Citations whose evidence is not found in the provided content will be rejected.
"""

COMMON_INSTRUCTION_JA = """あなたは read-only の reviewer です。ファイルを編集できません。編集できるかのように提案しないでください。
提供されたファイルと行だけを参照してください。確信がなければ file/line は null にしてください。
失敗原因、リスク、リグレッション、不明瞭な前提、不足テスト、次のアクションに焦点を当ててください。
severity 付きの簡潔な findings と、末尾に JSON findings block を返してください。

出力形式:
1. Markdown セクション: ## Summary, ## Findings, ## Open Questions
2. フェンス付き ```json ブロック（上記スキーマ）

重要: ファイルパスや行番号を捏造しないでください。file/line を引用するときは必ず
`evidence`(その行から一字一句そのままコピーしたテキスト)を含めてください。正確に
コピーできない場合は file・line・evidence を null にしてください。提供された内容に
evidence が見つからない引用は却下されます。
"""


def _presets_dir() -> Path:
    return Path(__file__).resolve().parent / "presets"


def load_preset(name: str) -> str:
    if name not in PRESET_NAMES:
        available = ", ".join(sorted(PRESET_NAMES))
        raise ValueError(f"unknown prompt preset '{name}'. Available: {available}")
    path = _presets_dir() / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"preset not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(
    preset: str,
    *,
    language: str,
    bundle_text: str,
    user_question: str | None = None,
) -> str:
    preset_body = load_preset(preset)
    common = COMMON_INSTRUCTION_JA if language == "ja" else COMMON_INSTRUCTION_EN
    parts = [common.strip(), "", f"--- PRESET: {preset} ---", preset_body.strip()]

    if user_question:
        label = "ユーザーの質問" if language == "ja" else "User question"
        parts.extend(["", f"--- {label} ---", user_question.strip()])

    label = "収集された内容" if language == "ja" else "Collected content"
    parts.extend(["", f"--- {label} ---", bundle_text.strip()])
    return "\n".join(parts)
