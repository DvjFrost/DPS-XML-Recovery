from pathlib import Path

import pytest

from tools.inspect_pe import RuntimeReport, read_pe_machine


def _write_pe(path: Path, machine: int) -> None:
    data = bytearray(256)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\x00\x00"
    data[0x84:0x86] = machine.to_bytes(2, "little")
    path.write_bytes(data)


def test_read_pe_machine_x86(tmp_path: Path) -> None:
    pe = tmp_path / "x86.dll"
    _write_pe(pe, 0x014C)

    assert read_pe_machine(pe) == "x86"


def test_read_pe_machine_x64(tmp_path: Path) -> None:
    pe = tmp_path / "x64.pyd"
    _write_pe(pe, 0x8664)

    assert read_pe_machine(pe) == "x64"


def test_rejects_non_pe(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.bin"
    invalid.write_bytes(b"not a PE file")

    with pytest.raises(ValueError, match="PE"):
        read_pe_machine(invalid)


def test_runtime_report_rejects_mixed_architecture() -> None:
    report = RuntimeReport(
        python_machine="x64",
        pyd_machine="x86",
        dll_machine="x86",
        import_ok=False,
        dll_version="1.3.1.220",
    )

    assert report.compatible is False
    assert "architecture" in report.reason.lower()


def test_runtime_report_requires_successful_import() -> None:
    report = RuntimeReport(
        python_machine="x86",
        pyd_machine="x86",
        dll_machine="x86",
        import_ok=False,
        dll_version="1.3.1.220",
    )

    assert report.compatible is False
    assert "import" in report.reason.lower()
