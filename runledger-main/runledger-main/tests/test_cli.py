import pytest

from runledger import cli


def test_invalid_measure_exits_cleanly(capsys):
    # bogus measure kind must be rejected before running anything
    rc = cli.main(["run", "--measure", "bogus", "--", "true"])
    assert rc == 2
    assert "unknown measure kind" in capsys.readouterr().err


def test_invalid_objective_rejected_by_argparse():
    # CLI typo is caught at parse time (argparse choices -> SystemExit 2)
    with pytest.raises(SystemExit) as exc:
        cli.main(["sweep", "x.tsv", "--budget", "1", "--objective", "bogus", "--", "true"])
    assert exc.value.code == 2


def test_invalid_objective_from_config_does_not_run(tmp_path, monkeypatch, capsys):
    (tmp_path / "configs.tsv").write_text(
        "id\ttarget\tbin\tranks\tomp\trep\targs\n000\ts\ts\t1\t1\t1\t--a 1\n"
    )
    (tmp_path / ".runledger.toml").write_text('objective = "bogus"\n')
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["sweep", "configs.tsv", "--budget", "1", "--", "true"])
    assert rc == 2
    assert "unknown objective" in capsys.readouterr().err
    assert not (tmp_path / "sweeps").exists()  # nothing was executed
