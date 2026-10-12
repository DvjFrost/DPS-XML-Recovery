"""Deterministic, offline-only runtime for the frozen acceptance build.

This module is deliberately absent from the public PyInstaller specification.
It never loads IIT components and never opens a network connection.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Mapping

from .model import CompletionResult, RecoveryRequest
from .stop_control import StopToken


EventSink = Callable[[str, Mapping[str, object]], None]


def _result(job_id: str, success: bool, status: str, exit_code: int, **summary: object) -> CompletionResult:
    return CompletionResult(job_id, success, status, exit_code, summary)


def _scenario(root: Path) -> str:
    try:
        value = json.loads((root / "acceptance-scenario.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "happy"
    name = value.get("scenario") if isinstance(value, dict) else None
    return name if isinstance(name, str) else "happy"


def _emit_progress(emit: EventSink, phase: str, current: int, total: int) -> None:
    emit("progress", {"phase": phase, "current": current, "total": total})


def acceptance_request_runner(
    request: RecoveryRequest,
    emit: EventSink,
    stop: StopToken,
    *,
    job_id: str = "acceptance",
    **_options: object,
) -> CompletionResult:
    """Run a deterministic acceptance scenario without crypto or HTTP."""

    root = Path(request.root)
    name = _scenario(root)
    if name == "worker_crash":
        os._exit(23)

    failures: Mapping[str, tuple[str, str]] = {
        "wrong_password": ("credential_failed", "CredentialSessionError"),
        "authorization_failed": ("authorization_failed", "AuthFatalError"),
        "corrupt_state": ("state_corrupt", "DatabaseError"),
    }
    if name in failures:
        status, error_type = failures[name]
        return _result(job_id, False, status, 2, audit_pass=False, error_type=error_type)
    if name == "partial_xml":
        (root / "acceptance-download.xml.part").write_bytes(b"incomplete-test-data")
        return _result(
            job_id, False, "audit_failed", 2,
            audit_pass=False, error_type="InvalidXML", part_files=1,
        )

    if request.mode == "audit_only":
        emit("phase_started", {"phase": "audit"})
        return _result(
            job_id, True, "audit_pass", 0, audit_pass=True,
            primary_valid=3, related_valid=2, invalid_files=0, part_files=0, error_count=0,
        )

    emit("phase_started", {"phase": "inventory"})
    state_path = root / "acceptance-state.json"
    start = 1
    if name == "stop_resume" and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("checkpoint") == 1:
            start = 2
    for current in range(start, 4):
        if stop.is_requested():
            state_path.write_text(
                json.dumps({"checkpoint": max(1, current - 1), "complete": False}), encoding="utf-8"
            )
            return _result(job_id, False, "stopped", 3, audit_pass=False)
        _emit_progress(emit, "inventory", current, 3)
        if name == "stop_resume" and current == 1:
            state_path.write_text(json.dumps({"checkpoint": 1, "complete": False}), encoding="utf-8")
            if stop.wait(5.0):
                return _result(job_id, False, "stopped", 3, audit_pass=False)

    if request.mode == "inventory_only":
        return _result(job_id, True, "inventory_completed", 0, audit_pass=False, inventory_unique=3)

    emit("phase_started", {"phase": "download"})
    for current in range(1, 4):
        if stop.is_requested():
            return _result(job_id, False, "stopped", 3, audit_pass=False)
        _emit_progress(emit, "download", current, 3)
    emit("phase_started", {"phase": "audit"})
    if name == "stop_resume":
        state_path.write_text(json.dumps({"checkpoint": 3, "complete": True}), encoding="utf-8")
    return _result(
        job_id, True, "audit_pass", 0, audit_pass=True,
        primary_valid=3, related_valid=2, invalid_files=0, part_files=0, error_count=0,
    )


__all__ = ["acceptance_request_runner"]
