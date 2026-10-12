"""Explicit portable-data resolution without profile-directory fallbacks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class PortableDataError(RuntimeError):
    pass


def _ensure_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(prefix=".write-probe-", dir=path)
        os.close(descriptor)
        Path(probe).unlink()
        return True
    except OSError:
        return False


def resolve_portable_data(executable_dir: Path, override: Path | None) -> Path:
    """Return an explicit writable portable-data path or fail closed."""
    executable_dir = Path(executable_dir).expanduser().resolve()
    target = (
        Path(override).expanduser().resolve()
        if override is not None
        else executable_dir / "portable-data"
    )
    if not _ensure_writable(target):
        if override is None:
            raise PortableDataError(
                "Каталог portable-data рядом с программой недоступен для записи. "
                "Выберите другой каталог явно."
            )
        raise PortableDataError("Выбранный каталог portable-data недоступен для записи.")
    return target


__all__ = ["PortableDataError", "resolve_portable_data"]
