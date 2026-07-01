"""Tests for configuration."""

from pathlib import Path

from review_artifact.config import Config, load_config


def test_config_defaults():
    cfg = Config()
    assert cfg.language == "ja"
    assert cfg.backend.name == "llm"
    assert cfg.outputs.dir == ".review"
    assert "markdown" in cfg.outputs.formats


def test_config_from_dict():
    data = {
        "language": "en",
        "backend": {"name": "fake", "command": "fake"},
        "outputs": {"dir": "out", "formats": ["json"]},
        "targets": {
            "results_latest": {
                "kind": "directory",
                "path": "results/latest",
                "prompt": "experiment_analysis",
                "include": ["meta.json"],
            }
        },
    }
    cfg = Config.from_dict(data)
    assert cfg.language == "en"
    assert cfg.backend.name == "fake"
    assert cfg.outputs.formats == ["json"]
    assert "results_latest" in cfg.targets
    assert cfg.targets["results_latest"].include == ["meta.json"]


def test_load_config_from_file(tmp_path):
    config_file = tmp_path / ".review-artifact.toml"
    config_file.write_text(
        """
language = "en"
[backend]
name = "fake"
[outputs]
dir = "artifacts"
"""
    )
    cfg = load_config(config_file)
    assert cfg.language == "en"
    assert cfg.backend.name == "fake"
    assert cfg.outputs.dir == "artifacts"


def test_merge_cli_overrides():
    cfg = Config()
    merged = cfg.merge_cli(language="en", backend="fake", out_dir="out")
    assert merged.language == "en"
    assert merged.backend.name == "fake"
    assert merged.outputs.dir == "out"
    # original unchanged
    assert cfg.language == "ja"
