import json
import struct
from pathlib import Path

import pytest

from dps_gui import iit_preflight


def _write_pe(path: Path, machine: int = 0x014C) -> None:
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path.write_bytes(data)


def _valid_static(path: Path) -> iit_preflight.StaticIITReport:
    return iit_preflight.StaticIITReport(
        exists=True,
        architecture="x86",
        file_version="1.3.1.220",
        product_version="1.3.1.0",
        signature_valid=True,
        publisher_trusted=True,
        required_exports_present=True,
        missing_exports=(),
    )


def test_locate_missing_dll_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(iit_preflight, "DEFAULT_DLL_CANDIDATES", (tmp_path / "missing.dll",))

    with pytest.raises(iit_preflight.IITNotFoundError):
        iit_preflight.locate_iit_dll(None)


def test_locate_rejects_multiple_automatic_candidates(tmp_path, monkeypatch):
    first, second = tmp_path / "one.dll", tmp_path / "two.dll"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    monkeypatch.setattr(iit_preflight, "DEFAULT_DLL_CANDIDATES", (first, second))

    with pytest.raises(iit_preflight.IITMultipleCandidatesError):
        iit_preflight.locate_iit_dll(None)


def test_locate_accepts_explicit_existing_candidate(tmp_path):
    dll = tmp_path / "EUSignCP.dll"
    dll.write_bytes(b"candidate")

    assert iit_preflight.locate_iit_dll(dll) == dll.resolve()


def test_inspection_reports_wrong_machine_without_loading_dll(tmp_path, monkeypatch):
    dll = tmp_path / "EUSignCP.dll"
    _write_pe(dll, machine=0x8664)
    called = []
    monkeypatch.setattr(iit_preflight, "_read_versions", lambda _: ("1", "1"))
    monkeypatch.setattr(iit_preflight, "_verify_authenticode", lambda _: (True, True))
    monkeypatch.setattr(iit_preflight, "_probe_exports", lambda _: called.append(True) or ())

    report = iit_preflight.inspect_iit_dll(dll)

    assert report.architecture == "x64"
    assert not report.usable
    assert called == []


def test_inspection_rejects_untrusted_signature_without_loading(tmp_path, monkeypatch):
    dll = tmp_path / "EUSignCP.dll"
    _write_pe(dll)
    called = []
    monkeypatch.setattr(iit_preflight, "_read_versions", lambda _: ("1", "1"))
    monkeypatch.setattr(iit_preflight, "_verify_authenticode", lambda _: (False, False))
    monkeypatch.setattr(iit_preflight, "_probe_exports", lambda _: called.append(True) or ())

    report = iit_preflight.inspect_iit_dll(dll)

    assert not report.signature_valid
    assert not report.publisher_trusted
    assert not report.usable
    assert called == []


def test_inspection_reports_missing_required_export(tmp_path, monkeypatch):
    dll = tmp_path / "EUSignCP.dll"
    _write_pe(dll)
    monkeypatch.setattr(iit_preflight, "_read_versions", lambda _: ("1.3.1.220", "1.3.1.0"))
    monkeypatch.setattr(iit_preflight, "_verify_authenticode", lambda _: (True, True))
    monkeypatch.setattr(iit_preflight, "_probe_exports", lambda _: ("EUGetCMPSettings",))

    report = iit_preflight.inspect_iit_dll(dll)

    assert report.missing_exports == ("EUGetCMPSettings",)
    assert not report.required_exports_present
    assert not report.usable


def test_inspection_accepts_signed_x86_dll_with_all_exports(tmp_path, monkeypatch):
    dll = tmp_path / "EUSignCP.dll"
    _write_pe(dll)
    monkeypatch.setattr(iit_preflight, "_read_versions", lambda _: ("1.3.1.220", "1.3.1.0"))
    monkeypatch.setattr(iit_preflight, "_verify_authenticode", lambda _: (True, True))
    monkeypatch.setattr(iit_preflight, "_probe_exports", lambda _: ())

    report = iit_preflight.inspect_iit_dll(dll)

    assert report.usable
    assert report.to_public_dict()["file_version"] == "1.3.1.220"


