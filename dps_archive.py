"""Exact-byte archive storage, verification, and offline reindex."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dps_recovery_state import RecoveryState


class InvalidXML(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _validate_xml(payload: bytes) -> str:
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError) as error:
        raise InvalidXML(str(error)) from error
    return root.tag


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, path)
    except BaseException:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class ArchiveStore:
    def __init__(self, data_root: Path | str, state: RecoveryState):
        self.data_root = Path(data_root)
        self.archive_root = self.data_root / "archive"
        self.state = state

    @staticmethod
    def object_key(kind: str, year: int, document_id: int, parent_id: int | None) -> str:
        if kind == "primary":
            return f"primary:{year}:{document_id}"
        if parent_id is None:
            raise ValueError("related object requires parent_id")
        return f"related:{year}:{parent_id}:{document_id}"

    def object_directory(
        self,
        kind: str,
        year: int,
        month: int,
        document_id: int,
        parent_id: int | None,
        parent_year: int | None = None,
        parent_month: int | None = None,
    ) -> Path:
        storage_year = parent_year if kind == "related" and parent_year is not None else year
        storage_month = parent_month if kind == "related" and parent_month is not None else month
        base = self.archive_root / f"{storage_year:04d}" / f"{storage_month:02d}"
        if kind == "primary":
            return base / str(document_id)
        if parent_id is None:
            raise ValueError("related object requires parent_id")
        return base / str(parent_id) / "related" / str(document_id)

    def commit_payload(
        self,
        *,
        kind: str,
        year: int,
        month: int,
        document_id: int,
        parent_id: int | None,
        form_code: str | None,
        form_name: str | None,
        api_record: dict[str, Any],
        source_endpoint: str,
        content_type: str,
        payload: bytes,
        parent_year: int | None = None,
        parent_month: int | None = None,
    ) -> dict[str, Any]:
        root_element = _validate_xml(payload)
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.object_directory(
            kind, year, month, document_id, parent_id, parent_year, parent_month
        )
        directory.mkdir(parents=True, exist_ok=True)
        payload_path = directory / "document.xml"
        metadata_path = directory / "metadata.json"
        relative_path = payload_path.relative_to(self.data_root).as_posix()
        key = self.object_key(kind, year, document_id, parent_id)
        metadata = {
            "schema_version": 1,
            "object_key": key,
            "kind": kind,
            "year": year,
            "month": month,
            "document_id": document_id,
            "parent_id": parent_id,
            "parent_year": parent_year,
            "parent_month": parent_month,
            "form_code": form_code,
            "form_name": form_name,
            "status": "downloaded",
            "source": "DPS private API",
            "source_endpoint": source_endpoint,
            "downloaded_at": _now(),
            "http_content_type": content_type,
            "relative_path": relative_path,
            "size_bytes": len(payload),
            "sha256": digest,
            "xml_valid": True,
            "xml_root": root_element,
            "api_record": api_record,
        }
        _atomic_bytes(payload_path, payload)
        _atomic_json(metadata_path, metadata)
        self._upsert_metadata(metadata)
        return metadata

    def _upsert_metadata(self, metadata: dict[str, Any]) -> None:
        self.state.upsert_object(
            object_key=metadata["object_key"],
            kind=metadata["kind"],
            year=int(metadata["year"]),
            month=int(metadata["month"]),
            document_id=int(metadata["document_id"]),
            parent_id=metadata.get("parent_id"),
            form_code=metadata.get("form_code"),
            form_name=metadata.get("form_name"),
            status=metadata.get("status", "downloaded"),
            relative_path=metadata["relative_path"],
            size_bytes=int(metadata["size_bytes"]),
            sha256=metadata["sha256"],
            xml_valid=bool(metadata["xml_valid"]),
            content_type=metadata.get("http_content_type"),
            downloaded_at=metadata.get("downloaded_at"),
            source_endpoint=metadata.get("source_endpoint"),
            error=None,
            api_record=metadata.get("api_record", {}),
        )

    def verify_existing(self, metadata_path: Path | str) -> dict[str, Any] | None:
        metadata_path = Path(metadata_path)
        try:
            metadata = json.loads(metadata_path.read_text("utf-8"))
            payload_path = self.data_root / metadata["relative_path"]
            payload = payload_path.read_bytes()
            if len(payload) != int(metadata["size_bytes"]):
                return None
            if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
                return None
            if metadata.get("xml_valid") is True:
                _validate_xml(payload)
            return metadata
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, InvalidXML):
            return None

    def existing_for(
        self,
        kind: str,
        year: int,
        month: int,
        document_id: int,
        parent_id: int | None,
        parent_year: int | None = None,
        parent_month: int | None = None,
    ) -> dict[str, Any] | None:
        directory = self.object_directory(
            kind, year, month, document_id, parent_id, parent_year, parent_month
        )
        metadata = self.verify_existing(directory / "metadata.json")
        if metadata is not None:
            self._upsert_metadata(metadata)
        return metadata

    def write_related_index(
        self,
        parent_year: int,
        parent_month: int,
        parent_id: int,
        records: list[dict[str, Any]],
        source_endpoint: str,
    ) -> Path:
        directory = self.object_directory(
            "primary", parent_year, parent_month, parent_id, None
        ) / "related"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "index.json"
        _atomic_json(
            path,
            {
                "schema_version": 1,
                "parent_year": parent_year,
                "parent_month": parent_month,
                "parent_id": parent_id,
                "source": "DPS private API",
                "source_endpoint": source_endpoint,
                "downloaded_at": _now(),
                "records": records,
            },
        )
        return path

    def read_related_index(
        self, parent_year: int, parent_month: int, parent_id: int
    ) -> list[dict[str, Any]] | None:
        path = self.object_directory(
            "primary", parent_year, parent_month, parent_id, None
        ) / "related" / "index.json"
        try:
            value = json.loads(path.read_text("utf-8"))
            records = value["records"]
            return records if isinstance(records, list) else None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def reindex(self) -> dict[str, int]:
        result = {"valid": 0, "invalid": 0}
        if not self.archive_root.exists():
            return result
        for metadata_path in self.archive_root.rglob("metadata.json"):
            metadata = self.verify_existing(metadata_path)
            if metadata is None:
                result["invalid"] += 1
                continue
            self._upsert_metadata(metadata)
            if metadata.get("kind") == "primary":
                record = metadata.get("api_record") or {
                    "codRegdoc": metadata["document_id"],
                    "periodYear": metadata["year"],
                    "periodMonth": metadata["month"],
                    "doc": metadata.get("form_code"),
                    "docName": metadata.get("form_name"),
                }
                self.state.import_document_record(
                    record,
                    fallback_year=int(metadata["year"]),
                    fallback_month=int(metadata["month"]),
                )
            result["valid"] += 1
        return result

    def part_files(self) -> list[Path]:
        if not self.archive_root.exists():
            return []
        return sorted(self.archive_root.rglob("*.part"))

    def cleanup_part_files(self) -> int:
        files = self.part_files()
        for path in files:
            path.unlink(missing_ok=True)
        return len(files)
