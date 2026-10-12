"""Non-blocking controller for the isolated worker process."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any, Callable, Mapping, Sequence

from .model import RecoveryRequest, WorkerEvent
from .protocol import EVENT_TYPES, PROTOCOL_VERSION, ProtocolError, encode_event
from .secret_pipe import SecretPipe, wipe_bytearray


class WorkerClientError(RuntimeError):
    pass


class WorkerAlreadyRunning(WorkerClientError):
    pass


class WorkerStartError(WorkerClientError):
    pass


class WorkerProtocolError(WorkerClientError):
    pass


def _default_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "dps_gui.entrypoint"]


def _parse_event(line: str) -> WorkerEvent:
    try:
        document = json.loads(line)
        if not isinstance(document, dict) or frozenset(document) != {
            "protocol_version", "job_id", "timestamp", "type", "payload"
        }:
            raise ValueError
        if document["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError
        if not isinstance(document["type"], str) or document["type"] not in EVENT_TYPES:
            raise ValueError
        if not isinstance(document["job_id"], str) or not document["job_id"]:
            raise ValueError
        if not isinstance(document["timestamp"], str) or not document["timestamp"]:
            raise ValueError
        if not isinstance(document["payload"], dict):
            raise ValueError
        event = WorkerEvent(
            protocol_version=PROTOCOL_VERSION,
            job_id=document["job_id"],
            timestamp=document["timestamp"],
            type=document["type"],
            payload=document["payload"],
        )
        encode_event(event)
        return event
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ProtocolError):
        raise WorkerProtocolError("Worker вернул некорректное событие") from None


class WorkerClient:
    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        secret_pipe_factory: Callable[[], SecretPipe] = SecretPipe.create,
        ready_timeout: float = 10.0,
        terminate_timeout: float = 3.0,
        job_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._command = list(command or _default_command())
        self._process_factory = process_factory
        self._secret_pipe_factory = secret_pipe_factory
        self._ready_timeout = ready_timeout
        self._terminate_timeout = terminate_timeout
        self._job_id_factory = job_id_factory
        self._lock = threading.Lock()
        self._events: queue.Queue[WorkerEvent] = queue.Queue()
        self._process: Any | None = None
        self._job_id: str | None = None
        self._ready = threading.Event()
        self._reader_done = threading.Event()
        self._reader_error = threading.Event()
        self._terminal_seen = False
        self._crash_reported = False
        self._protocol_failure_reported = False
        self._state = "idle"

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _event_reader(self, stdout: Any) -> None:
        try:
            while True:
                line = stdout.readline()
                if not line:
                    return
                try:
                    event = _parse_event(line)
                except WorkerProtocolError:
                    self._reader_error.set()
                    return
                self._events.put(event)
                if event.type == "worker_ready":
                    self._ready.set()
        except Exception:
            self._reader_error.set()
        finally:
            self._reader_done.set()

    @staticmethod
    def _discard_stderr(stderr: Any) -> None:
        try:
            while stderr.readline():
                pass
        except Exception:
            pass

    def _write_control(self, message_type: str, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise WorkerClientError("Worker не запущен")
        document = {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": self._job_id,
            "type": message_type,
            "payload": dict(payload),
        }
        try:
            process.stdin.write(
                json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"
            )
            process.stdin.flush()
        except Exception:
            raise WorkerClientError("Не удалось отправить команду worker") from None

    def start(self, request: RecoveryRequest, password: bytearray) -> None:
        if not isinstance(password, bytearray):
            raise TypeError("password must be a bytearray")
        secret_pipe = None
        with self._lock:
            if self.is_running:
                wipe_bytearray(password)
                raise WorkerAlreadyRunning("Задание уже выполняется")
            if self._process is not None:
                self._terminate_process()
            self._state = "starting"
            self._job_id = self._job_id_factory()
            self._ready.clear()
            self._reader_done.clear()
            self._reader_error.clear()
            self._terminal_seen = False
            self._crash_reported = False
            self._protocol_failure_reported = False
            try:
                secret_pipe = self._secret_pipe_factory()
                command = [
                    *self._command,
                    "--worker",
                    "--protocol-version",
                    str(PROTOCOL_VERSION),
                    "--secret-handle",
                    str(secret_pipe.read_handle),
                ]
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.lpAttributeList = {"handle_list": [secret_pipe.read_handle]}
                self._process = self._process_factory(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    bufsize=1,
                    close_fds=True,
                    startupinfo=startupinfo,
                )
                secret_pipe.close_parent_read()
                threading.Thread(
                    target=self._event_reader,
                    args=(self._process.stdout,),
                    name="worker-event-reader",
                    daemon=True,
                ).start()
                threading.Thread(
                    target=self._discard_stderr,
                    args=(self._process.stderr,),
                    name="worker-stderr-drain",
                    daemon=True,
                ).start()

                deadline = time.monotonic() + self._ready_timeout
                while not self._ready.wait(0.005):
                    if self._reader_error.is_set():
                        raise WorkerProtocolError("Worker вернул некорректное событие")
                    if self._reader_done.is_set() or self._process.poll() is not None:
                        raise WorkerStartError("Worker завершился до готовности")
                    if time.monotonic() >= deadline:
                        raise WorkerStartError("Worker не подтвердил запуск вовремя")

                self._write_control("start", {"request": asdict(request)})
                secret_pipe.send(password)
                self._state = "running"
            except (WorkerAlreadyRunning, WorkerProtocolError, WorkerStartError):
                wipe_bytearray(password)
                self._terminate_process()
                raise
            except Exception:
                wipe_bytearray(password)
                self._terminate_process()
                raise WorkerStartError("Не удалось запустить worker") from None
            finally:
                wipe_bytearray(password)
                if secret_pipe is not None:
                    secret_pipe.close()

    def request_stop(self) -> None:
        with self._lock:
            if not self.is_running:
                return
            self._write_control("stop", {})
            self._state = "stopping"

    def _terminate_process(self) -> None:
        process = self._process
        if process is None:
            self._state = "idle"
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=self._terminate_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=self._terminate_timeout)
                except subprocess.TimeoutExpired:
                    pass
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._process = None
        self._state = "idle"

    def force_terminate(self) -> None:
        with self._lock:
            self._terminate_process()

    def poll_events(self) -> list[WorkerEvent]:
        result: list[WorkerEvent] = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            result.append(event)
            if event.type in {"completed", "failed"}:
                self._terminal_seen = True
                self._state = "idle"

        if (
            self._reader_error.is_set()
            and not self._terminal_seen
            and not self._protocol_failure_reported
        ):
            self._protocol_failure_reported = True
            self._terminal_seen = True
            self._terminate_process()
            result.append(
                WorkerEvent(
                    protocol_version=PROTOCOL_VERSION,
                    job_id=self._job_id or "worker",
                    timestamp="worker-protocol-error",
                    type="failed",
                    payload={"status": "invalid_worker_event"},
                )
            )
            return result

        process = self._process
        if (
            process is not None
            and process.poll() is not None
            and not self._terminal_seen
            and not self._crash_reported
        ):
            self._crash_reported = True
            self._state = "idle"
            result.append(
                WorkerEvent(
                    protocol_version=PROTOCOL_VERSION,
                    job_id=self._job_id or "worker",
                    timestamp="worker-exit",
                    type="failed",
                    payload={"status": "worker_crashed", "exit_code": int(process.poll())},
                )
            )
        return result

    def close(self) -> None:
        with self._lock:
            if self.is_running:
                try:
                    self._write_control("stop", {})
                    self._state = "stopping"
                    self._process.wait(timeout=self._terminate_timeout)
                except subprocess.TimeoutExpired:
                    self._terminate_process()
                except Exception:
                    self._terminate_process()
            else:
                self._terminate_process()


__all__ = [
    "WorkerAlreadyRunning",
    "WorkerClient",
    "WorkerClientError",
    "WorkerProtocolError",
    "WorkerStartError",
]
