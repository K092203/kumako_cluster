"""Configuration loading and defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAMES = (".review-artifact.toml", "review-artifact.toml")


@dataclass
class BackendConfig:
    name: str = "llm"
    command: str = "llm"
    prompt_stdin: bool = True
    timeout: int = 300


@dataclass
class OutputsConfig:
    dir: str = ".review"
    formats: list[str] = field(default_factory=lambda: ["markdown", "json"])


@dataclass
class TargetConfig:
    kind: str = "directory"
    path: str = ""
    prompt: str = "experiment_analysis"
    include: list[str] = field(default_factory=list)
    collect: list[str] = field(default_factory=list)


@dataclass
class Config:
    language: str = "ja"
    backend: BackendConfig = field(default_factory=BackendConfig)
    outputs: OutputsConfig = field(default_factory=OutputsConfig)
    targets: dict[str, TargetConfig] = field(default_factory=dict)
    max_file_bytes: int = 256 * 1024
    max_total_bytes: int = 1024 * 1024

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        cfg = cls()
        if "language" in data:
            cfg.language = str(data["language"])

        if backend := data.get("backend"):
            cfg.backend = BackendConfig(
                name=backend.get("name", cfg.backend.name),
                command=backend.get("command", cfg.backend.command),
                prompt_stdin=backend.get("prompt_stdin", cfg.backend.prompt_stdin),
                timeout=int(backend.get("timeout", cfg.backend.timeout)),
            )

        if outputs := data.get("outputs"):
            cfg.outputs = OutputsConfig(
                dir=outputs.get("dir", cfg.outputs.dir),
                formats=list(outputs.get("formats", cfg.outputs.formats)),
            )

        if limits := data.get("limits"):
            cfg.max_file_bytes = int(limits.get("max_file_bytes", cfg.max_file_bytes))
            cfg.max_total_bytes = int(limits.get("max_total_bytes", cfg.max_total_bytes))

        targets: dict[str, TargetConfig] = {}
        for key, value in data.get("targets", {}).items():
            if not isinstance(value, dict):
                continue
            targets[key] = TargetConfig(
                kind=value.get("kind", "directory"),
                path=value.get("path", ""),
                prompt=value.get("prompt", "experiment_analysis"),
                include=list(value.get("include", [])),
                collect=list(value.get("collect", [])),
            )
        cfg.targets = targets
        return cfg

    def merge_cli(
        self,
        *,
        language: str | None = None,
        backend: str | None = None,
        backend_command: str | None = None,
        out_dir: str | None = None,
        formats: list[str] | None = None,
    ) -> Config:
        import copy

        merged = copy.deepcopy(self)
        if language is not None:
            merged.language = language
        if backend is not None:
            merged.backend.name = backend
        if backend_command is not None:
            merged.backend.command = backend_command
        if out_dir is not None:
            merged.outputs.dir = out_dir
        if formats is not None:
            merged.outputs.formats = formats
        return merged


def find_config_file(start: Path | None = None) -> Path | None:
    cwd = (start or Path.cwd()).resolve()
    for directory in [cwd, *cwd.parents]:
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_config(path: Path | None = None) -> Config:
    config_path = path or find_config_file()
    if config_path is None:
        return Config()
    with config_path.open("rb") as fh:
        data = tomllib.load(fh)
    return Config.from_dict(data)
