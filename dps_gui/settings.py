"""Allowlisted, atomic and secret-free GUI settings."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


MODES = frozenset({"full", "inventory_only", "audit_only", "probe_only", "reindex_and_audit"})


@dataclass(frozen=True)
class GUISettings:
    language: str = "ru"
    tax_id: str = ""
    start_year: int = 2020
    end_year: int = 2026
    recovery_root: str = ""
    mode: str = "full"
    remember_jks: bool = False
    jks_path: str | None = None


def _parse(document: Mapping[str, Any]) -> GUISettings:
    language = document.get("language", "ru")
    tax_id = document.get("tax_id", "")
    start_year = document.get("start_year", 2020)
    end_year = document.get("end_year", 2026)
    recovery_root = document.get("recovery_root", "")
    mode = document.get("mode", "full")
    remember_jks = document.get("remember_jks", False) is True
    jks_path = document.get("jks_path") if remember_jks else None
    if language not in {"ru", "uk"}:
        language = "ru"
    if not isinstance(tax_id, str):
        tax_id = ""
    if type(start_year) is not int or start_year < 2010:
        start_year = 2020
    if type(end_year) is not int or end_year < start_year:
        end_year = max(start_year, 2026)
    if not isinstance(recovery_root, str):
        recovery_root = ""
    if mode not in MODES:
        mode = "full"
    if not isinstance(jks_path, str) or not jks_path:
        jks_path = None
    return GUISettings(
        language, tax_id, start_year, end_year, recovery_root,
        mode, remember_jks, jks_path,
    )


def load_settings(path: Path) -> GUISettings:
    path = Path(path)
    if not path.is_file():
        return GUISettings()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return _parse(document) if isinstance(document, dict) else GUISettings()
    except (OSError, UnicodeError, json.JSONDecodeError):
        return GUISettings()


def _document(settings: GUISettings) -> dict[str, Any]:
    result = asdict(settings)
    if not settings.remember_jks:
        result.pop("jks_path", None)
    return result


def save_settings_atomic(settings: GUISettings, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(_document(settings), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


__all__ = ["GUISettings", "load_settings", "save_settings_atomic"]
