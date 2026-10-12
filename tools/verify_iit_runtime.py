"""Fail-closed preflight for the official IIT Python wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProbeReport:
    compatible: bool
    status: str
    missing: tuple[str, ...] = ()


def probe_runtime(python_exe: Path, pyd: Path, dll: Path) -> ProbeReport:
    """Report missing official runtime files without loading any key material."""
    missing = tuple(str(path) for path in (python_exe, pyd, dll) if not path.is_file())
    if missing:
        return ProbeReport(False, "missing_files", missing)
    return ProbeReport(False, "unverified_runtime")
