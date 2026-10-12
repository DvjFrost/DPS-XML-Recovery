import json
from dataclasses import FrozenInstanceError

import pytest

from dps_gui.model import CompletionResult, RecoveryRequest
from dps_gui.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    WorkerEvent,
    encode_event,
    parse_message,
)


@pytest.mark.parametrize(
    ("message_type", "payload"),
    [
        ("preflight", {}),
        ("preflight", {"dll_path": r"C:\IIT\EUSignCP.dll"}),
        ("list_aliases", {"jks_path": r"D:\Ключі\key.jks"}),
        (
            "start",
            {
                "request": {
                    "mode": "full",
                    "root": r"D:\Архів ДПС",
                    "start_year": 2026,
                    "end_year": 2010,
                    "tax_id": "12345678",
                    "jks_path": r"D:\Ключі\key.jks",
                    "alias_index": 0,
                    "probe_year": 2026,
                    "probe_month": 7,
                    "delay": 0.25,
                    "root_fingerprint": "a" * 64,
                }
            },
        ),
        ("stop", {}),
        ("ping", {}),
    ],
)
def test_parse_message_accepts_known_commands(message_type, payload):
    line = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": "job-123",
            "type": message_type,
            "payload": payload,
        }
    )

    message = parse_message(line)

    assert message.type == message_type
    assert message.job_id == "job-123"
    if message_type == "start":
        assert isinstance(message.payload["request"], RecoveryRequest)
        assert message.payload["request"].mode == "full"


def test_parse_message_rejects_unknown_protocol_version():
    line = json.dumps(
        {"protocol_version": 99, "job_id": "job-1", "type": "ping", "payload": {}}
    )

    with pytest.raises(ProtocolError, match="protocol_version"):
        parse_message(line)


def test_parse_message_rejects_boolean_protocol_version():
    line = json.dumps(
        {"protocol_version": True, "job_id": "job-1", "type": "ping", "payload": {}}
    )

    with pytest.raises(ProtocolError, match="protocol_version"):
        parse_message(line)


def test_parse_message_rejects_unknown_type():
    line = json.dumps(
        {"protocol_version": 1, "job_id": "job-1", "type": "execute", "payload": {}}
    )

    with pytest.raises(ProtocolError, match="type"):
        parse_message(line)


def test_parse_message_rejects_non_string_type_as_protocol_error():
    line = json.dumps(
        {"protocol_version": 1, "job_id": "job-1", "type": [], "payload": {}}
    )

    with pytest.raises(ProtocolError, match="type"):
        parse_message(line)


def test_parse_message_rejects_extra_top_level_fields():
    line = json.dumps(
        {
            "protocol_version": 1,
            "job_id": "job-1",
            "type": "ping",
            "payload": {},
            "extra": True,
        }
    )

    with pytest.raises(ProtocolError, match="fields"):
        parse_message(line)


@pytest.mark.parametrize("secret_name", ["password", "authorization", "private_key", "token"])
def test_parse_message_rejects_nested_secret_fields(secret_name):
    line = json.dumps(
        {
            "protocol_version": 1,
            "job_id": "job-1",
            "type": "ping",
            "payload": {"nested": {secret_name: "must-not-cross-json"}},
        }
    )

    with pytest.raises(ProtocolError, match="forbidden"):
        parse_message(line)


def test_recovery_request_and_completion_result_are_immutable():
    request = RecoveryRequest(
        mode="audit_only",
        root=r"D:\Recovery",
        start_year=2026,
        end_year=2010,
    )
    result = CompletionResult(job_id="job-1", success=True, status="completed", exit_code=0)

    with pytest.raises(FrozenInstanceError):
        request.mode = "full"
    with pytest.raises(FrozenInstanceError):
        result.success = False


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("tax_id", 12345678),
        ("jks_path", ["key.jks"]),
        ("alias_index", "0"),
        ("probe_year", "2026"),
        ("probe_month", 7.0),
        ("delay", "0.25"),
    ],
)
def test_start_request_rejects_wrong_optional_field_types(field_name, invalid_value):
    request = {
        "mode": "full",
        "root": r"D:\Recovery",
        "start_year": 2026,
        "end_year": 2010,
        field_name: invalid_value,
    }
    line = json.dumps(
        {
            "protocol_version": 1,
            "job_id": "job-1",
            "type": "start",
            "payload": {"request": request},
        }
    )

    with pytest.raises(ProtocolError, match=field_name):
        parse_message(line)


def test_encode_event_is_jsonl_and_rejects_secret_payload():
    event = WorkerEvent(
        protocol_version=PROTOCOL_VERSION,
        job_id="job-1",
        timestamp="2026-10-11T12:00:00+03:00",
        type="worker_ready",
        payload={"pid": 123},
    )

    encoded = encode_event(event)

    assert encoded.endswith("\n")
    assert json.loads(encoded)["payload"] == {"pid": 123}

    unsafe = WorkerEvent(
        protocol_version=PROTOCOL_VERSION,
        job_id="job-1",
        timestamp="2026-10-11T12:00:00+03:00",
        type="failed",
        payload={"details": {"authorization": "secret"}},
    )
    with pytest.raises(ProtocolError, match="forbidden"):
        encode_event(unsafe)


def test_jsonl_event_is_ascii_safe_and_roundtrips_unicode():
    text = "Українська: ЄЇІє · кириллица · space 😀"
    encoded = encode_event(
        WorkerEvent(1, "job", "now", "warning", {"code": "unicode", "label": text})
    )

    assert encoded.isascii()
    assert json.loads(encoded)["payload"]["label"] == text
