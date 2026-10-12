import json
import struct
import zipfile
from pathlib import Path

from dps_gui.package_policy import inspect_distribution, read_pe_machine


def test_release_sources_enforce_x86_onedir_and_reproducible_dependencies():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "dps_xml_recovery.spec").read_text(encoding="utf-8")
    lock = (root / "packaging" / "requirements-build.lock").read_text(encoding="utf-8")
    build = (root / "scripts" / "build-portable.ps1").read_text(encoding="utf-8")

    assert "upx=False" in spec
    assert "console=False" in spec
    assert "certifi" in spec
    assert "launcher.py" in spec
    assert "--hash=sha256:" in lock
    assert "--require-hashes" in build
    assert "32" in build
    assert "package_policy" in build
    assert "EUSignCP.dll" not in spec


def write_pe(path: Path, machine: int = 0x014C) -> None:
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path.write_bytes(data)


def make_clean_package(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "DPS-XML-Recovery-2.0.0-win-x86"
    (root / "_internal" / "_tcl_data").mkdir(parents=True)
    (root / "_internal" / "_tk_data").mkdir(parents=True)
    (root / "_internal" / "certifi").mkdir(parents=True)
    (root / "portable-data").mkdir()
    (root / "licenses").mkdir()
    write_pe(root / "DPSXmlRecovery.exe")
    (root / "_internal" / "_tcl_data" / "init.tcl").write_text("# tcl", encoding="utf-8")
    (root / "_internal" / "_tk_data" / "tk.tcl").write_text("# tk", encoding="utf-8")
    (root / "_internal" / "certifi" / "cacert.pem").write_text("CA", encoding="utf-8")
    (root / "portable-data" / "README.txt").write_text("empty", encoding="utf-8")
    (root / "README.txt").write_text("2.0.0", encoding="utf-8")
    (root / "README.md").write_text("2.0.0 українська", encoding="utf-8")
    (root / "README.ru.md").write_text("2.0.0 русский", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("2.0.0", encoding="utf-8")
    (root / "THIRD-PARTY-NOTICES.txt").write_text("Python\nPyInstaller", encoding="utf-8")
    (root / "LICENSE").write_text("MIT License", encoding="utf-8")
    (root / "sbom.json").write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    for name in (
        "CPython-LICENSE.txt", "TclTk-LICENSE.txt", "PyInstaller-LICENSE.txt",
        "pyinstaller-hooks-contrib-LICENSE.txt", "certifi-LICENSE.txt",
        "altgraph-LICENSE.txt", "packaging-LICENSE.txt", "pefile-LICENSE.txt",
        "pywin32-ctypes-LICENSE.txt", "setuptools-LICENSE.txt",
    ):
        (root / "licenses" / name).write_text("upstream license", encoding="utf-8")
    archive = tmp_path / f"{root.name}.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in root.rglob("*"):
            if path.is_file():
                output.write(path, f"{root.name}/{path.relative_to(root)}")
    return root, archive


def test_read_pe_machine_detects_x86_and_x64(tmp_path):
    x86, x64 = tmp_path / "x86.exe", tmp_path / "x64.exe"
    write_pe(x86, 0x014C)
    write_pe(x64, 0x8664)
    assert read_pe_machine(x86) == "x86"
    assert read_pe_machine(x64) == "x64"


def test_clean_package_and_zip_pass_all_guardrails(tmp_path):
    root, archive = make_clean_package(tmp_path)
    report = inspect_distribution(root, archive, expected_version="2.0.0")
    assert report["pass"] is True
    assert report["architecture"] == "x86"
    assert report["forbidden_files"] == []
    assert report["secret_hits"] == []
    assert report["zip_matches_directory"] is True


def test_rejects_iit_keys_user_data_secrets_and_nonempty_portable_data(tmp_path):
    root, archive = make_clean_package(tmp_path)
    (root / "EUSignCP.dll").write_bytes(b"iit")
    (root / "real-key.jks").write_bytes(b"key")
    (root / "archive.xml").write_text("<DECLAR/>", encoding="utf-8")
    (root / "state.sqlite3").write_bytes(b"SQLite format 3")
    (root / "download.log").write_text("Authorization: Bearer SECRET-TOKEN", encoding="utf-8")
    (root / "portable-data" / "settings.json").write_text("{}", encoding="utf-8")
    report = inspect_distribution(root, None, expected_version="2.0.0")
    assert report["pass"] is False
    assert any("EUSignCP.dll" in item for item in report["forbidden_files"])
    assert any("real-key.jks" in item for item in report["forbidden_files"])
    assert any("portable-data/settings.json" in item.replace("\\", "/") for item in report["forbidden_files"])
    assert report["secret_hits"]


def test_rejects_missing_runtime_docs_or_wrong_architecture(tmp_path):
    root, archive = make_clean_package(tmp_path)
    write_pe(root / "DPSXmlRecovery.exe", 0x8664)
    (root / "THIRD-PARTY-NOTICES.txt").unlink()
    (root / "_internal" / "certifi" / "cacert.pem").unlink()
    report = inspect_distribution(root, None, expected_version="2.0.0")
    assert report["pass"] is False
    assert report["architecture"] == "x64"
    assert "THIRD-PARTY-NOTICES.txt" in report["missing_required"]
    assert "trusted CA bundle" in report["missing_required"]


def test_release_requires_bilingual_readmes_and_changelog(tmp_path):
    root, _archive = make_clean_package(tmp_path)
    for name in ("README.md", "README.ru.md", "CHANGELOG.md"):
        (root / name).unlink()
    report = inspect_distribution(root, None, expected_version="2.0.0")
    assert "README.md" in report["missing_required"]
    assert "README.ru.md" in report["missing_required"]
    assert "CHANGELOG.md" in report["missing_required"]


def test_release_requires_redistributed_license_texts(tmp_path):
    root, _archive = make_clean_package(tmp_path)
    for name in ("CPython-LICENSE.txt", "PyInstaller-LICENSE.txt", "certifi-LICENSE.txt"):
        (root / "licenses" / name).unlink()
    report = inspect_distribution(root, None, expected_version="2.0.0")
    assert "licenses/CPython-LICENSE.txt" in report["missing_required"]
    assert "licenses/PyInstaller-LICENSE.txt" in report["missing_required"]
    assert "licenses/certifi-LICENSE.txt" in report["missing_required"]
