"""GUI state machine and side-effect-free input validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from .model import RecoveryRequest, WorkerEvent
from .path_contract import recovery_root_fingerprint


def _wipe_bytearray(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


AUTHENTICATED_MODES = frozenset({"full", "inventory_only", "probe_only"})
KNOWN_MODES = AUTHENTICATED_MODES | frozenset({"audit_only", "reindex_and_audit"})
DEFAULT_PROTECTED_ROOTS = tuple(
    Path(item)
    for item in os.environ.get("DPS_XML_RECOVERY_PROTECTED_ROOTS", "").split(os.pathsep)
    if item.strip()
)


class AppState(Enum):
    SYSTEM_CHECK = auto()
    CREDENTIALS = auto()
    OPTIONS = auto()
    CONFIRM = auto()
    RUNNING = auto()
    STOPPING = auto()
    RESULT = auto()


class WorkerPort(Protocol):
    @property
    def is_running(self) -> bool: ...
    def start(self, request: RecoveryRequest, password: bytearray) -> None: ...
    def request_stop(self) -> None: ...
    def force_terminate(self) -> None: ...
    def poll_events(self) -> list[WorkerEvent]: ...


@dataclass(frozen=True)
class InputValues:
    tax_id: str
    jks_path: str
    alias_index: int | None
    start_year: int
    end_year: int
    recovery_root: str
    mode: str = "full"


@dataclass(frozen=True)
class ValidationResult:
    errors: Mapping[str, str] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors


def _same_path(first: Path, second: Path) -> bool:
    try:
        return os.path.normcase(str(first.resolve(strict=False))) == os.path.normcase(
            str(second.resolve(strict=False))
        )
    except OSError:
        return False


def validate_inputs(
    values: InputValues,
    *,
    current_year: int = 2026,
    protected_roots: Iterable[Path] = DEFAULT_PROTECTED_ROOTS,
) -> ValidationResult:
    errors: dict[str, str] = {}
    if values.mode not in KNOWN_MODES:
        errors["mode"] = "Выберите допустимый режим восстановления."
    authenticated = values.mode in AUTHENTICATED_MODES
    tax_id = values.tax_id.strip()
    if authenticated and (not tax_id.isascii() or not tax_id.isdigit() or len(tax_id) not in {8, 10}):
        errors["tax_id"] = "ЄДРПОУ должен содержать 8 цифр, РНОКПП — 10 цифр."
    if not isinstance(values.start_year, int) or values.start_year < 2010:
        errors["start_year"] = "Начальный год должен быть не ранее 2010."
    if not isinstance(values.end_year, int) or values.end_year > current_year:
        errors["end_year"] = f"Конечный год должен быть не позднее {current_year}."
    elif isinstance(values.start_year, int) and values.end_year < values.start_year:
        errors["end_year"] = "Конечный год не может быть меньше начального."
    if authenticated:
        jks = Path(values.jks_path) if values.jks_path.strip() else None
        if jks is None or not jks.is_file():
            errors["jks_path"] = "Выберите существующий файл ключа JKS."
        if values.alias_index is None or values.alias_index < 0:
            errors["alias_index"] = "Выберите alias ключа."
    root = Path(values.recovery_root) if values.recovery_root.strip() else None
    if root is None or not root.is_dir():
        errors["recovery_root"] = "Выберите существующий каталог сохранения."
    elif any(_same_path(root, Path(item)) for item in protected_roots):
        errors["recovery_root"] = "Эталонный или staging-каталог защищён от записи."
    elif not os.access(root, os.W_OK):
        errors["recovery_root"] = "Выбранный каталог недоступен для записи."
    return ValidationResult(errors)


class RecoveryController:
    def __init__(
        self,
        worker: WorkerPort,
        *,
        current_year: int = 2026,
        protected_roots: Iterable[Path] = DEFAULT_PROTECTED_ROOTS,
        log_limit: int = 250,
    ) -> None:
        self.worker = worker
        self.current_year = current_year
        self.protected_roots = tuple(protected_roots)
        self.log_limit = max(10, log_limit)
        self.state = AppState.SYSTEM_CHECK
        self.inputs: InputValues | None = None
        self.validation = ValidationResult({"form": "Заполните параметры восстановления."})
        self.preflight_ok = False
        self.preflight_message = "Проверка не выполнена"
        self.result_kind: str | None = None
        self.result_message = ""
        self.result_details = ""
        self.progress_current = 0
        self.progress_total = 0
        self.progress_phase = ""
        self.log_lines: list[str] = []
        self.diagnostic_events: list[dict[str, object]] = []

    @property
    def needs_password(self) -> bool:
        return bool(
            self.inputs
            and self.inputs.mode in AUTHENTICATED_MODES
            and self.preflight_ok
            and self.validation.valid
        )

    @property
    def can_start(self) -> bool:
        if self.state in {AppState.RUNNING, AppState.STOPPING} or not self.inputs:
            return False
        return self.validation.valid and (
            self.inputs.mode not in AUTHENTICATED_MODES or self.preflight_ok
        )

    def set_preflight(self, success: bool, message: str) -> None:
        if self.state in {AppState.RUNNING, AppState.STOPPING}:
            return
        self.preflight_ok = bool(success)
        self.preflight_message = message
        self.state = AppState.CREDENTIALS if success else AppState.SYSTEM_CHECK

    def set_inputs(self, values: InputValues) -> ValidationResult:
        if self.state in {AppState.RUNNING, AppState.STOPPING}:
            return self.validation
        self.inputs = values
        self.validation = validate_inputs(
            values,
            current_year=self.current_year,
            protected_roots=self.protected_roots,
        )
        self.state = AppState.CONFIRM if self.validation.valid else AppState.OPTIONS
        return self.validation

    def _request(self) -> RecoveryRequest:
        if self.inputs is None:
            raise RuntimeError("Параметры восстановления не заданы")
        return RecoveryRequest(
            mode=self.inputs.mode,
            root=self.inputs.recovery_root,
            # The GUI presents a natural ascending interval ("from"/"to"),
            # while the established recovery engine iterates newest to oldest.
            start_year=max(self.inputs.start_year, self.inputs.end_year),
            end_year=min(self.inputs.start_year, self.inputs.end_year),
            tax_id=self.inputs.tax_id.strip() or None,
            jks_path=self.inputs.jks_path or None,
            alias_index=self.inputs.alias_index,
            root_fingerprint=recovery_root_fingerprint(self.inputs.recovery_root),
        )

    def start(self, password: bytearray) -> None:
        if self.state in {AppState.RUNNING, AppState.STOPPING} or self.worker.is_running:
            _wipe_bytearray(password)
            raise RuntimeError("Задание уже выполняется")
        if not self.can_start:
            _wipe_bytearray(password)
            raise RuntimeError("Параметры не прошли проверку")
        if self.needs_password and not password:
            _wipe_bytearray(password)
            raise RuntimeError("Введите пароль КЭП")
        self.result_kind = None
        self.result_message = ""
        self.result_details = ""
        self.progress_current = self.progress_total = 0
        self.progress_phase = "Запуск"
        self.diagnostic_events = []
        try:
            self.worker.start(self._request(), password)
        finally:
            _wipe_bytearray(password)
        self.state = AppState.RUNNING

    def request_stop(self, confirmed: bool) -> bool:
        if not confirmed or self.state is not AppState.RUNNING:
            return False
        self.worker.request_stop()
        self.state = AppState.STOPPING
        self.progress_phase = "Безопасная остановка"
        return True

    def force_terminate(self) -> None:
        if self.state in {AppState.RUNNING, AppState.STOPPING}:
            self.worker.force_terminate()
            self.state = AppState.RESULT
            self.result_kind = "FAIL"
            self.result_message = "Worker принудительно остановлен. PASS не подтверждён."
            self.result_details = ""

    def reset_result(self) -> None:
        if self.state is AppState.RESULT:
            self.state = AppState.CONFIRM if self.validation.valid else AppState.OPTIONS
            self.result_kind = None
            self.result_message = ""
            self.result_details = ""

    def _append_log(self, line: str) -> None:
        self.log_lines.append(line)
        if len(self.log_lines) > self.log_limit:
            del self.log_lines[: len(self.log_lines) - self.log_limit]

    @staticmethod
    def _safe_code(value: object, fallback: str) -> str:
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}", value):
            return value
        return fallback

    def _record_diagnostic(self, value: dict[str, object]) -> None:
        self.diagnostic_events.append(value)
        if len(self.diagnostic_events) > self.log_limit:
            del self.diagnostic_events[: len(self.diagnostic_events) - self.log_limit]

    def _apply_progress(self, item: WorkerEvent) -> None:
        payload = item.payload
        phase = self._safe_code(payload.get("phase"), self.progress_phase or "progress")
        current = payload.get("current")
        total = payload.get("total")
        valid_ratio = (
            isinstance(current, int) and not isinstance(current, bool)
            and isinstance(total, int) and not isinstance(total, bool)
            and total > 0 and 0 <= current <= total
        )
        self.progress_phase = phase
        self.progress_current = current if valid_ratio else 0
        self.progress_total = total if valid_ratio else 0
        line = f"{phase}: {current}/{total}" if valid_ratio else phase
        self._append_log(line)
        safe: dict[str, object] = {"type": "progress", "phase": phase}
        for key in (
            "current", "total", "year", "month", "page", "total_pages",
            "records_seen", "unique_documents", "periods_checked", "errors",
            "primary_processed", "primary_total", "primary_downloaded",
            "related_downloaded", "primary_errors", "related_errors",
        ):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                safe[key] = value
        self._record_diagnostic(safe)

    def poll(self) -> int:
        events = self.worker.poll_events()
        non_progress = 0
        latest_progress: WorkerEvent | None = None
        for item in events:
            if item.type == "progress":
                latest_progress = item
                continue
            if latest_progress is not None:
                self._apply_progress(latest_progress)
                latest_progress = None
            non_progress += 1
            if item.type == "phase_started":
                self.progress_phase = self._safe_code(item.payload.get("phase"), "unknown")
                self._append_log(f"Этап: {self.progress_phase}")
                self._record_diagnostic({"type": "phase_started", "phase": self.progress_phase})
            elif item.type == "warning":
                code = self._safe_code(item.payload.get("code"), "unknown_warning")
                self._append_log(f"Предупреждение: {code}")
                self._record_diagnostic({"type": "warning", "code": code})
            elif item.type in {"completed", "failed"}:
                status = self._safe_code(item.payload.get("status"), "unknown")
                summary = item.payload.get("summary", {})
                audit_pass = isinstance(summary, Mapping) and summary.get("audit_pass") is True
                labels = (
                    ("primary_valid", "Основных XML"),
                    ("related_valid", "Связанных XML"),
                    ("invalid_files", "Невалидных XML"),
                    ("part_files", "Незавершённых .part"),
                    ("error_count", "Ошибок аудита"),
                )
                self.result_details = " · ".join(
                    f"{label}: {summary[key]}"
                    for key, label in labels
                    if isinstance(summary, Mapping) and isinstance(summary.get(key), int)
                )
                error_type = summary.get("error_type") if isinstance(summary, Mapping) else None
                if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type):
                    detail = f"Тип ошибки: {error_type}"
                    self.result_details = " · ".join(filter(None, (self.result_details, detail)))
                    self._append_log(detail)
                terminal = {"type": item.type, "status": status}
                if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type):
                    terminal["error_type"] = error_type
                self._record_diagnostic(terminal)
                self.state = AppState.RESULT
                if item.type == "completed" and status == "audit_pass" and audit_pass:
                    self.result_kind = "PASS"
                    self.result_message = "Финальный аудит: PASS"
                elif item.type == "completed":
                    self.result_kind = "INFO"
                    self.result_message = "Операция завершена без финального audit PASS."
                else:
                    self.result_kind = "FAIL"
                    self.result_message = "Операция завершилась с ошибкой. PASS не подтверждён."
                self._append_log(f"Результат: {status}")
        if latest_progress is not None:
            self._apply_progress(latest_progress)
        return non_progress + (1 if latest_progress is not None else 0)


__all__ = [
    "AppState",
    "InputValues",
    "RecoveryController",
    "ValidationResult",
    "validate_inputs",
]
