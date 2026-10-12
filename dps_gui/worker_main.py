"""JSONL command loop for the isolated recovery worker."""

from __future__ import annotations

import queue
import sys
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, TextIO

from .model import CompletionResult, RecoveryRequest, WorkerEvent
from .path_contract import RecoveryRootError, verify_recovery_root
from .protocol import PROTOCOL_VERSION, ProtocolError, encode_event, parse_message
from .secret_pipe import CredentialSessionError, credential_session
from .stop_control import StopToken


RequestRunner = Callable[..., CompletionResult]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_reader(
    control_in: TextIO,
    commands: queue.Queue[Any],
    stop: StopToken,
) -> None:
    while True:
        try:
            line = control_in.readline()
        except Exception as error:
            commands.put(error)
            return
        if not line:
            commands.put(EOFError("control channel closed"))
            return
        try:
            message = parse_message(line)
        except ProtocolError as error:
            commands.put(error)
            return
        if message.type == "stop":
            stop.request_stop()
        else:
            commands.put(message)


def worker_main(
    control_in: TextIO,
    events_out: TextIO,
    secret_handle: int,
    *,
    request_runner: RequestRunner | None = None,
) -> int:
    """Run one job with independent control and one-shot secret channels."""
    commands: queue.Queue[Any] = queue.Queue()
    stop = StopToken()
    reader = threading.Thread(
        target=_command_reader,
        args=(control_in, commands, stop),
        name="worker-command-reader",
        daemon=True,
    )
    reader.start()

    def write_event(job_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        events_out.write(
            encode_event(
                WorkerEvent(
                    protocol_version=PROTOCOL_VERSION,
                    job_id=job_id,
                    timestamp=_timestamp(),
                    type=event_type,
                    payload=dict(payload),
                )
            )
        )
        events_out.flush()

    write_event("worker", "worker_ready", {})
    command = commands.get()
    if isinstance(command, BaseException):
        write_event("worker", "failed", {"status": "invalid_control"})
        return 2
    if command.type != "start":
        write_event(command.job_id, "failed", {"status": "start_required"})
        return 2

    job_id = command.job_id
    request = command.payload.get("request")
    if not isinstance(request, RecoveryRequest):
        write_event(job_id, "failed", {"status": "invalid_request"})
        return 2
    try:
        verified_root = verify_recovery_root(request.root, request.root_fingerprint)
        request = replace(request, root=str(verified_root))
    except RecoveryRootError as error:
        write_event(
            job_id,
            "failed",
            {
                "status": "invalid_recovery_root",
                "exit_code": 2,
                "summary": {"error_type": error.error_type},
            },
        )
        return 2

    def emit(event_type: str, payload: Mapping[str, Any]) -> None:
        write_event(job_id, event_type, payload)

    try:
        if request_runner is None:
            from .worker_runtime import run_request

            request_runner = run_request
        options: dict[str, Any] = {"job_id": job_id}
        if secret_handle >= 0:
            from .worker_runtime import DefaultRuntimeServices

            services: DefaultRuntimeServices
            used = False

            def open_credentials(value: RecoveryRequest):
                nonlocal used
                if used:
                    raise CredentialSessionError("Канал пароля уже использован")
                used = True
                return credential_session(
                    value,
                    secret_handle,
                    client_factory=services.make_client,
                )

            services = DefaultRuntimeServices(open_credentials)
            options["services"] = services
        result = request_runner(request, emit, stop, **options)
    except Exception as error:
        result = CompletionResult(
            job_id,
            False,
            "failed",
            1,
            {"error_type": type(error).__name__},
        )
    if result.status == "stopped":
        emit("stopping", {"status": "stopped"})
    terminal_type = "completed" if result.success else "failed"
    emit(
        terminal_type,
        {
            "status": result.status,
            "exit_code": result.exit_code,
            "summary": dict(result.summary),
        },
    )
    return result.exit_code


def run_worker(secret_handle: int = -1) -> int:
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")
    return worker_main(sys.stdin, sys.stdout, secret_handle)


__all__ = ["run_worker", "worker_main"]
