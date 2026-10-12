"""Strict, secret-free JSON Lines protocol for GUI/worker communication."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from .model import ControlMessage, RecoveryRequest, WorkerEvent


PROTOCOL_VERSION = 1

CONTROL_TYPES = frozenset({"preflight", "list_aliases", "start", "stop", "ping"})
EVENT_TYPES = frozenset(
    {
        "worker_ready",
        "preflight",
        "aliases",
        "phase_started",
        "progress",
        "warning",
        "stopping",
        "completed",
        "failed",
    }
)
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "password",
        "authorization",
        "private_key",
        "token",
        "http_body",
        "raw_http_body",
        "xml_content",
    }
)
REQUEST_FIELDS = frozenset(
    {
        "mode",
        "root",
        "start_year",
        "end_year",
        "tax_id",
        "jks_path",
        "alias_index",
        "probe_year",
        "probe_month",
        "delay",
        "root_fingerprint",
    }
)
REQUEST_REQUIRED_FIELDS = frozenset({"mode", "root", "start_year", "end_year"})
RECOVERY_MODES = frozenset(
    {"full", "inventory_only", "audit_only", "probe_only", "reindex_and_audit"}
)


class ProtocolError(ValueError):
    """A message violates the versioned IPC contract."""


def _reject_forbidden_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProtocolError("object fields must be strings")
            if key.casefold() in FORBIDDEN_FIELD_NAMES:
                raise ProtocolError(f"forbidden field: {key}")
            _reject_forbidden_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_forbidden_fields(child)


def _expect_exact_fields(value: Mapping[str, Any], expected: set[str] | frozenset[str]) -> None:
    actual = frozenset(value)
    if actual != frozenset(expected):
        raise ProtocolError(
            f"invalid fields: expected {sorted(expected)}, received {sorted(actual)}"
        )


def _parse_request(value: Any) -> RecoveryRequest:
    if not isinstance(value, dict):
        raise ProtocolError("start.request must be an object")
    unknown = frozenset(value) - REQUEST_FIELDS
    missing = REQUEST_REQUIRED_FIELDS - frozenset(value)
    if unknown or missing:
        raise ProtocolError(
            f"invalid request fields: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if not isinstance(value["mode"], str) or value["mode"] not in RECOVERY_MODES:
        raise ProtocolError("unknown recovery mode")
    if not isinstance(value["root"], str) or not value["root"]:
        raise ProtocolError("request.root must be a non-empty string")
    for field_name in ("start_year", "end_year"):
        if not isinstance(value[field_name], int) or isinstance(value[field_name], bool):
            raise ProtocolError(f"request.{field_name} must be an integer")
    for field_name in ("tax_id", "jks_path"):
        field_value = value.get(field_name)
        if field_value is not None and not isinstance(field_value, str):
            raise ProtocolError(f"request.{field_name} must be a string or null")
    alias_index = value.get("alias_index")
    if alias_index is not None and (
        not isinstance(alias_index, int) or isinstance(alias_index, bool)
    ):
        raise ProtocolError("request.alias_index must be an integer or null")
    for field_name in ("probe_year", "probe_month"):
        if field_name in value and (
            not isinstance(value[field_name], int) or isinstance(value[field_name], bool)
        ):
            raise ProtocolError(f"request.{field_name} must be an integer")
    if "delay" in value and (
        not isinstance(value["delay"], (int, float)) or isinstance(value["delay"], bool)
    ):
        raise ProtocolError("request.delay must be a number")
    root_fingerprint = value.get("root_fingerprint")
    if root_fingerprint is not None and (
        not isinstance(root_fingerprint, str)
        or len(root_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in root_fingerprint)
    ):
        raise ProtocolError("request.root_fingerprint must be lowercase SHA-256")
    try:
        return RecoveryRequest(**value)
    except TypeError as error:
        raise ProtocolError("invalid recovery request") from error


def _parse_payload(message_type: str, payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")
    _reject_forbidden_fields(payload)
    if message_type in {"stop", "ping"}:
        _expect_exact_fields(payload, set())
        return {}
    if message_type == "preflight":
        if frozenset(payload) not in {frozenset(), frozenset({"dll_path"})}:
            raise ProtocolError("invalid fields for preflight")
        dll_path = payload.get("dll_path")
        if dll_path is not None and (not isinstance(dll_path, str) or not dll_path):
            raise ProtocolError("preflight.dll_path must be a non-empty string or null")
        return dict(payload)
    if message_type == "list_aliases":
        _expect_exact_fields(payload, {"jks_path"})
        if not isinstance(payload["jks_path"], str) or not payload["jks_path"]:
            raise ProtocolError("list_aliases.jks_path must be a non-empty string")
        return dict(payload)
    if message_type == "start":
        _expect_exact_fields(payload, {"request"})
        return {"request": _parse_request(payload["request"])}
    raise ProtocolError("unknown control message type")


def parse_message(line: str) -> ControlMessage:
    try:
        document = json.loads(line)
    except (TypeError, json.JSONDecodeError) as error:
        raise ProtocolError("message is not valid JSON") from error
    if not isinstance(document, dict):
        raise ProtocolError("message must be a JSON object")
    _expect_exact_fields(document, {"protocol_version", "job_id", "type", "payload"})
    _reject_forbidden_fields(document)
    if type(document["protocol_version"]) is not int or document["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol_version")
    if not isinstance(document["job_id"], str) or not document["job_id"]:
        raise ProtocolError("job_id must be a non-empty string")
    if not isinstance(document["type"], str) or document["type"] not in CONTROL_TYPES:
        raise ProtocolError("unknown control message type")
    payload = _parse_payload(document["type"], document["payload"])
    return ControlMessage(
        protocol_version=PROTOCOL_VERSION,
        job_id=document["job_id"],
        type=document["type"],
        payload=payload,
    )


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(child) for key, child in asdict(value).items()}
    if isinstance(value, Mapping):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def encode_event(event: WorkerEvent) -> str:
    if event.protocol_version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol_version")
    if event.type not in EVENT_TYPES:
        raise ProtocolError("unknown worker event type")
    if not event.job_id or not event.timestamp:
        raise ProtocolError("event job_id and timestamp must be non-empty")
    document = {
        "protocol_version": event.protocol_version,
        "job_id": event.job_id,
        "timestamp": event.timestamp,
        "type": event.type,
        "payload": _json_value(event.payload),
    }
    _reject_forbidden_fields(document)
    return json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"


__all__ = [
    "PROTOCOL_VERSION",
    "ProtocolError",
    "WorkerEvent",
    "encode_event",
    "parse_message",
]
