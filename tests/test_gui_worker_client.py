from __future__ import annotations

import json
import queue
import subprocess
import time

import pytest

from dps_gui.model import RecoveryRequest, WorkerEvent
from dps_gui.protocol import PROTOCOL_VERSION, encode_event
from dps_gui.worker_client import (
    WorkerAlreadyRunning,
    WorkerClient,
    WorkerProtocolError,
    WorkerStartError,
)


SECRET = b"TEST-ONLY-CLIENT-PASSWORD-9f48"


def request():
    return RecoveryRequest(
        mode="full",
        root=r"D:\Архів з пробілами\Ї Є І 😀",
        start_year=2026,
        end_year=2010,
        tax_id="12345678",
        jks_path=r"D:\Keys\test.jks",
        alias_index=0,
        root_fingerprint="b" * 64,
    )


def event(kind, payload=None):
    return encode_event(
        WorkerEvent(
            protocol_version=PROTOCOL_VERSION,
            job_id="worker" if kind == "worker_ready" else "job-test",
            timestamp="2026-10-12T00:00:00+00:00",
            type=kind,
            payload=payload or {},
        )
    )


class QueueReader:
    def __init__(self, lines=()):
        self.lines = queue.Queue()
        for line in lines:
            self.lines.put(line)

    def readline(self):
        return self.lines.get(timeout=2)

    def feed(self, line):
        self.lines.put(line)


