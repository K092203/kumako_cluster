"""Schema constants, shared defaults, and TOML config loading."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

DEFAULT_CONFIG_NAMES = (".runledger.toml", "runledger.toml")

# --- outcome enumeration (meta.json "outcome") ---------------------------------
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_KILLED = "killed-or-incomplete"
OUTCOME_MISSING_BIN = "missing-bin"
OUTCOME_INVALID_CONFIG = "invalid-config"

# Conventional synthetic exit codes for non-process outcomes.
EXIT_TIMEOUT = 124
EXIT_MISSING_BIN = 127

# --- default layout ------------------------------------------------------------
RUNS_DIR = "runs"
SWEEPS_DIR = "sweeps"
STATE_DIR = "state"

# --- env capture allowlist (§15) ----------------------------------------------
# Exact names plus prefixes that are safe-by-default to record for reproducibility.
ENV_ALLOWLIST_EXACT = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "SLURM_JOB_ID",
        "PJM_JOBID",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
    }
)
ENV_ALLOWLIST_PREFIXES = ("OMP_", "SLURM_", "PJM_", "MV2_", "UCX_", "OMPI_")

# --- secret guard: never copy these as input (§15) ----------------------------
SENSITIVE_PATTERNS = (
    ".env",
    "*.pem",
    "*.key",
    "id_rsa",
    "*token*",
    "*secret*",
    ".netrc",
    "credentials.json",
)


@dataclass
class Config:
    """Optional defaults sourced from a .runledger.toml file."""

    runs_dir: str = RUNS_DIR
    sweeps_dir: str = SWEEPS_DIR
    state_dir: str = STATE_DIR
    objective: str = "max-score"
    measure: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        if paths := data.get("paths"):
            cfg.runs_dir = str(paths.get("runs_dir", cfg.runs_dir))
            cfg.sweeps_dir = str(paths.get("sweeps_dir", cfg.sweeps_dir))
            cfg.state_dir = str(paths.get("state_dir", cfg.state_dir))
        if "objective" in data:
            cfg.objective = str(data["objective"])
        if measure := data.get("measure"):
            cfg.measure = dict(measure)
        return cfg


def find_config_file(start: Path | None = None) -> Path | None:
    base = (start or Path.cwd()).resolve()
    for directory in [base, *base.parents]:
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
