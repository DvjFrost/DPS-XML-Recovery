from pathlib import Path

from tools.verify_iit_runtime import probe_runtime


def test_probe_runtime_reports_missing_official_wrapper(tmp_path: Path) -> None:
    report = probe_runtime(
        python_exe=tmp_path / "python.exe",
        pyd=tmp_path / "_EUSignCP.pyd",
        dll=tmp_path / "EUSignCP.dll",
    )

    assert report.compatible is False
    assert report.status == "missing_files"
    assert report.missing == (
        str(tmp_path / "python.exe"),
        str(tmp_path / "_EUSignCP.pyd"),
        str(tmp_path / "EUSignCP.dll"),
    )
