"""Offline integrity audit for the portable DPS archive."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from dps_archive import ArchiveStore
from dps_recovery_state import RecoveryState


@dataclass
class AuditReport:
    clean: bool
    inventory_documents: int
    primary_valid: int
    related_valid: int
    invalid_files: int
    part_files: int
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_archive(state: RecoveryState, archive: ArchiveStore) -> AuditReport:
    errors: list[str] = []
    primary_valid = 0
    related_valid = 0
    documents = state.list_documents()

    for period in state.list_periods():
        year = int(period["year"])
        month = int(period["month"])
        unique = state.count_documents_for_period(year, month)
        expected = int(period["total_elements"] or 0)
        if not period["complete"]:
            errors.append(f"inventory period incomplete: {year}-{month:02d}: {period.get('error') or 'no error detail'}")
        if unique != expected:
            errors.append(
                f"inventory count mismatch: {year}-{month:02d}: "
                f"API totalElements={expected}, unique inventory={unique}"
            )
        expected_next_page = max(1, int(period["total_pages"] or 0))
        if int(period["next_page"]) != expected_next_page:
            errors.append(
                f"inventory checkpoint invalid: {year}-{month:02d}: "
                f"next_page={period['next_page']}, expected={expected_next_page}, "
                f"total_pages={period['total_pages']}"
            )

    for document in documents:
        year = int(document["year"])
        month = int(document["month"])
        document_id = int(document["cod_regdoc"])
        directory = archive.object_directory("primary", year, month, document_id, None)
        metadata = archive.verify_existing(directory / "metadata.json")
        if metadata is None:
            errors.append(f"primary missing or corrupt: {year}/{document_id}")
            continue
        primary_valid += 1

        related_index = directory / "related" / "index.json"
        if not related_index.exists():
            errors.append(f"related index missing: {year}/{document_id}")
            continue
        try:
            related_records = json.loads(related_index.read_text("utf-8"))["records"]
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            errors.append(f"related index invalid: {year}/{document_id}")
            continue
        for record in related_records:
            related_id = int(record.get("codRegdoc") or 0)
            if related_id == document_id:
                continue
            related_year = int(record.get("periodYear") or year)
            related_month = int(record.get("periodMonth") or month)
            related_directory = archive.object_directory(
                "related", related_year, related_month, related_id, document_id, year, month
            )
            if archive.verify_existing(related_directory / "metadata.json") is None:
                errors.append(f"related missing or corrupt: parent={document_id} id={related_id}")
            else:
                related_valid += 1

    invalid_files = 0
    metadata_paths = archive.archive_root.rglob("metadata.json") if archive.archive_root.exists() else []
    for metadata_path in metadata_paths:
        if archive.verify_existing(metadata_path) is None:
            invalid_files += 1
            errors.append(f"invalid metadata/payload: {metadata_path.relative_to(archive.data_root)}")

    parts = archive.part_files()
    errors.extend(f"unfinished .part: {path.relative_to(archive.data_root)}" for path in parts)
    error_objects = [item for item in state.list_objects() if item["status"] == "error"]
    errors.extend(
        f"unresolved download error: {item['object_key']}: {item.get('error') or ''}"
        for item in error_objects
    )
    return AuditReport(
        clean=not errors,
        inventory_documents=len(documents),
        primary_valid=primary_valid,
        related_valid=related_valid,
        invalid_files=invalid_files,
        part_files=len(parts),
        errors=errors,
    )
