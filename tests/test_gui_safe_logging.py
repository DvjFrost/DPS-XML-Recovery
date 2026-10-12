import json

from dps_gui.safe_logging import SafeOperationalLogger, export_diagnostic_report, sanitize_event


SECRETS = (
    "test-password-unique",
    "Bearer abc.def.ghi",
    "PRIVATE-KEY-BYTES",
    "proxy-user-secret",
    "<DECLAR><TIN>12345678</TIN></DECLAR>",
    "raw-response-body-secret",
)


def hostile_event():
    return {
        "phase": "download",
        "password": SECRETS[0],
        "nested": {
            "Authorization": SECRETS[1],
            "private_key": SECRETS[2].encode(),
            "proxy_credentials": SECRETS[3],
            "xml_content": SECRETS[4],
            "http_body": SECRETS[5],
        },
        "exception": f"request failed password={SECRETS[0]} Authorization: {SECRETS[1]} body={SECRETS[4]}",
        "count": 17,
    }


def assert_secret_free(text: str):
    for secret in SECRETS:
        assert secret not in text
    assert "12345678" not in text


def test_sanitize_event_recursively_removes_secrets_and_keeps_safe_fields():
    result = sanitize_event(hostile_event())
    encoded = json.dumps(result, ensure_ascii=False)
    assert_secret_free(encoded)
    assert result["phase"] == "download"
    assert result["count"] == 17


def test_operational_jsonl_and_text_logs_are_secret_free(tmp_path):
    logger = SafeOperationalLogger(tmp_path / "операційні логи")
    logger.write("worker_event", hostile_event())
    assert_secret_free(logger.jsonl_path.read_text(encoding="utf-8"))
    assert_secret_free(logger.text_path.read_text(encoding="utf-8"))


def test_exported_support_report_is_atomic_and_secret_free(tmp_path):
    target = tmp_path / "Звіт діагностики.json"
    export_diagnostic_report(
        target,
        app={"version": "test", "authorization": SECRETS[1]},
        settings={"remember_jks": True, "jks_path": r"D:\Secret\key.jks", "password": SECRETS[0]},
        events=[hostile_event()],
    )
    text = target.read_text(encoding="utf-8")
    assert_secret_free(text)
    document = json.loads(text)
    assert document["app"]["version"] == "test"
    assert not list(tmp_path.glob("*.tmp"))
