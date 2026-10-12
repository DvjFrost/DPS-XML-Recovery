from __future__ import annotations

from types import SimpleNamespace

import pytest

from dps_archive import ArchiveStore
from dps_recovery import create_verified_authorization
from dps_audit import audit_archive
from dps_recovery_state import RecoveryState


XML = b"<DECLAR/>"


class FakeAPI:
    def __init__(self, recovered=b"12345678", certificate_present=True):
        self.recovered = recovered
        self.certificate_present = certificate_present

    def sign_data_internal(self, data, append_certificate):
        assert data == b"12345678"
        assert append_certificate is True
        return b"MIIJ-safe-test"

    def verify_data_internal(self, signed):
        assert signed == b"MIIJ-safe-test"
        return SimpleNamespace(
            data=self.recovered,
            certificate_present=self.certificate_present,
            signer_common_name="Signer",
        )


def test_authorization_requires_exact_recovered_bytes_and_certificate():
    authorization, signer = create_verified_authorization(FakeAPI(), "12345678")
    assert authorization == "MIIJ-safe-test"
    assert signer == "Signer"
    with pytest.raises(RuntimeError, match="differ"):
        create_verified_authorization(FakeAPI(recovered=b"87654321"), "12345678")
    with pytest.raises(RuntimeError, match="certificate"):
        create_verified_authorization(FakeAPI(certificate_present=False), "12345678")


def test_audit_clean_archive_and_detects_part_file(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(
            2026, 7, 0, 1,
            [{"codRegdoc": 10, "periodYear": 2026, "periodMonth": 7, "doc": "J"}],
            total_elements=1,
        )
        archive = ArchiveStore(tmp_path, state)
        archive.commit_payload(
            kind="primary", year=2026, month=7, document_id=10, parent_id=None,
            form_code="J", form_name="Form", api_record={"codRegdoc": 10},
            source_endpoint="/xml", content_type="application/xml", payload=XML,
        )
        archive.write_related_index(2026, 7, 10, [{"codRegdoc": 10}], "/docs")
        clean = audit_archive(state, archive)
        assert clean.clean is True
        assert clean.primary_valid == 1

        part = tmp_path / "archive/2026/07/10/orphan.part"
        part.write_bytes(b"partial")
        dirty = audit_archive(state, archive)
        assert dirty.clean is False
        assert any(".part" in item for item in dirty.errors)


def test_audit_detects_missing_related_document(tmp_path):
    with RecoveryState(tmp_path / "state.sqlite3", tmp_path) as state:
        state.checkpoint_page(
            2026, 7, 0, 1,
            [{"codRegdoc": 10, "periodYear": 2026, "periodMonth": 7, "doc": "J"}],
            total_elements=1,
        )
        archive = ArchiveStore(tmp_path, state)
        archive.commit_payload(
            kind="primary", year=2026, month=7, document_id=10, parent_id=None,
            form_code="J", form_name="Form", api_record={"codRegdoc": 10},
            source_endpoint="/xml", content_type="application/xml", payload=XML,
        )
        archive.write_related_index(
            2026, 7, 10,
            [{"codRegdoc": 10}, {"codRegdoc": 20, "periodYear": 2025, "periodMonth": 12}],
            "/docs",
        )
        report = audit_archive(state, archive)
        assert report.clean is False
        assert any("related" in item and "20" in item for item in report.errors)

