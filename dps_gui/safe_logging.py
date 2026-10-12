"""Secret-safe operational logging and support-report export."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REDACTED = "[СКРЫТО]"
SENSITIVE_KEY_PARTS = (
    "password", "passwd", "passphrase", "authorization", "privatekey",
    "private_key", "secret", "token", "proxy", "xml", "httpbody",
    "http_body", "rawbody", "raw_body", "jks_path", "keybytes",
)
ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|authorization|token|proxy(?:_credentials)?)\s*[:=]\s*(?:bearer\s+|basic\s+)?[^\s,;]+"
)
AUTHORIZATION = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
XML_LIKE = re.compile(r"<\??[A-Za-z][^>]*>")


def _key_sensitive(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _sanitize_string(value: str) -> str:
    if XML_LIKE.search(value):
        return REDACTED
    value = ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", value)
    return AUTHORIZATION.sub(REDACTED, value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _key_sensitive(str(key)) else _sanitize(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(child) for child in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED
    if isinstance(value, BaseException):
        return _sanitize_string(str(value))
    if isinstance(value, str):
        return _sanitize_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_string(str(value))


def sanitize_event(value: Any) -> dict[str, Any]:
    sanitized = _sanitize(value)
    return sanitized if isinstance(sanitized, dict) else {"event": sanitized}


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
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


class SafeOperationalLogger:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.directory / "operational.jsonl"
        self.text_path = self.directory / "operational.log"

    def write(self, event_type: str, payload: Any) -> None:
        document = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": _sanitize_string(str(event_type)),
            "payload": sanitize_event(payload),
        }
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
        with self.text_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{document['timestamp']} {document['type']} {encoded}\n")


def export_diagnostic_report(
    path: Path,
    *,
    app: Mapping[str, Any],
    settings: Mapping[str, Any],
    events: Sequence[Any],
) -> None:
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": sanitize_event(app),
        "settings": sanitize_event(settings),
        "events": [sanitize_event(item) for item in events],
        "safety": {"secrets_redacted": True, "xml_content_excluded": True},
    }
    _atomic_json(Path(path), document)


__all__ = [
    "SafeOperationalLogger",
    "export_diagnostic_report",
    "sanitize_event",
]
