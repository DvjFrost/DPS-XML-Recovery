from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace


class FakeState:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        self.calls.append(("state_enter",))
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.calls.append(("state_exit", exc_type.__name__ if exc_type else None))

    def export_all(self):
        self.calls.append(("state_export_all",))


class FakeArchive:
    def __init__(self, calls):
        self.calls = calls

    def reindex(self):
        self.calls.append(("archive_reindex",))
        return {"valid": 3, "invalid": 0}

    def cleanup_part_files(self):
        self.calls.append(("archive_cleanup_parts",))
        return 0


class FakeClient:
    def __init__(self, calls):
        self.calls = calls

    def probe_pagination(self, year, month):
        self.calls.append(("probe", year, month))
        return {"year": year, "month": month, "total_elements": 9, "total_pages": 1}


class FakeEngine:
    def __init__(self, services, progress):
        self.services = services
        self.progress = progress

    def run_inventory(self):
        self.services.calls.append(("inventory",))
        if self.services.inventory_progress:
            if self.services.before_inventory_progress:
                self.services.before_inventory_progress()
            self.services.calls.append(("inventory_page_committed",))
            self.progress({
                "phase": "inventory", "page": 1, "total_pages": 1,
                "periods_checked": 0, "records_seen": 9, "unique_documents": 9,
            })
        return dict(self.services.inventory_stats)

    def run_download(self):
        self.services.calls.append(("download",))
        if self.services.before_download_progress:
            self.services.before_download_progress()
        self.services.calls.append(("download_parent_committed",))
        self.progress({"phase": "download", "primary_processed": 1, "primary_total": 4})
        return dict(self.services.download_stats)


class FakeServices:
    def __init__(self):
        self.calls = []
        self.inventory_stats = {"errors": 0, "unique_documents": 4}
        self.download_stats = {"primary_total": 4, "primary_downloaded": 4, "primary_errors": 0}
        self.audit_clean = True
        self.inventory_progress = False
        self.before_inventory_progress = None
        self.before_download_progress = None

    def setup_logging(self, path):
        self.calls.append(("setup_logging", path))

    def open_state(self, data_root, force_reindex):
        self.calls.append(("open_state", data_root, force_reindex))
        return FakeState(self.calls)

    def make_archive(self, data_root, state):
        self.calls.append(("make_archive", data_root, state))
        return FakeArchive(self.calls)

    @contextmanager
    def credential_session(self, request):
        self.calls.append(("credential_enter", request.tax_id, request.jks_path, request.alias_index, request.delay))
        try:
            yield FakeClient(self.calls)
        finally:
            self.calls.append(("credential_exit",))

    def make_engine(self, client, state, archive, *, start_year, end_year, progress):
        self.calls.append(("make_engine", client, state, archive, start_year, end_year))
        return FakeEngine(self, progress)

    def audit(self, state, archive):
        self.calls.append(("audit", state, archive))
        return SimpleNamespace(
            clean=self.audit_clean,
            to_dict=lambda: {"clean": self.audit_clean, "errors": [] if self.audit_clean else ["missing"]},
        )

    def write_audit_report(self, path, report):
        self.calls.append(("write_audit_report", path, report))
