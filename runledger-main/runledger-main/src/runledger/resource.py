"""Best-effort resource accounting via rusage (Unix). Empty elsewhere (§17).

v0.1 keeps this intentionally simple: capture cumulative child rusage before
and after a run and report the delta. On platforms without the ``resource``
module (e.g. Windows) we return ``{}`` rather than guessing.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - platform dependent
    import resource as _resource
except ImportError:  # pragma: no cover
    _resource = None


def supported() -> bool:
    return _resource is not None


def snapshot() -> Any | None:
    """Cumulative resource usage of child processes, or None if unsupported."""
    if _resource is None:
        return None
    return _resource.getrusage(_resource.RUSAGE_CHILDREN)


def delta(before: Any | None, after: Any | None) -> dict[str, Any]:
    if before is None or after is None:
        return {}
    return {
        "user_sec": round(after.ru_utime - before.ru_utime, 6),
        "sys_sec": round(after.ru_stime - before.ru_stime, 6),
        # ru_maxrss is a high-water mark across children, not a per-run delta;
        # reported best-effort. Units: KB on Linux, bytes on macOS.
        "maxrss": after.ru_maxrss,
    }
