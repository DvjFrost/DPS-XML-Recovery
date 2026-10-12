"""Immutable messages shared by the GUI and recovery worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RecoveryRequest:
    mode: str
    root: str
    start_year: int
    end_year: int
    tax_id: str | None = None
    jks_path: str | None = None
    alias_index: int | None = None
    probe_year: int = 2026
    probe_month: int = 7
    delay: float = 0.25
    root_fingerprint: str | None = None


@dataclass(frozen=True)
class ControlMessage:
    protocol_version: int
    job_id: str
    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerEvent:
    protocol_version: int
    job_id: str
    timestamp: str
    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionResult:
    job_id: str
    success: bool
    status: str
    exit_code: int
    summary: Mapping[str, Any] = field(default_factory=dict)
