"""Recovery orchestration for the isolated worker process."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, ContextManager, Mapping, Protocol

from dps_api import DPSClient
from dps_archive import ArchiveStore
from dps_audit import audit_archive
from dps_recovery import _atomic_audit_report, _open_state, setup_logging
from dps_recovery_engine import RecoveryEngine

from .model import CompletionResult, RecoveryRequest
from .stop_control import StopToken, UserRequestedStop


EventSink = Callable[[str, Mapping[str, Any]], None]


class RuntimeServices(Protocol):
    def setup_logging(self, path: Path) -> None: ...
    def open_state(self, data_root: Path, force_reindex: bool) -> ContextManager[Any]: ...
    def make_archive(self, data_root: Path, state: Any) -> Any: ...
    def credential_session(self, request: RecoveryRequest) -> ContextManager[Any]: ...
    def make_engine(
        self,
        client: Any,
        state: Any,
        archive: Any,
        *,
        start_year: int,
        end_year: int,
        progress: Callable[[dict[str, Any]], None],
    ) -> Any: ...
    def audit(self, state: Any, archive: Any) -> Any: ...
    def write_audit_report(self, path: Path, report: dict[str, Any]) -> None: ...


class DefaultRuntimeServices:
    """Bindings to the established downloader; Task 4 supplies credentials."""

    def __init__(
        self,
        credential_session_factory: Callable[[RecoveryRequest], ContextManager[Any]] | None = None,
        api_event_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._credential_session_factory = credential_session_factory
        self._api_event_callback = api_event_callback or (lambda _level, _message: None)

    def setup_logging(self, path: Path) -> None:
        setup_logging(path)

    def open_state(self, data_root: Path, force_reindex: bool) -> ContextManager[Any]:
        return _open_state(data_root, force_reindex)

    def make_archive(self, data_root: Path, state: Any) -> ArchiveStore:
        return ArchiveStore(data_root, state)

    def make_client(self, authorization: str, request: RecoveryRequest) -> DPSClient:
        return DPSClient(
            authorization,
            request_delay=request.delay,
            event_callback=self._api_event_callback,
        )

    def credential_session(self, request: RecoveryRequest) -> ContextManager[Any]:
        if self._credential_session_factory is None:
            raise RuntimeError("credential channel is not configured")
        return self._credential_session_factory(request)

    def make_engine(
        self,
        client: Any,
        state: Any,
        archive: Any,
        *,
        start_year: int,
        end_year: int,
        progress: Callable[[dict[str, Any]], None],
    ) -> RecoveryEngine:
        return RecoveryEngine(
            client,
            state,
            archive,
            start_year=start_year,
            end_year=end_year,
            progress=progress,
        )

    def audit(self, state: Any, archive: Any) -> Any:
        return audit_archive(state, archive)

    def write_audit_report(self, path: Path, report: dict[str, Any]) -> None:
        _atomic_audit_report(path, report)


def _audit_summary(report: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"audit_pass": bool(report.clean)}
    for name in (
        "inventory_documents",
        "primary_valid",
        "related_valid",
        "invalid_files",
        "part_files",
    ):
        value = getattr(report, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            result[name] = value
    errors = getattr(report, "errors", ())
    result["error_count"] = len(errors) if isinstance(errors, (list, tuple)) else 0
    return result


def _audit_result(
    *,
    job_id: str,
    services: RuntimeServices,
    state: Any,
    archive: Any,
    data_root: Path,
) -> CompletionResult:
    report = services.audit(state, archive)
    services.write_audit_report(data_root / "audit-report.json", report.to_dict())
    summary = _audit_summary(report)
    return CompletionResult(
        job_id=job_id,
        success=bool(report.clean),
        status="audit_pass" if report.clean else "audit_failed",
        exit_code=0 if report.clean else 2,
        summary=summary,
    )


def run_request(
    request: RecoveryRequest,
    emit: EventSink,
    stop: StopToken,
    *,
    services: RuntimeServices | None = None,
    job_id: str = "worker-job",
) -> CompletionResult:
    """Run one request using durable engine callbacks as stop checkpoints."""

    services = services or DefaultRuntimeServices()
    root = Path(request.root)
    data_root = root / "data"
    try:
        services.setup_logging(root / "logs" / "download.log")
        force_reindex = request.mode == "reindex_and_audit"
        with services.open_state(data_root, force_reindex) as state:
            archive = services.make_archive(data_root, state)
            if force_reindex:
                emit("phase_started", {"phase": "reindex"})
                summary = archive.reindex()
                state.export_all()
                emit("progress", {"phase": "reindex", **summary})

            if request.mode in {"audit_only", "reindex_and_audit"}:
                emit("phase_started", {"phase": "audit"})
                return _audit_result(
                    job_id=job_id,
                    services=services,
                    state=state,
                    archive=archive,
                    data_root=data_root,
                )

            archive.cleanup_part_files()
            with services.credential_session(request) as client:
                emit("phase_started", {"phase": "probe"})
                proof = client.probe_pagination(request.probe_year, request.probe_month)
                if request.mode == "probe_only":
                    return CompletionResult(
                        job_id,
                        True,
                        "probe_completed",
                        0,
                        {
                            "year": int(proof["year"]),
                            "month": int(proof["month"]),
                            "total_elements": int(proof["total_elements"]),
                            "total_pages": int(proof["total_pages"]),
                        },
                    )

                def progress(snapshot: dict[str, Any]) -> None:
                    stop.raise_if_requested()
                    payload = dict(snapshot)
                    phase = payload.get("phase")
                    if phase == "inventory":
                        total = max(0, (request.start_year - request.end_year + 1) * 12)
                        checked = payload.get("periods_checked")
                        if isinstance(checked, int) and not isinstance(checked, bool) and total > 0:
                            payload["current"] = min(max(checked + 1, 0), total)
                            payload["total"] = total
                    elif phase == "download":
                        current = payload.get("primary_processed")
                        total = payload.get("primary_total")
                        if (
                            isinstance(current, int) and not isinstance(current, bool)
                            and isinstance(total, int) and not isinstance(total, bool)
                            and total > 0
                        ):
                            payload["current"] = min(max(current, 0), total)
                            payload["total"] = total
                    emit("progress", payload)

                engine = services.make_engine(
                    client,
                    state,
                    archive,
                    start_year=request.start_year,
                    end_year=request.end_year,
                    progress=progress,
                )
                emit("phase_started", {"phase": "inventory"})
                inventory_stats = engine.run_inventory()
                if int(inventory_stats.get("errors", 0)):
                    return CompletionResult(
                        job_id,
                        False,
                        "inventory_failed",
                        2,
                        {"inventory": dict(inventory_stats)},
                    )
                if request.mode == "inventory_only":
                    return CompletionResult(
                        job_id,
                        True,
                        "inventory_completed",
                        0,
                        {"inventory": dict(inventory_stats), "audit_pass": False},
                    )

                emit("phase_started", {"phase": "download"})
                download_stats = engine.run_download()
                state.export_all()
                emit("phase_started", {"phase": "audit"})
                result = _audit_result(
                    job_id=job_id,
                    services=services,
                    state=state,
                    archive=archive,
                    data_root=data_root,
                )
                return CompletionResult(
                    result.job_id,
                    result.success,
                    result.status,
                    result.exit_code,
                    {
                        **dict(result.summary),
                        "inventory": dict(inventory_stats),
                        "download": dict(download_stats),
                    },
                )
    except UserRequestedStop:
        return CompletionResult(job_id, False, "stopped", 130)
    except Exception as error:
        return CompletionResult(
            job_id,
            False,
            "failed",
            1,
            {"error_type": type(error).__name__},
        )


__all__ = [
    "DefaultRuntimeServices",
    "EventSink",
    "StopToken",
    "UserRequestedStop",
    "run_request",
]
