"""Canonical, encoding-sensitive contract for the user-selected recovery root."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path


class RecoveryRootError(RuntimeError):
    error_type = "RecoveryRootUnavailable"


class RecoveryRootMismatch(RecoveryRootError):
    error_type = "RecoveryRootMismatch"


def _canonical_text(value: Path | str, *, strict: bool) -> str:
    path = Path(value).expanduser().resolve(strict=strict)
    return os.path.normcase(str(path))


def recovery_root_fingerprint(value: Path | str) -> str:
    canonical = _canonical_text(value, strict=False)
    return hashlib.sha256(canonical.encode("utf-8", errors="strict")).hexdigest()


def verify_recovery_root(value: Path | str, expected_fingerprint: str | None) -> Path:
    actual_fingerprint = recovery_root_fingerprint(value)
    if not isinstance(expected_fingerprint, str) or not hmac.compare_digest(
        actual_fingerprint, expected_fingerprint
    ):
        raise RecoveryRootMismatch("recovery root confirmation mismatch")
    try:
        resolved = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        raise RecoveryRootError("recovery root is unavailable") from error
    if not resolved.is_dir():
        raise RecoveryRootError("recovery root is not a directory")
    return resolved


__all__ = [
    "RecoveryRootError",
    "RecoveryRootMismatch",
    "recovery_root_fingerprint",
    "verify_recovery_root",
]