class FakeBackend:
    def __init__(self, *, initialize_status=0, getter_failure=None):
        self.initialize_status = initialize_status
        self.getter_failure = getter_failure
        self.calls = []

    def initialize(self):
        self.calls.append("EUInitialize")
        return self.initialize_status

    def is_initialized(self):
        self.calls.append("EUIsInitialized")
        return True

    def does_need_set_settings(self):
        self.calls.append("EUDoesNeedSetSettings")
        return False

    def get_setting(self, export):
        self.calls.append(export)
        if export == self.getter_failure:
            raise OSError("SECRET-MARKER must not escape")
        return 0, {
            "enabled": True,
            "endpoint": "SECRET-MARKER",
            "password": b"SECRET-MARKER",
            "expire_time": 3600,
        }

    def finalize(self):
        self.calls.append("EUFinalize")


class RaisingInitializeBackend(FakeBackend):
    def initialize(self):
        self.calls.append("EUInitialize")
        raise OSError("SECRET-MARKER must not escape")


def test_runtime_calls_only_allowlisted_getters_and_finalizes(monkeypatch, tmp_path):
    backend = FakeBackend()
    monkeypatch.setattr(iit_preflight, "inspect_iit_dll", lambda _: _valid_static(tmp_path))

    report = iit_preflight.run_iit_preflight(
        tmp_path / "EUSignCP.dll", backend_factory=lambda _: backend
    )

    expected = [
        "EUInitialize",
        "EUIsInitialized",
        "EUDoesNeedSetSettings",
        *(spec.export for spec in iit_preflight.GETTER_SPECS),
        "EUFinalize",
    ]
    assert backend.calls == expected
    assert report.success
    assert all(not name.startswith("EUSet") for name in backend.calls)
    assert "EUGetPrivateKeyMediaSettings" not in backend.calls
    assert not any("Sign" in name and name != "EUInitialize" for name in backend.calls)


def test_runtime_finalizes_after_getter_failure_and_redacts_exception(monkeypatch, tmp_path):
    backend = FakeBackend(getter_failure="EUGetCMPSettings")
    monkeypatch.setattr(iit_preflight, "inspect_iit_dll", lambda _: _valid_static(tmp_path))

    report = iit_preflight.run_iit_preflight(
        tmp_path / "EUSignCP.dll", backend_factory=lambda _: backend
    )

    assert backend.calls[-1] == "EUFinalize"
    assert backend.calls.count("EUFinalize") == 1
    assert not report.success
    encoded = json.dumps(report.to_public_dict(), ensure_ascii=False)
    assert "SECRET-MARKER" not in encoded
    assert "password" not in encoded.lower()
    assert "endpoint" not in encoded.lower()


def test_runtime_does_not_finalize_when_initialize_returns_error(monkeypatch, tmp_path):
    backend = FakeBackend(initialize_status=17)
    monkeypatch.setattr(iit_preflight, "inspect_iit_dll", lambda _: _valid_static(tmp_path))

    report = iit_preflight.run_iit_preflight(
        tmp_path / "EUSignCP.dll", backend_factory=lambda _: backend
    )

    assert backend.calls == ["EUInitialize"]
    assert report.initialize_status == 17
    assert not report.success


def test_runtime_redacts_initialize_exception_and_does_not_finalize(monkeypatch, tmp_path):
    backend = RaisingInitializeBackend()
    monkeypatch.setattr(iit_preflight, "inspect_iit_dll", lambda _: _valid_static(tmp_path))

    report = iit_preflight.run_iit_preflight(
        tmp_path / "EUSignCP.dll", backend_factory=lambda _: backend
    )

    assert backend.calls == ["EUInitialize"]
    assert report.runtime_error
    assert "SECRET-MARKER" not in json.dumps(report.to_public_dict())


def test_runtime_does_not_load_incompatible_static_report(monkeypatch, tmp_path):
    static = iit_preflight.StaticIITReport(exists=False)
    monkeypatch.setattr(iit_preflight, "inspect_iit_dll", lambda _: static)
    loaded = []

    report = iit_preflight.run_iit_preflight(
        tmp_path / "missing.dll", backend_factory=lambda _: loaded.append(True)
    )

    assert loaded == []
    assert not report.success
