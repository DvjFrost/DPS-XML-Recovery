from __future__ import annotations

import io
import json
import queue
import threading
from pathlib import Path

from dps_gui.model import CompletionResult, RecoveryRequest
from dps_gui.protocol import PROTOCOL_VERSION
from dps_gui.path_contract import recovery_root_fingerprint
from dps_gui.worker_main import worker_main
from dps_gui import worker_main as worker_module
from dps_gui.worker_runtime import DefaultRuntimeServices, StopToken, run_request
from tests.fakes.fake_recovery_runtime import FakeServices


def request(mode="full", root=None):
    root = str(Path.cwd() if root is None else root)
    return RecoveryRequest(
        mode=mode,
        root=root,
        start_year=2026,
        end_year=2010,
        tax_id="12345678",
        jks_path=r"D:\Ключи\test.jks",
        alias_index=1,
        probe_year=2026,
        probe_month=7,
        delay=0.25,
        root_fingerprint=recovery_root_fingerprint(root),
    )


def run(mode="full", services=None, stop=None):
    events = []
    services = services or FakeServices()
    result = run_request(
        request(mode),
        lambda event_type, payload: events.append((event_type, payload)),
        stop or StopToken(),
        services=services,
        job_id="job-1",
    )
    return result, services, events


def names(services):
    return [call[0] for call in services.calls]


def test_audit_only_never_opens_credentials_or_network():
    result, services, events = run("audit_only")

    assert result.success
    assert result.status == "audit_pass"
    assert "credential_enter" not in names(services)
    assert "probe" not in names(services)
    assert "inventory" not in names(services)
    assert names(services).count("audit") == 1


def test_reindex_and_audit_maps_to_reindex_then_audit_without_credentials():
    result, services, _ = run("reindex_and_audit")

    assert result.success
    assert names(services).index("archive_reindex") < names(services).index("audit")
    open_state = next(call for call in services.calls if call[0] == "open_state")
    assert open_state[2] is True
    assert "credential_enter" not in names(services)


def test_probe_only_stops_after_authenticated_probe():
    result, services, _ = run("probe_only")

    assert result.success
    assert result.status == "probe_completed"
    assert "credential_enter" in names(services)
    assert "probe" in names(services)
    assert "make_engine" not in names(services)
    assert "inventory" not in names(services)


def test_inventory_only_uses_existing_component_options_and_never_claims_audit_pass():
    result, services, events = run("inventory_only")

    assert result.success
    assert result.status == "inventory_completed"
    assert result.summary.get("audit_pass") is not True
    assert "download" not in names(services)
    assert "audit" not in names(services)
    engine_call = next(call for call in services.calls if call[0] == "make_engine")
    assert engine_call[-2:] == (2026, 2010)
    credential_call = next(call for call in services.calls if call[0] == "credential_enter")
    assert credential_call[-1] == 0.25
    assert ("probe", 2026, 7) in services.calls


def test_default_services_constructs_dps_client_with_existing_delay_option(monkeypatch):
    captured = {}

    def fake_client(authorization, *, request_delay, event_callback):
        captured.update(
            authorization=authorization,
            request_delay=request_delay,
            event_callback=event_callback,
        )
        return object()

    monkeypatch.setattr("dps_gui.worker_runtime.DPSClient", fake_client)

    DefaultRuntimeServices().make_client("safe-test-authorization", request())

    assert captured["authorization"] == "safe-test-authorization"
    assert captured["request_delay"] == 0.25
    assert callable(captured["event_callback"])


def test_inventory_error_prevents_download_and_returns_failure():
    services = FakeServices()
    services.inventory_stats["errors"] = 2

    result, services, _ = run("full", services)

    assert not result.success
    assert result.status == "inventory_failed"
    assert "download" not in names(services)
    assert "audit" not in names(services)


def test_full_recovery_requires_clean_final_audit():
    services = FakeServices()
    services.audit_clean = False

    result, services, _ = run("full", services)

    assert not result.success
    assert result.status == "audit_failed"
    assert "download" in names(services)
    assert "write_audit_report" in names(services)


def test_inventory_stop_occurs_after_current_page_checkpoint():
    services = FakeServices()
    services.inventory_progress = True
    stop = StopToken()
    services.before_inventory_progress = stop.request_stop

    result, services, events = run("full", services, stop)

    assert not result.success
    assert result.status == "stopped"
    assert "inventory_page_committed" in names(services)
    assert "download" not in names(services)
    assert "credential_exit" in names(services)
    assert "state_exit" in names(services)


def test_inventory_progress_uses_bounded_period_counter():
    services = FakeServices()
    services.inventory_progress = True

    _, _, events = run("inventory_only", services)

    progress = next(payload for kind, payload in events if kind == "progress")
    assert progress["current"] == 1
    assert progress["total"] == 204
    assert progress["records_seen"] == 9


def test_download_progress_maps_primary_processed_to_total():
    services = FakeServices()

    _, _, events = run("full", services)

    progress = next(payload for kind, payload in events if kind == "progress")
    assert progress["current"] == 1
    assert progress["total"] == 4


def test_download_stop_occurs_after_current_parent_checkpoint():
    services = FakeServices()
    stop = StopToken()
    services.before_download_progress = stop.request_stop

    result, services, events = run("full", services, stop)

    assert not result.success
    assert result.status == "stopped"
    assert "download_parent_committed" in names(services)
    assert "audit" not in names(services)
    assert "credential_exit" in names(services)
    assert "state_exit" in names(services)


class BlockingControl:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self):
        return self.lines.get(timeout=2)

    def send(self, message):
        self.lines.put(json.dumps(message) + "\n")

    def close(self):
        self.lines.put("")


def message(kind, payload=None):
    return {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": "job-live",
        "type": kind,
        "payload": payload or {},
    }


def test_command_reader_sets_stop_while_request_runner_is_blocked():
    control = BlockingControl()
    output = io.StringIO()
    runner_started = threading.Event()
    stop_seen = threading.Event()

    def blocked_runner(req, emit, stop, **kwargs):
        runner_started.set()
        assert stop.wait(2)
        stop_seen.set()
        return CompletionResult("job-live", False, "stopped", 130)

    thread = threading.Thread(
        target=worker_main,
        args=(control, output, -1),
        kwargs={"request_runner": blocked_runner},
    )
    thread.start()
    control.send(message("start", {"request": request().__dict__}))
    assert runner_started.wait(2)
    control.send(message("stop"))
    assert stop_seen.wait(2)
    thread.join(2)
    control.close()

    assert not thread.is_alive()
    event_types = [json.loads(line)["type"] for line in output.getvalue().splitlines()]
    assert event_types[-2:] == ["stopping", "failed"]
    assert "completed" not in event_types


def test_frozen_worker_explicitly_reconfigures_standard_streams_to_utf8(monkeypatch):
    calls = []

    class Stream:
        def reconfigure(self, **options):
            calls.append(options)

    input_stream = Stream()
    output_stream = Stream()
    monkeypatch.setattr(worker_module.sys, "stdin", input_stream)
    monkeypatch.setattr(worker_module.sys, "stdout", output_stream)
    monkeypatch.setattr(worker_module, "worker_main", lambda source, target, handle: 0)

    assert worker_module.run_worker(123) == 0
    assert calls == [
        {"encoding": "utf-8", "errors": "strict"},
        {"encoding": "utf-8", "errors": "strict"},
    ]