class RecordingWriter:
    def __init__(self):
        self.text = ""
        self.closed = False

    def write(self, value):
        if self.closed:
            raise OSError("closed")
        self.text += value

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stdout_lines=(), *, timeout_on_wait=False):
        self.stdin = RecordingWriter()
        self.stdout = QueueReader(stdout_lines)
        self.stderr = QueueReader()
        self.returncode = None
        self.timeout_on_wait = timeout_on_wait
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.timeout_on_wait and self.returncode is None:
            raise subprocess.TimeoutExpired("fake-worker", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if not self.timeout_on_wait:
            self.returncode = 143
        self.stdout.feed("")
        self.stderr.feed("")

    def kill(self):
        self.kill_calls += 1
        self.returncode = 137
        self.stdout.feed("")
        self.stderr.feed("")

    def crash(self, code=9):
        self.returncode = code
        self.stdout.feed("")
        self.stderr.feed("")


class FakeSecretPipe:
    def __init__(self, process):
        self.process = process
        self.read_handle = 444
        self.write_handle = 555
        self.sent = False
        self.parent_read_closed = False
        self.closed = False
        self.control_ready_before_send = False

    def send(self, value):
        self.control_ready_before_send = bool(self.process.stdin.text)
        self.sent = bytes(value) == SECRET
        for index in range(len(value)):
            value[index] = 0
        self.write_handle = 0

    def close_parent_read(self):
        self.parent_read_closed = True
        self.read_handle = 0

    def close(self):
        self.closed = True


class Harness:
    def __init__(self, process):
        self.process = process
        self.process_call = None
        self.pipe = FakeSecretPipe(process)

    def popen(self, command, **kwargs):
        self.process_call = (list(command), dict(kwargs))
        return self.process

    def create_pipe(self):
        return self.pipe

    def client(self, **kwargs):
        return WorkerClient(
            command=[r"C:\Portable\DPSXmlRecovery.exe"],
            process_factory=self.popen,
            secret_pipe_factory=self.create_pipe,
            ready_timeout=kwargs.pop("ready_timeout", 0.3),
            terminate_timeout=kwargs.pop("terminate_timeout", 0.01),
            job_id_factory=lambda: "job-test",
            **kwargs,
        )


def test_start_uses_only_secret_handle_and_wipes_password():
    harness = Harness(FakeProcess([event("worker_ready")]))
    client = harness.client()
    password = bytearray(SECRET)

    client.start(request(), password)

    command, options = harness.process_call
    combined = json.dumps({"command": command, "options": str(options), "stdin": harness.process.stdin.text})
    assert SECRET.decode() not in combined
    assert command == [
        r"C:\Portable\DPSXmlRecovery.exe",
        "--worker",
        "--protocol-version",
        str(PROTOCOL_VERSION),
        "--secret-handle",
        "444",
    ]
    assert options["close_fds"] is True
    assert options["startupinfo"].lpAttributeList == {"handle_list": [444]}
    assert "env" not in options
    assert password == bytearray(len(password))
    assert harness.pipe.sent
    assert harness.pipe.control_ready_before_send
    assert harness.pipe.parent_read_closed
    start_message = json.loads(harness.process.stdin.text)
    assert start_message["type"] == "start"
    assert "password" not in harness.process.stdin.text.casefold()
    assert harness.process.stdin.text.isascii()
    assert start_message["payload"]["request"]["root"] == request().root


def test_duplicate_start_is_rejected():
    harness = Harness(FakeProcess([event("worker_ready")]))
    client = harness.client()
    client.start(request(), bytearray(SECRET))

    second_password = bytearray(SECRET)
    with pytest.raises(WorkerAlreadyRunning):
        client.start(request(), second_password)
    assert second_password == bytearray(len(second_password))


def test_terminal_event_does_not_allow_second_job_before_process_exit():
    process = FakeProcess([event("worker_ready")])
    harness = Harness(process)
    client = harness.client()
    client.start(request(), bytearray(SECRET))
    process.stdout.feed(event("completed", {"status": "completed", "exit_code": 0}))
    time.sleep(0.02)
    client.poll_events()

    second_password = bytearray(SECRET)
    with pytest.raises(WorkerAlreadyRunning):
        client.start(request(), second_password)

    assert second_password == bytearray(len(second_password))


def test_ready_timeout_terminates_worker_and_wipes_password():
    process = FakeProcess()
    harness = Harness(process)
    client = harness.client(ready_timeout=0.02)
    password = bytearray(SECRET)

    with pytest.raises(WorkerStartError) as error:
        client.start(request(), password)

    assert process.terminate_calls == 1
    assert password == bytearray(len(password))
    assert SECRET.decode() not in str(error.value)


def test_malformed_event_fails_closed_without_echoing_worker_output():
    process = FakeProcess([SECRET.decode() + " not-json\n"])
    harness = Harness(process)
    client = harness.client()

    with pytest.raises(WorkerProtocolError) as error:
        client.start(request(), bytearray(SECRET))

    assert SECRET.decode() not in str(error.value)
    assert process.terminate_calls == 1


def test_request_stop_uses_control_channel_and_force_terminate_kills_hung_worker():
    process = FakeProcess([event("worker_ready")], timeout_on_wait=True)
    harness = Harness(process)
    client = harness.client()
    client.start(request(), bytearray(SECRET))

    client.request_stop()
    stop_message = json.loads(process.stdin.text.splitlines()[-1])
    assert stop_message["type"] == "stop"
    client.force_terminate()

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert not client.is_running


def test_worker_crash_becomes_safe_failed_event_and_client_remains_usable():
    process = FakeProcess([event("worker_ready")])
    harness = Harness(process)
    client = harness.client()
    client.start(request(), bytearray(SECRET))
    client.poll_events()

    process.crash(17)
    time.sleep(0.02)
    events = client.poll_events()

    assert any(item.type == "failed" and item.payload["status"] == "worker_crashed" for item in events)
    assert not client.is_running
    assert SECRET.decode() not in json.dumps([dict(item.payload) for item in events])


def test_malformed_event_after_ready_terminates_job_without_echoing_payload():
    process = FakeProcess([event("worker_ready")])
    harness = Harness(process)
    client = harness.client()
    client.start(request(), bytearray(SECRET))
    client.poll_events()

    process.stdout.feed(SECRET.decode() + " malformed-event\n")
    time.sleep(0.02)
    events = client.poll_events()

    assert process.terminate_calls == 1
    assert any(item.type == "failed" and item.payload["status"] == "invalid_worker_event" for item in events)
    assert SECRET.decode() not in json.dumps([dict(item.payload) for item in events])
    assert not client.is_running


def test_close_requests_stop_then_forces_hung_worker():
    process = FakeProcess([event("worker_ready")], timeout_on_wait=True)
    harness = Harness(process)
    client = harness.client()
    client.start(request(), bytearray(SECRET))

    client.close()

    messages = [json.loads(line) for line in process.stdin.text.splitlines()]
    assert messages[-1]["type"] == "stop"
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert not client.is_running
