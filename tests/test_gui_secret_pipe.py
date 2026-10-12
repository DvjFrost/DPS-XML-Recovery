from __future__ import annotations

import ctypes
import io
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from dps_gui.model import RecoveryRequest
from dps_gui.model import CompletionResult
from dps_gui.protocol import PROTOCOL_VERSION
from dps_gui.path_contract import recovery_root_fingerprint
from dps_gui import worker_main as worker_module
from dps_gui.secret_pipe import (
    CredentialSessionError,
    SecretPipe,
    credential_session,
    read_secret,
    wipe_bytearray,
)


HANDLE_FLAG_INHERIT = 0x00000001


def _handle_flags(handle: int) -> int:
    flags = ctypes.c_uint32()
    function = ctypes.windll.kernel32.GetHandleInformation
    function.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    function.restype = ctypes.c_int32
    if not function(ctypes.c_void_p(handle), ctypes.byref(flags)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(flags.value)


def _request() -> RecoveryRequest:
    root = str(Path.cwd())
    return RecoveryRequest(
        mode="full",
        root=root,
        start_year=2026,
        end_year=2010,
        tax_id="12345678",
        jks_path=r"D:\Keys\test.jks",
        alias_index=0,
        root_fingerprint=recovery_root_fingerprint(root),
    )


def test_secret_pipe_roundtrip_is_length_prefixed_and_wipes_parent_buffer():
    pipe = SecretPipe.create()
    assert _handle_flags(pipe.read_handle) & HANDLE_FLAG_INHERIT
    assert not (_handle_flags(pipe.write_handle) & HANDLE_FLAG_INHERIT)
    received = []
    reader = threading.Thread(target=lambda: received.append(read_secret(pipe.read_handle)))
    reader.start()
    secret = bytearray(b"TEST-ONLY-PIPE-PASSWORD")

    pipe.send(secret)
    reader.join(2)

    assert not reader.is_alive()
    assert secret == bytearray(len(secret))
    assert received == [bytearray(b"TEST-ONLY-PIPE-PASSWORD")]
    wipe_bytearray(received[0])
    assert received[0] == bytearray(len(received[0]))
    pipe.close()


def test_secret_pipe_wipes_parent_buffer_when_write_fails():
    pipe = SecretPipe.create()
    pipe.close_parent_read()
    secret = bytearray(b"TEST-ONLY-BROKEN-PIPE")

    with pytest.raises(OSError):
        pipe.send(secret)

    assert secret == bytearray(len(secret))
    pipe.close()


class FakeAPI:
    def __init__(self, calls, fail=False):
        self.calls = calls
        self.fail = fail

    def __enter__(self):
        self.calls.append("api_enter")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.calls.append("api_exit")

    def list_jks_aliases(self, path):
        self.calls.append(("aliases", path))
        return [b"alias-0"]

    def read_jks_key(self, path, alias, password):
        self.calls.append(("read_jks_key", path, alias, type(password), password))
        if self.fail:
            raise RuntimeError(f"native failure containing {password}")


class FakeClient:
    def __init__(self, calls):
        self.calls = calls

    def close(self):
        self.calls.append("client_close")


def test_credential_session_uses_temporary_string_and_wipes_worker_buffer():
    marker = bytearray(b"TEST-ONLY-WORKER-PASSWORD")
    calls = []
    client = FakeClient(calls)

    with credential_session(
        _request(),
        123,
        secret_reader=lambda handle: marker,
        api_factory=lambda: FakeAPI(calls),
        authorization_factory=lambda api, tax_id: ("safe-authorization", "Signer"),
        client_factory=lambda authorization, request: client,
    ) as yielded:
        assert yielded is client
        read_call = next(item for item in calls if isinstance(item, tuple) and item[0] == "read_jks_key")
        assert read_call[3] is str
        assert read_call[4] == "TEST-ONLY-WORKER-PASSWORD"

    assert marker == bytearray(len(marker))
    assert calls[-2:] == ["client_close", "api_exit"]


def test_credential_session_wipes_worker_buffer_and_redacts_native_error():
    marker = bytearray(b"TEST-ONLY-EXCEPTION-PASSWORD")
    calls = []

    with pytest.raises(CredentialSessionError) as error:
        with credential_session(
            _request(),
            123,
            secret_reader=lambda handle: marker,
            api_factory=lambda: FakeAPI(calls, fail=True),
            authorization_factory=lambda api, tax_id: ("unused", None),
            client_factory=lambda authorization, request: FakeClient(calls),
        ):
            pass

    assert marker == bytearray(len(marker))
    assert "TEST-ONLY" not in str(error.value)
    assert error.value.__cause__ is None
    assert calls[-1] == "api_exit"


def test_worker_main_wires_secret_handle_to_injected_credential_session(monkeypatch):
    calls = []

    @contextmanager
    def fake_credentials(request, handle, *, client_factory):
        calls.append((handle, request.alias_index, callable(client_factory)))
        yield object()

    monkeypatch.setattr(worker_module, "credential_session", fake_credentials)
    request_value = _request()
    message = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": "job-secret",
            "type": "start",
            "payload": {"request": request_value.__dict__},
        }
    )
    output = io.StringIO()

    def runner(request, emit, stop, *, services, job_id):
        with services.credential_session(request):
            return CompletionResult(job_id, True, "completed", 0)

    assert worker_module.worker_main(
        io.StringIO(message + "\n"), output, 444, request_runner=runner
    ) == 0
    assert calls == [(444, 0, True)]
    assert "password" not in output.getvalue().casefold()
