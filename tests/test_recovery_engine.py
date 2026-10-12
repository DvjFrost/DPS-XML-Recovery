from __future__ import annotations

from dps_api import APIError, APIResponse
from dps_archive import ArchiveStore
from dps_recovery_engine import RecoveryEngine, iter_periods
from dps_recovery_state import RecoveryState


XML = b"<DECLAR><TIN>12345678</TIN></DECLAR>"


def record(doc_id, year=2026, month=7, parent=None, form="J0500111"):
    return {
        "codRegdoc": doc_id,
        "codRegdocRef": parent,
        "periodYear": year,
        "periodMonth": month,
        "doc": form,
        "docName": "Form",
        "flagName": "Accepted",
    }


def page(number, ids, total_pages):
    return {
        "content": [record(item) for item in ids],
        "number": number,
        "size": 20,
        "totalElements": sum(len(ids) for _ in range(total_pages)),
        "totalPages": total_pages,
        "first": number == 0,
        "last": number + 1 >= total_pages,
        "numberOfElements": len(ids),
    }


class FakeClient:
    def __init__(self):
        self.pages = {}
        self.related = {}
        self.xml = {}
        self.list_calls = []
        self.related_calls = []
        self.xml_calls = []

    def list_page(self, year, month, page):
        self.list_calls.append((year, month, page))
        value = self.pages[(year, month, page)]
        if isinstance(value, Exception):
            raise value
        return value

    def related_documents(self, year, document_id):
        self.related_calls.append((year, document_id))
        return self.related.get((year, document_id), [record(document_id, year)])

    def document_xml(self, year, document_id):
        self.xml_calls.append((year, document_id))
        value = self.xml.get((year, document_id), XML)
        if isinstance(value, Exception):
            raise value
        return APIResponse(200, {"Content-Type": "application/octet-stream"}, value)


def test_periods_descend_to_2010_and_never_earlier():
    periods = list(iter_periods(2026, 2010))
    assert periods[0] == (2026, 12)
    assert periods[-1] == (2010, 1)
    assert len(periods) == 17 * 12
    assert all(year >= 2010 for year, _ in periods)


def test_inventory_resumes_at_next_page(tmp_path):
    client = FakeClient()
    client.pages[(2026, 7, 1)] = page(1, [11], 2)
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 0, 2, [record(10)], total_elements=2)
        engine = RecoveryEngine(client, state, ArchiveStore(tmp_path, state), start_year=2026, end_year=2026)
        engine.run_inventory(periods=[(2026, 7)])
        assert client.list_calls == [(2026, 7, 1)]
        assert [item["cod_regdoc"] for item in state.list_documents()] == [10, 11]
        assert state.get_period(2026, 7)["complete"] is True
        assert (tmp_path / "inventory.json").exists()


def test_inventory_rejects_api_total_greater_than_unique_records(tmp_path):
    client = FakeClient()
    incomplete = page(0, [10, 11], 1)
    incomplete["totalElements"] = 9
    client.pages[(2026, 7, 0)] = incomplete
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        stats = RecoveryEngine(client, state, ArchiveStore(tmp_path, state)).run_inventory(periods=[(2026, 7)])
        period = state.get_period(2026, 7)
        assert stats["errors"] == 1
        assert period["complete"] is False
        assert "totalElements=9" in period["error"]


def test_inventory_rejects_impossible_resume_checkpoint(tmp_path):
    client = FakeClient()
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 2, 1, [record(10), record(11)], total_elements=9)
        stats = RecoveryEngine(client, state, ArchiveStore(tmp_path, state)).run_inventory(periods=[(2026, 7)])
        assert stats["errors"] == 1
        assert client.list_calls == []
        assert state.get_period(2026, 7)["complete"] is False


def test_inventory_accepts_and_repairs_empty_period_checkpoint(tmp_path):
    client = FakeClient()
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 11, 0, 0, [], total_elements=0)
        state.mark_period_error(2026, 11, "false positive from old guard")
        assert state.get_period(2026, 11)["complete"] is False

        stats = RecoveryEngine(client, state, ArchiveStore(tmp_path, state)).run_inventory(periods=[(2026, 11)])

        assert stats["errors"] == 0
        assert client.list_calls == []
        period = state.get_period(2026, 11)
        assert period["complete"] is True
        assert period["error"] is None


def test_download_saves_primary_and_related_using_related_year(tmp_path, caplog):
    caplog.set_level("INFO", logger="dps_recovery")
    client = FakeClient()
    client.related[(2026, 10)] = [record(10), record(20, year=2025, month=12, parent=10, form="J1499201")]
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 0, 1, [record(10)], total_elements=1)
        engine = RecoveryEngine(client, state, ArchiveStore(tmp_path, state))
        stats = engine.run_download()

        assert client.xml_calls == [(2026, 10), (2025, 20)]
        assert stats["primary_downloaded"] == 1
        assert stats["related_downloaded"] == 1
        assert (tmp_path / "archive/2026/07/10/document.xml").read_bytes() == XML
        assert (tmp_path / "archive/2026/07/10/related/20/document.xml").read_bytes() == XML
        messages = "\n".join(record.message for record in caplog.records)
        assert "document_id=10" in messages
        assert "form=J0500111" in messages
        assert "sha256=" in messages


def test_download_skips_valid_and_redownloads_corrupt(tmp_path):
    client = FakeClient()
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 0, 1, [record(10)], total_elements=1)
        archive = ArchiveStore(tmp_path, state)
        archive.commit_payload(
            kind="primary", year=2026, month=7, document_id=10, parent_id=None,
            form_code="J", form_name="Form", api_record=record(10), source_endpoint="/xml",
            content_type="application/xml", payload=XML,
        )
        engine = RecoveryEngine(client, state, archive)
        first = engine.run_download()
        assert first["primary_existing"] == 1
        assert client.xml_calls == []

        (tmp_path / "archive/2026/07/10/document.xml").write_bytes(b"bad")
        second = engine.run_download()
        assert second["primary_downloaded"] == 1
        assert client.xml_calls == [(2026, 10)]


def test_document_error_is_recorded_and_next_document_continues(tmp_path):
    client = FakeClient()
    client.xml[(2026, 10)] = APIError("temporary exhausted")
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 0, 1, [record(10), record(11)], total_elements=2)
        stats = RecoveryEngine(client, state, ArchiveStore(tmp_path, state)).run_download()
        assert stats["primary_errors"] == 1
        assert stats["primary_downloaded"] == 1
        assert state.get_object("primary:2026:10")["status"] == "error"
