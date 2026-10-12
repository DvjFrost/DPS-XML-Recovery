"""Two-phase inventory and download orchestration."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Callable

from dps_api import APIError, AuthFatalError, DPSClient
from dps_archive import ArchiveStore, InvalidXML
from dps_recovery_state import RecoveryState


ProgressCallback = Callable[[dict[str, Any]], None]
LOGGER = logging.getLogger("dps_recovery")


def iter_periods(start_year: int = 2026, end_year: int = 2010):
    if end_year > start_year:
        raise ValueError("end_year must not exceed start_year")
    for year in range(start_year, end_year - 1, -1):
        for month in range(12, 0, -1):
            yield year, month


class RecoveryEngine:
    def __init__(
        self,
        client: DPSClient,
        state: RecoveryState,
        archive: ArchiveStore,
        *,
        start_year: int = 2026,
        end_year: int = 2010,
        progress: ProgressCallback | None = None,
    ):
        if end_year < 2010:
            raise ValueError("DPS recovery is restricted to 2010 or later")
        self.client = client
        self.state = state
        self.archive = archive
        self.start_year = start_year
        self.end_year = end_year
        self.progress = progress or (lambda snapshot: None)

    def run_inventory(self, periods: Iterable[tuple[int, int]] | None = None) -> dict[str, int]:
        checked = 0
        errors = 0
        records_seen = 0
        for year, month in periods or iter_periods(self.start_year, self.end_year):
            period = self.state.get_period(year, month)
            if (
                period
                and int(period["total_pages"] or 0) == 0
                and int(period["total_elements"] or 0) == 0
                and int(period["next_page"]) == 1
                and self.state.count_documents_for_period(year, month) == 0
            ):
                if not period["complete"] or period.get("error"):
                    self.state.mark_period_complete(year, month)
                checked += 1
                continue
            if period and period["total_pages"] is not None and int(period["next_page"]) > int(period["total_pages"]):
                message = (
                    f"invalid resume checkpoint: next_page={period['next_page']} "
                    f"> total_pages={period['total_pages']}"
                )
                self.state.mark_period_error(year, month, message)
                self.state.record_event("ERROR", "inventory", message)
                errors += 1
                checked += 1
                continue
            if period and period["complete"]:
                unique = self.state.count_documents_for_period(year, month)
                expected = int(period["total_elements"] or 0)
                if unique != expected:
                    message = f"inventory incomplete: API totalElements={expected}, unique inventory={unique}"
                    self.state.mark_period_error(year, month, message)
                    self.state.record_event("ERROR", "inventory", message)
                    errors += 1
                checked += 1
                continue
            page_number = int(period["next_page"]) if period else 0
            while True:
                try:
                    payload = self.client.list_page(year, month, page_number)
                    returned_page = int(payload["number"])
                    if returned_page != page_number:
                        raise APIError(
                            f"pagination mismatch: requested {page_number}, received {returned_page}",
                            endpoint="/reg_doc/list",
                        )
                    total_pages = int(payload["totalPages"])
                    records = payload["content"]
                    self.state.checkpoint_page(
                        year,
                        month,
                        page_number,
                        total_pages,
                        records,
                        total_elements=int(payload["totalElements"]),
                    )
                    records_seen += len(records)
                    self.state.export_all()
                    self.progress(
                        {
                            "phase": "inventory",
                            "year": year,
                            "month": month,
                            "page": page_number + 1,
                            "total_pages": total_pages,
                            "records_seen": records_seen,
                            "unique_documents": len(self.state.list_documents()),
                            "periods_checked": checked,
                            "errors": errors,
                        }
                    )
                    page_number += 1
                    if total_pages == 0 or page_number >= total_pages or bool(payload["last"]):
                        unique = self.state.count_documents_for_period(year, month)
                        expected = int(payload["totalElements"])
                        if unique != expected:
                            raise APIError(
                                f"inventory incomplete: API totalElements={expected}, unique inventory={unique}",
                                endpoint="/reg_doc/list",
                            )
                        break
                except AuthFatalError:
                    raise
                except (APIError, KeyError, TypeError, ValueError) as error:
                    errors += 1
                    self.state.mark_period_error(year, month, str(error))
                    self.state.record_event("ERROR", "inventory", str(error))
                    self.state.export_all()
                    break
            checked += 1
        return {
            "periods_checked": checked,
            "records_seen": records_seen,
            "unique_documents": len(self.state.list_documents()),
            "errors": errors,
        }

    def run_download(self) -> dict[str, int]:
        stats = {
            "primary_total": len(self.state.list_documents()),
            "primary_downloaded": 0,
            "primary_existing": 0,
            "primary_errors": 0,
            "related_found": 0,
            "related_downloaded": 0,
            "related_existing": 0,
            "related_errors": 0,
            "bytes": 0,
        }
        for index, document in enumerate(self.state.list_documents(), start=1):
            parent_year = int(document["year"])
            parent_month = int(document["month"])
            parent_id = int(document["cod_regdoc"])
            api_record = document["api_record"]
            existing = self.archive.existing_for(
                "primary", parent_year, parent_month, parent_id, None
            )
            if existing:
                stats["primary_existing"] += 1
            else:
                try:
                    response = self.client.document_xml(parent_year, parent_id)
                    metadata = self.archive.commit_payload(
                        kind="primary",
                        year=parent_year,
                        month=parent_month,
                        document_id=parent_id,
                        parent_id=None,
                        form_code=document.get("form_code"),
                        form_name=document.get("form_name"),
                        api_record=api_record,
                        source_endpoint=f"/reg_doc/doc/{parent_year}/{parent_id}/xml",
                        content_type=response.content_type,
                        payload=response.body,
                    )
                    stats["primary_downloaded"] += 1
                    stats["bytes"] += int(metadata["size_bytes"])
                    LOGGER.info(
                        "saved primary document_id=%s form=%s size=%s sha256=%s endpoint=%s",
                        parent_id,
                        document.get("form_code") or "",
                        metadata["size_bytes"],
                        metadata["sha256"],
                        metadata["source_endpoint"],
                    )
                except AuthFatalError:
                    raise
                except (APIError, InvalidXML, OSError, ValueError) as error:
                    stats["primary_errors"] += 1
                    self._record_error(
                        "primary", parent_year, parent_month, parent_id, None,
                        document.get("form_code"), document.get("form_name"), api_record, error,
                    )
                    self.state.export_all()
                    self._progress_download(stats, index, document)
                    continue

            related_records = self.archive.read_related_index(parent_year, parent_month, parent_id)
            if related_records is None:
                try:
                    related_records = self.client.related_documents(parent_year, parent_id)
                    self.archive.write_related_index(
                        parent_year,
                        parent_month,
                        parent_id,
                        related_records,
                        f"/reg_doc/docs/{parent_year}/{parent_id}",
                    )
                except AuthFatalError:
                    raise
                except (APIError, OSError, ValueError) as error:
                    stats["related_errors"] += 1
                    self.state.record_event("ERROR", "related-list", str(error), parent_id)
                    self.state.export_all()
                    self._progress_download(stats, index, document)
                    continue

            related_records = [
                item for item in related_records
                if int(item.get("codRegdoc") or 0) != parent_id
            ]
            stats["related_found"] += len(related_records)
            for related in related_records:
                related_id = int(related["codRegdoc"])
                related_year = int(related.get("periodYear") or parent_year)
                related_month = int(related.get("periodMonth") or parent_month)
                existing_related = self.archive.existing_for(
                    "related", related_year, related_month, related_id, parent_id,
                    parent_year, parent_month,
                )
                if existing_related:
                    stats["related_existing"] += 1
                    continue
                try:
                    response = self.client.document_xml(related_year, related_id)
                    metadata = self.archive.commit_payload(
                        kind="related",
                        year=related_year,
                        month=related_month,
                        document_id=related_id,
                        parent_id=parent_id,
                        parent_year=parent_year,
                        parent_month=parent_month,
                        form_code=related.get("doc") or related.get("cDoc"),
                        form_name=related.get("docName"),
                        api_record=related,
                        source_endpoint=f"/reg_doc/doc/{related_year}/{related_id}/xml",
                        content_type=response.content_type,
                        payload=response.body,
                    )
                    stats["related_downloaded"] += 1
                    stats["bytes"] += int(metadata["size_bytes"])
                    LOGGER.info(
                        "saved related document_id=%s parent_id=%s form=%s size=%s sha256=%s endpoint=%s",
                        related_id,
                        parent_id,
                        related.get("doc") or related.get("cDoc") or "",
                        metadata["size_bytes"],
                        metadata["sha256"],
                        metadata["source_endpoint"],
                    )
                except AuthFatalError:
                    raise
                except (APIError, InvalidXML, OSError, ValueError, KeyError) as error:
                    stats["related_errors"] += 1
                    self._record_error(
                        "related", related_year, related_month, related_id, parent_id,
                        related.get("doc") or related.get("cDoc"), related.get("docName"),
                        related, error,
                    )
            self.state.export_all()
            self._progress_download(stats, index, document)
        return stats

    def _record_error(
        self,
        kind: str,
        year: int,
        month: int,
        document_id: int,
        parent_id: int | None,
        form_code: str | None,
        form_name: str | None,
        api_record: dict[str, Any],
        error: BaseException,
    ) -> None:
        key = self.archive.object_key(kind, year, document_id, parent_id)
        self.state.upsert_object(
            object_key=key,
            kind=kind,
            year=year,
            month=month,
            document_id=document_id,
            parent_id=parent_id,
            form_code=form_code,
            form_name=form_name,
            status="error",
            relative_path=None,
            size_bytes=None,
            sha256=None,
            xml_valid=None,
            content_type=None,
            downloaded_at=None,
            source_endpoint=f"/reg_doc/doc/{year}/{document_id}/xml",
            error=str(error),
            api_record=api_record,
        )
        self.state.record_event("ERROR", "download", str(error), document_id)

    def _progress_download(
        self, stats: dict[str, int], index: int, document: dict[str, Any]
    ) -> None:
        self.progress(
            {
                "phase": "download",
                **stats,
                "primary_processed": index,
                "current_year": document["year"],
                "current_month": document["month"],
                "current_id": document["cod_regdoc"],
                "current_form": document.get("form_code"),
            }
        )
