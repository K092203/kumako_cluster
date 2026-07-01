"""Tests for findings parsing and line verification."""

from review_artifact.collect import CollectionBundle, CollectedFile
from review_artifact.findings import (
    Finding,
    extract_json_block,
    parse_review_output,
    verify_finding,
)


SAMPLE_OUTPUT = """## Summary
The job timed out.

```json
{
  "summary": "The job timed out.",
  "findings": [
    {
      "severity": "medium",
      "title": "Timeout",
      "body": "stderr shows deadline",
      "file": "results/latest/stderr.txt",
      "line": 42,
      "confidence": "medium"
    },
    {
      "severity": "low",
      "title": "Fabricated",
      "body": "nowhere",
      "file": "missing.txt",
      "line": 10,
      "confidence": "low"
    }
  ],
  "open_questions": ["Was walltime enough?"]
}
```
"""


def _stderr_bundle() -> CollectionBundle:
    lines = "\n".join(f"line {i}" for i in range(1, 50))
    return CollectionBundle(
        target="logs",
        files=[
            CollectedFile(
                path="results/latest/stderr.txt",
                content=lines,
            )
        ],
    )


def test_extract_json_block():
    data = extract_json_block(SAMPLE_OUTPUT)
    assert data is not None
    assert data["summary"] == "The job timed out."
    assert len(data["findings"]) == 2


def test_line_verification_valid():
    bundle = _stderr_bundle()
    parsed = parse_review_output(SAMPLE_OUTPUT, bundle)
    timeout = next(f for f in parsed.findings if f.title == "Timeout")
    assert timeout.line == 42
    assert timeout.line_verified is True


def test_line_verification_invalid_file():
    bundle = _stderr_bundle()
    parsed = parse_review_output(SAMPLE_OUTPUT, bundle)
    fabricated = next(f for f in parsed.findings if f.title == "Fabricated")
    assert fabricated.file is None
    assert fabricated.line is None
    assert fabricated.line_verified is False


def test_line_verification_out_of_range():
    bundle = CollectionBundle(
        target="files",
        files=[CollectedFile(path="a.txt", content="one line\n")],
    )
    finding = Finding(
        severity="info",
        title="OOR",
        body="test",
        file="a.txt",
        line=99,
    )
    verified = verify_finding(finding, {"a.txt": ["one line"]})
    assert verified.line is None
    assert verified.line_verified is False


def test_evidence_corroborated_verifies():
    finding = Finding(
        severity="info", title="E", body="b", file="a.txt", line=2, evidence="needle"
    )
    verified = verify_finding(finding, {"a.txt": ["first", "has needle here", "third"]})
    assert verified.line == 2
    assert verified.line_verified is True
    assert verified.line_relocated is False


def test_evidence_relocates_to_real_line():
    # cited line is wrong, but the quoted evidence is real -> auto-correct the line
    finding = Finding(
        severity="info", title="E", body="b", file="a.txt", line=1, evidence="needle"
    )
    verified = verify_finding(finding, {"a.txt": ["first", "second", "has needle here"]})
    assert verified.line == 3
    assert verified.line_verified is True
    assert verified.line_relocated is True


def test_evidence_not_found_is_rejected():
    # plausible, in-range line, but the quoted evidence does not exist -> rejected
    finding = Finding(
        severity="info",
        title="E",
        body="b",
        file="a.txt",
        line=2,
        evidence="Segmentation fault (core dumped)",
    )
    verified = verify_finding(finding, {"a.txt": ["first", "second", "third"]})
    assert verified.line is None
    assert verified.line_verified is False
    assert verified.file == "a.txt"  # the real file is kept; only the bad line is dropped


def test_parse_without_json_fallback():
    raw = "## Summary\n\nSomething failed.\n\n## Open Questions\n\n- Why?"
    bundle = CollectionBundle(target="ask")
    parsed = parse_review_output(raw, bundle)
    assert "failed" in parsed.summary.lower() or parsed.summary
    assert parsed.findings == []
