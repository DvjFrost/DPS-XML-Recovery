from __future__ import annotations

import json

import pytest

from dps_gui.acceptance_runtime import acceptance_request_runner
from dps_gui.model import RecoveryRequest
from dps_gui.path_contract import recovery_root_fingerprint
from dps_gui.worker_runtime import StopToken


def request(root, mode="full"):
    return RecoveryRequest(
        mode=mode,
        root=str(root),
        start_year=2026,
        end_year=2025,
        root_fingerprint=recovery_root_fingerprint(root),
    )


def scenario(root, name):
    (root / "acceptance-scenario.json").write_text(
        json.dumps({"scenario": name}), encoding="utf-8"
    )


def run(root, name, mode="full", stop=None):
    scenario(root, name)
    events = []
    result = acceptance_request_runner(
        request(root, mode),
        lambda kind, payload: events.append((kind, payload)),
        stop or StopToken(),
        job_id="acceptance",
    )
    return result, events


@pytest.mark.parametrize(
    ("name", "status", "error_type"),
    [
        ("wrong_password", "credential_failed", "CredentialSessionError"),
        ("authorization_failed", "authorization_failed", "AuthFatalError"),
        ("corrupt_state", "state_corrupt", "DatabaseError"),
        ("partial_xml", "audit_failed", "InvalidXML"),
    ],
)
def test_failure_scenarios_never_return_pass(tmp_path, name, status, error_type):
    result, events = run(tmp_path, name)

    assert not result.success
    assert result.status == status
    assert result.summary["audit_pass"] is False
    assert result.summary["error_type"] == error_type
    assert all(payload.get("audit_pass") is not True for _, payload in events)


def test_modes_have_expected_terminal_semantics(tmp_path):
    full, full_events = run(tmp_path, "happy", "full")
    inventory, inventory_events = run(tmp_path, "happy", "inventory_only")
    audit, audit_events = run(tmp_path, "happy", "audit_only")

    assert full.success and full.status == "audit_pass" and full.summary["audit_pass"] is True
    assert inventory.success and inventory.status == "inventory_completed"
    assert inventory.summary["audit_pass"] is False
    assert audit.success and audit.status == "audit_pass"
    assert any(kind == "progress" and payload["phase"] == "inventory" for kind, payload in full_events)
    assert not any(payload.get("phase") == "download" for _, payload in inventory_events)
    assert not any(payload.get("phase") == "inventory" for _, payload in audit_events)


def test_stop_checkpoint_then_resume_finishes_without_restarting(tmp_path):
    stop = StopToken()
    stop.request_stop()
    stopped, _ = run(tmp_path, "stop_resume", stop=stop)

    resumed, events = run(tmp_path, "stop_resume")

    assert stopped.status == "stopped"
    assert resumed.status == "audit_pass"
    first_progress = next(payload for kind, payload in events if kind == "progress")
    assert first_progress["current"] == 2
    assert json.loads((tmp_path / "acceptance-state.json").read_text(encoding="utf-8"))["complete"] is True


def test_partial_download_creates_only_test_part_and_reports_it(tmp_path):
    result, _ = run(tmp_path, "partial_xml")

    assert not result.success
    assert result.summary["part_files"] == 1
    assert list(tmp_path.glob("*.part"))
