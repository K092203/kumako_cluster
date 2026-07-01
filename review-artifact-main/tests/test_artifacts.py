"""Tests for artifact writers."""

import json
from pathlib import Path

from review_artifact.artifacts import artifact_basename, get_repo_info, write_artifacts
from review_artifact.backend import BackendResult
from review_artifact.collect import CollectionBundle, CollectedFile
from review_artifact.findings import Finding, ParsedReview


def test_artifact_basename_format():
    name = artifact_basename("logs-results")
    assert name.endswith("-logs-results")
    assert len(name.split("-")[0]) == 8  # YYYYMMDD


def test_write_markdown_and_json(tmp_path):
    bundle = CollectionBundle(
        target="logs",
        files=[CollectedFile(path="stderr.txt", content="error\n")],
    )
    parsed = ParsedReview(
        summary="Job failed",
        findings=[
            Finding(
                severity="high",
                title="Timeout",
                body="deadline reached",
                file="stderr.txt",
                line=1,
                line_verified=True,
            )
        ],
        open_questions=["Increase walltime?"],
        raw_output="raw",
    )
    backend = BackendResult(output="raw", command="fake", backend_name="fake")
    repo = get_repo_info(tmp_path)

    paths = write_artifacts(
        target="logs-test",
        prompt_name="experiment_analysis",
        bundle=bundle,
        parsed=parsed,
        backend_result=backend,
        repo=repo,
        output_dir=tmp_path / ".review",
        formats=["markdown", "json"],
        command_label="review-artifact logs test",
    )

    assert paths.markdown is not None
    assert paths.json is not None
    md = paths.markdown.read_text(encoding="utf-8")
    assert "## Summary" in md
    assert "Timeout" in md

    doc = json.loads(paths.json.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert doc["summary"] == "Job failed"
    assert doc["findings"][0]["line_verified"] is True
    assert "markdown" in doc["artifacts"]
