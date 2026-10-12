"""Distribution scanner shared by tests and release verification scripts."""

from __future__ import annotations

import hashlib
import argparse
import json
import re
import struct
import zipfile
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = frozenset({".txt", ".json", ".md", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".log"})
FORBIDDEN_SUFFIXES = frozenset({".jks", ".xml", ".sqlite", ".sqlite3", ".db", ".part", ".pfx", ".p12", ".key", ".cer", ".crt"})
SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer|basic)?\s*[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(?:password|passwd|passphrase|private[_ -]?key)\s*[:=]\s*\S{4,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\]+\\"),
    re.compile(r"(?i)D:\\DPS_XML_Recovery\\"),
)


def read_pe_machine(path: Path) -> str:
    try:
        with Path(path).open("rb") as stream:
            if stream.read(2) != b"MZ":
                return "unknown"
            stream.seek(0x3C)
            offset = struct.unpack("<I", stream.read(4))[0]
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                return "unknown"
            machine = struct.unpack("<H", stream.read(2))[0]
    except (OSError, struct.error):
        return "unknown"
    return {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(machine, "unknown")


def _relative_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def _forbidden(relative: str) -> bool:
    path = Path(relative)
    name = path.name.casefold()
    lower = relative.casefold().replace("\\", "/")
    if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        return True
    if name.endswith(".dll") and ("eusign" in name or "iit" in name):
        return True
    if name in {"settings.json", "state.sqlite3", "manifest.json", "audit-report.json"}:
        return True
    if name.endswith(".log"):
        return True
    if lower.startswith("portable-data/") and lower != "portable-data/readme.txt":
        return True
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_inventory(archive: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with zipfile.ZipFile(archive) as source:
        files = [item for item in source.infolist() if not item.is_dir()]
        if not files:
            return result
        first = files[0].filename.split("/", 1)[0]
        prefix = f"{first}/"
        for item in files:
            relative = item.filename[len(prefix):] if item.filename.startswith(prefix) else item.filename
            result[relative] = hashlib.sha256(source.read(item)).hexdigest()
    return result


def inspect_distribution(
    root: Path,
    archive: Path | None,
    *,
    expected_version: str,
) -> dict[str, Any]:
    root = Path(root)
    files = _relative_files(root) if root.is_dir() else {}
    missing: list[str] = []
    required = (
        "DPSXmlRecovery.exe",
        "README.txt",
        "README.md",
        "README.ru.md",
        "CHANGELOG.md",
        "THIRD-PARTY-NOTICES.txt",
        "LICENSE",
        "sbom.json",
        "licenses/CPython-LICENSE.txt",
        "licenses/TclTk-LICENSE.txt",
        "licenses/PyInstaller-LICENSE.txt",
        "licenses/pyinstaller-hooks-contrib-LICENSE.txt",
        "licenses/certifi-LICENSE.txt",
        "licenses/altgraph-LICENSE.txt",
        "licenses/packaging-LICENSE.txt",
        "licenses/pefile-LICENSE.txt",
        "licenses/pywin32-ctypes-LICENSE.txt",
        "licenses/setuptools-LICENSE.txt",
    )
    for name in required:
        if name not in files:
            missing.append(name)
    tcl_present = any(name.endswith("/init.tcl") and "tcl" in name.casefold() for name in files)
    tk_present = any(name.endswith("/tk.tcl") and "tk" in name.casefold() for name in files)
    ca_present = any(name.casefold().endswith("certifi/cacert.pem") for name in files)
    if not tcl_present:
        missing.append("Tcl runtime")
    if not tk_present:
        missing.append("Tk runtime")
    if not ca_present:
        missing.append("trusted CA bundle")
    readme = files.get("README.txt")
    if readme is not None:
        try:
            if expected_version not in readme.read_text(encoding="utf-8", errors="replace"):
                missing.append("version metadata")
        except OSError:
            missing.append("version metadata")

    forbidden = sorted(name for name in files if _forbidden(name))
    secret_hits: list[str] = []
    for relative, path in files.items():
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            secret_hits.append(relative)

    executable = files.get("DPSXmlRecovery.exe")
    architecture = read_pe_machine(executable) if executable else "missing"
    zip_matches: bool | None = None
    zip_error: str | None = None
    if archive is not None:
        try:
            directory_inventory = {name: _sha256(path) for name, path in files.items()}
            zip_inventory = _zip_inventory(Path(archive))
            zip_matches = directory_inventory == zip_inventory
        except (OSError, zipfile.BadZipFile):
            zip_matches = False
            zip_error = "invalid_zip"

    passed = (
        architecture == "x86"
        and not missing
        and not forbidden
        and not secret_hits
        and zip_matches is not False
    )
    return {
        "pass": passed,
        "architecture": architecture,
        "file_count": len(files),
        "missing_required": missing,
        "forbidden_files": forbidden,
        "secret_hits": sorted(secret_hits),
        "zip_matches_directory": zip_matches,
        "zip_error": zip_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a DPS XML Recovery release")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = inspect_distribution(args.root, args.archive, expected_version=args.version)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["inspect_distribution", "read_pe_machine"]
