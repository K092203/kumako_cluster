"""Generate configs.tsv from a space.tsv (§5.3, §6.3).

space.tsv columns (tab- or whitespace-separated):

    name   type    params...
    block  pow2    32   512
    alpha  float   0.1  2.0
    strategy choice greedy local beam

Methods: lhs (default), grid. Values are rounded by type, and duplicate
configs are removed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import product
from pathlib import Path

CONFIG_HEADER = ["id", "target", "bin", "ranks", "omp", "rep", "args"]


@dataclass
class Dimension:
    name: str
    kind: str  # pow2 | int | float | choice
    lo: float = 0.0
    hi: float = 0.0
    choices: list[str] | None = None


def parse_space(text: str) -> list[Dimension]:
    dims: list[Dimension] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"invalid space line (need name type params): {raw!r}")
        name, kind, *rest = parts
        if kind in {"pow2", "int", "float"}:
            if len(rest) < 2:
                raise ValueError(f"{kind} dimension '{name}' needs lo and hi")
            dims.append(Dimension(name=name, kind=kind, lo=float(rest[0]), hi=float(rest[1])))
        elif kind == "choice":
            dims.append(Dimension(name=name, kind="choice", choices=list(rest)))
        else:
            raise ValueError(f"unknown dimension type '{kind}' for '{name}'")
    if not dims:
        raise ValueError("space is empty")
    return dims


def _format_value(value: float | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _map_unit(dim: Dimension, u: float) -> float | str:
    """Map u in [0, 1) to a concrete value for the dimension."""
    if dim.kind == "choice":
        assert dim.choices
        idx = min(int(u * len(dim.choices)), len(dim.choices) - 1)
        return dim.choices[idx]
    if dim.kind == "float":
        return dim.lo + u * (dim.hi - dim.lo)
    if dim.kind == "int":
        return round(dim.lo + u * (dim.hi - dim.lo))
    if dim.kind == "pow2":
        lo_e = math.log2(dim.lo)
        hi_e = math.log2(dim.hi)
        exp = round(lo_e + u * (hi_e - lo_e))
        return 2**exp
    raise ValueError(f"unknown kind {dim.kind}")


def _lhs(n: int, ndim: int, rng: random.Random) -> list[list[float]]:
    columns: list[list[float]] = []
    for _ in range(ndim):
        strata = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(strata)
        columns.append(strata)
    return [[columns[d][i] for d in range(ndim)] for i in range(n)]


def _grid_values(dim: Dimension) -> list[float | str]:
    if dim.kind == "choice":
        assert dim.choices
        return list(dim.choices)
    if dim.kind == "pow2":
        lo_e = int(math.ceil(math.log2(dim.lo)))
        hi_e = int(math.floor(math.log2(dim.hi)))
        return [2**e for e in range(lo_e, hi_e + 1)]
    if dim.kind == "int":
        lo, hi = int(dim.lo), int(dim.hi)
        return list(range(lo, hi + 1))
    # float: endpoints + midpoint as a coarse grid
    return [dim.lo, (dim.lo + dim.hi) / 2.0, dim.hi]


def generate(
    dims: list[Dimension],
    *,
    n: int = 12,
    method: str = "lhs",
    seed: int | None = None,
) -> list[dict[str, str]]:
    """Return a list of {name: value-string} parameter assignments."""
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []

    if method == "lhs":
        for point in _lhs(n, len(dims), rng):
            rows.append(
                {dim.name: _format_value(_map_unit(dim, u)) for dim, u in zip(dims, point)}
            )
    elif method == "grid":
        value_lists = [[_format_value(v) for v in _grid_values(dim)] for dim in dims]
        for combo in product(*value_lists):
            rows.append({dim.name: val for dim, val in zip(dims, combo)})
    else:
        raise ValueError(f"unknown method '{method}'. Choose: lhs, grid")

    # de-duplicate while preserving order
    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def render_configs_tsv(
    rows: list[dict[str, str]],
    *,
    target: str = "solver",
    bin_name: str = "solver",
    ranks: int = 1,
    omp: int = 1,
    rep: int = 1,
) -> str:
    lines = ["\t".join(CONFIG_HEADER)]
    for i, row in enumerate(rows):
        args = " ".join(f"--{name} {value}" for name, value in row.items())
        lines.append(
            "\t".join(
                [f"{i:03d}", target, bin_name, str(ranks), str(omp), str(rep), args]
            )
        )
    return "\n".join(lines) + "\n"


def gen_configs_file(
    space_path: Path,
    *,
    n: int = 12,
    method: str = "lhs",
    seed: int | None = None,
    target: str = "solver",
    bin_name: str = "solver",
    ranks: int = 1,
    omp: int = 1,
    rep: int = 1,
) -> str:
    dims = parse_space(space_path.read_text(encoding="utf-8"))
    rows = generate(dims, n=n, method=method, seed=seed)
    return render_configs_tsv(
        rows, target=target, bin_name=bin_name, ranks=ranks, omp=omp, rep=rep
    )
