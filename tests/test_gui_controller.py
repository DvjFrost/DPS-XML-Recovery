from dataclasses import dataclass
from pathlib import Path

import pytest

from dps_gui.controller import AppState, InputValues, RecoveryController
from dps_gui.model import WorkerEvent


class FakeWorker:
    def __init__(self):
        self.started = []
        self.stop_calls = 0
        self.events = []
        self.is_running = False

    def start(self, request, password):
        self.started.append((request, bytes(password)))
        self.is_running = True

    def request_stop(self):
        self.stop_calls += 1

    def force_terminate(self):
        self.is_running = False

    def poll_events(self):
        events, self.events = self.events, []
        return events


def event(kind, payload):
    return WorkerEvent(1, "job", "now", kind, payload)


def valid_values(tmp_path: Path, *, mode="full"):
    jks = tmp_path / "ключ тест.jks"
    jks.write_bytes(b"fake")
    root = tmp_path / "XML архів"
    root.mkdir()
    return InputValues("12345678", str(jks), 0, 2020, 2026, str(root), mode)


def test_start_requires_successful_preflight_and_valid_input(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    assert not controller.can_start
    assert not controller.needs_password
    controller.set_preflight(False, "ІІТ не готовий")
    assert not controller.needs_password
    controller.set_preflight(True, "Готово")
    assert controller.can_start
    assert controller.needs_password


def test_user_period_is_normalized_to_recovery_engine_descending_years(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")

    controller.start(bytearray(b"pw"))

    request = worker.started[0][0]
    assert request.start_year == 2026
    assert request.end_year == 2020


def test_audit_skips_password_and_can_run_without_preflight(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    values = valid_values(tmp_path, mode="audit_only")
    controller.set_inputs(InputValues("", "", None, 2020, 2026, values.recovery_root, "audit_only"))
    assert controller.can_start
    assert not controller.needs_password


def test_double_start_is_rejected_and_stop_is_cooperative(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    password = bytearray(b"test-password")
    controller.start(password)
    assert controller.state is AppState.RUNNING
    assert password == bytearray(len(password))
    with pytest.raises(RuntimeError, match="уже выполняется"):
        controller.start(bytearray(b"again"))
    assert controller.request_stop(False) is False
    assert controller.request_stop(True) is True
    assert controller.state is AppState.STOPPING
    assert worker.stop_calls == 1


def test_terminal_pass_requires_completed_audit_pass(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    controller.start(bytearray(b"pw"))

    worker.events = [event("completed", {"status": "inventory_completed", "summary": {"audit_pass": False}})]
    controller.poll()
    assert controller.state is AppState.RESULT
    assert controller.result_kind == "INFO"

    worker.is_running = False
    controller.reset_result()
    controller.start(bytearray(b"pw"))
    worker.events = [event("completed", {"status": "audit_pass", "summary": {"audit_pass": True, "primary_valid": 5, "related_valid": 7, "invalid_files": 0, "part_files": 0}})]
    controller.poll()
    assert controller.result_kind == "PASS"
    assert "Основных XML: 5" in controller.result_details
    assert "Связанных XML: 7" in controller.result_details

    worker.is_running = False
    controller.reset_result()
    controller.start(bytearray(b"pw"))
    worker.events = [event("failed", {"status": "audit_failed", "summary": {"audit_pass": False}})]
    controller.poll()
    assert controller.result_kind == "FAIL"


def test_safe_worker_error_type_is_visible_and_exportable(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    controller.start(bytearray(b"pw"))

    worker.events = [
        event(
            "failed",
            {"status": "failed", "summary": {"error_type": "ValueError"}},
        )
    ]
    controller.poll()

    assert "ValueError" in controller.result_details
    assert any("ValueError" in line for line in controller.log_lines)


def test_progress_burst_is_coalesced_and_log_bounded(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026, log_limit=200)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    controller.start(bytearray(b"pw"))
    worker.events = [event("progress", {"current": i, "total": 10_000, "phase": "download"}) for i in range(10_000)]
    changed = controller.poll()
    assert changed <= 202
    assert controller.progress_current == 9_999
    assert len(controller.log_lines) <= 200


def test_progress_coalescing_preserves_phase_order_and_real_counters(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    controller.start(bytearray(b"pw"))
    worker.events = [
        event("progress", {"phase": "inventory", "current": 24, "total": 24, "unique_documents": 135}),
        event("phase_started", {"phase": "download"}),
        event("progress", {"phase": "download", "current": 10, "total": 135}),
    ]

    controller.poll()

    assert controller.log_lines[-3:] == [
        "inventory: 24/24",
        "Этап: download",
        "download: 10/135",
    ]
    assert controller.progress_phase == "download"
    assert controller.progress_current == 10
    assert controller.progress_total == 135
    assert all("0/0" not in line for line in controller.log_lines)


def test_unknown_progress_counters_are_not_rendered_as_zero_ratio(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    controller.start(bytearray(b"pw"))
    worker.events = [event("progress", {"phase": "reindex", "valid": 3, "invalid": 0})]

    controller.poll()

    assert controller.log_lines[-1] == "reindex"
    assert "0/0" not in controller.log_lines[-1]


def test_diagnostics_keep_only_structured_safe_worker_fields(tmp_path):
    worker = FakeWorker()
    controller = RecoveryController(worker, current_year=2026)
    controller.set_inputs(valid_values(tmp_path))
    controller.set_preflight(True, "Готово")
    controller.start(bytearray(b"pw"))
    worker.events = [
        event("warning", {"code": "retry_scheduled", "raw": "secret raw body"}),
        event("progress", {"phase": "inventory", "current": 2, "total": 24, "records_seen": 18}),
        event("failed", {"status": "inventory_failed", "summary": {"error_type": "APIError", "http_body": "secret"}}),
    ]

    controller.poll()

    encoded = __import__("json").dumps(controller.diagnostic_events)
    assert "retry_scheduled" in encoded
    assert '"current": 2' in encoded
    assert "inventory_failed" in encoded
    assert "APIError" in encoded
    assert "secret" not in encoded
