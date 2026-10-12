from __future__ import annotations

import hashlib
import json

import pytest

from dps_archive import ArchiveStore, InvalidXML
from dps_recovery_state import RecoveryState


XML = b'<?xml version="1.0" encoding="UTF-8"?><DECLAR><TIN>12345678</TIN></DECLAR>'


def test_commit_preserves_exact_bytes_and_writes_portable_metadata(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        archive = ArchiveStore(tmp_path, state)
        metadata = archive.commit_payload(
            kind="primary",
            year=2026,
            month=7,
            document_id=10,
            parent_id=None,
            form_code="J0500111",
            form_name="Test form",
            api_record={"codRegdoc": 10},
            source_endpoint="/reg_doc/doc/2026/10/xml",
            content_type="application/octet-stream",
            payload=XML,
        )

        payload_path = tmp_path / metadata["relative_path"]
        assert payload_path.read_bytes() == XML
        assert metadata["sha256"] == hashlib.sha256(XML).hexdigest()
        assert metadata["xml_valid"] is True
        assert json.loads(payload_path.with_name("metadata.json").read_text("utf-8"))["document_id"] == 10
        assert not list(tmp_path.rglob("*.part"))
        assert state.get_object("primary:2026:10")["status"] == "downloaded"


def test_invalid_xml_never_replaces_final_file(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        archive = ArchiveStore(tmp_path, state)
        with pytest.raises(InvalidXML):
            archive.commit_payload(
                kind="primary", year=2026, month=7, document_id=10, parent_id=None,
                form_code="J", form_name="Bad", api_record={}, source_endpoint="/xml",
                content_type="application/octet-stream", payload=b"<broken>",
            )
        assert not (tmp_path / "archive/2026/07/10/document.xml").exists()


def test_verify_existing_rejects_corrupt_payload(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        archive = ArchiveStore(tmp_path, state)
        metadata = archive.commit_payload(
            kind="primary", year=2026, month=7, document_id=10, parent_id=None,
            form_code="J", form_name="Form", api_record={}, source_endpoint="/xml",
            content_type="application/xml", payload=XML,
        )
        path = tmp_path / metadata["relative_path"]
        path.write_bytes(b"corrupt")
        assert archive.verify_existing(path.with_name("metadata.json")) is None


def test_reindex_rebuilds_empty_sqlite_from_archive(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with RecoveryState(db_path, tmp_path) as state:
        ArchiveStore(tmp_path, state).commit_payload(
            kind="related", year=2025, month=12, document_id=20, parent_id=10,
            form_code="J1499201", form_name="Receipt", api_record={"codRegdocRef": 10},
            source_endpoint="/reg_doc/doc/2025/20/xml", content_type="application/xml", payload=XML,
        )
    db_path.unlink()

    with RecoveryState(db_path, tmp_path) as rebuilt:
        stats = ArchiveStore(tmp_path, rebuilt).reindex()
        rebuilt.export_all()
        restored = rebuilt.get_object("related:2025:10:20")

    assert stats == {"valid": 1, "invalid": 0}
    assert restored["sha256"] == hashlib.sha256(XML).hexdigest()
    assert json.loads((tmp_path / "manifest.json").read_text("utf-8"))["objects"][0]["document_id"] == 20


def test_reindex_reconstructs_primary_inventory_from_metadata(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    api_record = {
        "codRegdoc": 10,
        "periodYear": 2026,
        "periodMonth": 7,
        "doc": "J0500111",
        "docName": "Form",
    }
    with RecoveryState(db_path, tmp_path) as state:
        ArchiveStore(tmp_path, state).commit_payload(
            kind="primary", year=2026, month=7, document_id=10, parent_id=None,
            form_code="J0500111", form_name="Form", api_record=api_record,
            source_endpoint="/xml", content_type="application/xml", payload=XML,
        )
    db_path.unlink()

    with RecoveryState(db_path, tmp_path) as rebuilt:
        ArchiveStore(tmp_path, rebuilt).reindex()
        assert rebuilt.list_documents()[0]["cod_regdoc"] == 10
