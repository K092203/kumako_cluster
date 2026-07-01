from runledger.measure import (
    FILE,
    JSON,
    NONE,
    REGEX,
    TUNE_LINE,
    MeasureSpec,
    extract_measure,
)


def test_tune_line(tmp_path):
    spec = MeasureSpec(kind=TUNE_LINE)
    r = extract_measure(
        spec,
        stdout="",
        stderr="noise\n#TUNE elapsed=1.5 score=42 correct=1\n",
        exit_code=0,
        base=tmp_path,
    )
    assert r.elapsed == 1.5
    assert r.score == 42.0
    assert r.correct is True


def test_tune_line_last_wins(tmp_path):
    spec = MeasureSpec(kind=TUNE_LINE)
    r = extract_measure(
        spec,
        stdout="",
        stderr="#TUNE score=1 correct=1\n#TUNE score=2 correct=0\n",
        exit_code=0,
        base=tmp_path,
    )
    assert r.score == 2.0
    assert r.correct is False


def test_regex(tmp_path):
    spec = MeasureSpec(
        kind=REGEX,
        stream="stdout",
        elapsed_re=r"time:\s*([0-9.]+)",
        score_re=r"best=([0-9.]+)",
        correct_re="OK",
    )
    r = extract_measure(
        spec, stdout="time: 2.5\nbest=99.0\nOK\n", stderr="", exit_code=0, base=tmp_path
    )
    assert r.elapsed == 2.5
    assert r.score == 99.0
    assert r.correct is True


def test_json(tmp_path):
    (tmp_path / "result.json").write_text('{"elapsed":3.0,"score":7.0,"correct":true}')
    spec = MeasureSpec(kind=JSON, path="result.json")
    r = extract_measure(spec, stdout="", stderr="", exit_code=0, base=tmp_path)
    assert r.elapsed == 3.0
    assert r.score == 7.0
    assert r.correct is True


def test_file(tmp_path):
    (tmp_path / "score.txt").write_text("123.5\n")
    spec = MeasureSpec(kind=FILE, path="score.txt")
    r = extract_measure(spec, stdout="", stderr="", exit_code=0, base=tmp_path)
    assert r.score == 123.5


def test_none_extracts_nothing(tmp_path):
    spec = MeasureSpec(kind=NONE)
    r = extract_measure(spec, stdout="#TUNE score=5", stderr="", exit_code=0, base=tmp_path)
    assert r.score is None
    assert r.source == "none"


def test_nonzero_exit_not_trusted(tmp_path):
    spec = MeasureSpec(kind=TUNE_LINE)
    r = extract_measure(
        spec,
        stdout="",
        stderr="#TUNE elapsed=1 score=9 correct=1",
        exit_code=1,
        base=tmp_path,
    )
    assert r.elapsed is None
    assert r.score is None


def test_from_cli_compact_forms():
    assert MeasureSpec.from_cli("json:out.json").path == "out.json"
    assert MeasureSpec.from_cli("file:s.txt").kind == FILE
    assert MeasureSpec.from_cli(None).kind == TUNE_LINE
    assert MeasureSpec.from_cli("none").kind == NONE


def test_from_cli_table_regex():
    table = {"kind": "regex", "stream": "stdout", "score": "best=([0-9.]+)"}
    spec = MeasureSpec.from_cli(None, table)
    assert spec.kind == REGEX
    assert spec.score_re == "best=([0-9.]+)"
