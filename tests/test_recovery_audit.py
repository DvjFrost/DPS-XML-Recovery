from dps_archive import ArchiveStore
from dps_audit import audit_archive
from dps_recovery_state import RecoveryState


def record(doc_id):
    return {
        "codRegdoc": doc_id,
        "periodYear": 2026,
        "periodMonth": 7,
        "doc": "J0500111",
        "docName": "Form",
        "flagName": "Accepted",
    }


def test_audit_is_not_clean_when_api_total_exceeds_unique_inventory(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 7, 0, 1, [record(10), record(11)], total_elements=9)
        report = audit_archive(state, ArchiveStore(tmp_path, state))
        assert report.clean is False
        assert any("2026-07" in error and "totalElements=9" in error for error in report.errors)


def test_audit_accepts_one_request_checkpoint_for_empty_period(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(2026, 11, 0, 0, [], total_elements=0)
        report = audit_archive(state, ArchiveStore(tmp_path, state))
        assert report.clean is True
        assert not any("checkpoint invalid" in error for error in report.errors)
