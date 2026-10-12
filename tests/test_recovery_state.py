from __future__ import annotations

import csv
import json

from dps_recovery_state import RecoveryState


def _record(doc_id: int, *, year: int = 2026, month: int = 7) -> dict:
    return {
        "codRegdoc": doc_id,
        "periodYear": year,
        "periodMonth": month,
        "doc": "J0500111",
        "docName": "Test form",
        "flagName": "Accepted",
    }


def test_checkpoint_page_is_transactional_and_deduplicates(tmp_path):
    state = RecoveryState(tmp_path / "state.sqlite3", tmp_path)
    state.checkpoint_page(2026, 7, page=0, total_pages=2, records=[_record(10)])
    state.checkpoint_page(2026, 7, page=1, total_pages=2, records=[_record(10), _record(11)])

    period = state.get_period(2026, 7)
    documents = state.list_documents()

    assert period["next_page"] == 2
    assert period["total_pages"] == 2
    assert period["complete"] is True
    assert [item["cod_regdoc"] for item in documents] == [10, 11]
    assert documents[0]["occurrences"] == 2
    pages = state.list_inventory_pages()
    assert [(item["page"], item["records_received"], item["cod_regdocs"]) for item in pages] == [
        (0, 1, [10]), (1, 2, [10, 11])
    ]


def test_exports_inventory_and_manifest_atomically(tmp_path):
    state = RecoveryState(tmp_path / "state.sqlite3", tmp_path)
    state.checkpoint_page(2026, 7, 0, 1, [_record(10)])
    state.upsert_object(
        object_key="primary:2026:10",
        kind="primary",
        year=2026,
        month=7,
        document_id=10,
        parent_id=None,
        form_code="J0500111",
        form_name="Test form",
        status="downloaded",
        relative_path="archive/2026/07/10/document.xml",
        size_bytes=4,
        sha256="abcd",
        xml_valid=True,
        content_type="application/xml",
        downloaded_at="2026-10-05T10:00:00+03:00",
        source_endpoint="/xml",
        api_record=_record(10),
    )

    state.export_all()

    inventory = json.loads((tmp_path / "inventory.json").read_text("utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert inventory["documents"][0]["cod_regdoc"] == 10
    assert manifest["objects"][0]["sha256"] == "abcd"
    assert not list(tmp_path.glob("*.tmp"))
    with (tmp_path / "inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.DictReader(handle))["cod_regdoc"] == "10"
    with (tmp_path / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.DictReader(handle))["object_key"] == "primary:2026:10"


def test_reopen_preserves_resume_checkpoint(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with RecoveryState(db_path, tmp_path) as state:
        state.checkpoint_page(2025, 12, 0, 3, [_record(20, year=2025, month=12)])

    with RecoveryState(db_path, tmp_path) as reopened:
        assert reopened.get_period(2025, 12)["next_page"] == 1
        assert reopened.get_period(2025, 12)["complete"] is False


def test_mark_period_error_always_revokes_complete_flag(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 0, 1, [_record(10)], total_elements=1)
        assert state.get_period(2026, 7)["complete"] is True
        state.mark_period_error(2026, 7, "incomplete")
        period = state.get_period(2026, 7)
        assert period["complete"] is False
        assert period["error"] == "incomplete"
