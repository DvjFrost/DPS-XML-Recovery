import os
from pathlib import Path

from dps_gui.controller import InputValues, validate_inputs


def values(tmp_path: Path, **changes) -> InputValues:
    jks = tmp_path / "Ключі з пробілами" / "ключ.jks"
    jks.parent.mkdir(exist_ok=True)
    jks.write_bytes(b"fake")
    root = tmp_path / "Архів XML з пробілами"
    root.mkdir(exist_ok=True)
    data = dict(
        tax_id="12345678",
        jks_path=str(jks),
        alias_index=0,
        start_year=2020,
        end_year=2026,
        recovery_root=str(root),
        mode="full",
    )
    data.update(changes)
    return InputValues(**data)


def test_accepts_edrpou_rnokpp_unicode_and_long_paths(tmp_path):
    long_root = tmp_path / ("довгий каталог " * 12)
    long_root.mkdir()
    assert validate_inputs(values(tmp_path, recovery_root=str(long_root))).valid
    assert validate_inputs(values(tmp_path, tax_id="1234567890")).valid


def test_rejects_invalid_tax_id_and_year_ranges(tmp_path):
    assert "tax_id" in validate_inputs(values(tmp_path, tax_id="1234")).errors
    assert "start_year" in validate_inputs(values(tmp_path, start_year=2009)).errors
    assert "end_year" in validate_inputs(values(tmp_path, start_year=2026, end_year=2025)).errors
    assert "end_year" in validate_inputs(values(tmp_path, end_year=2027), current_year=2026).errors


def test_authenticated_modes_require_existing_jks_and_alias(tmp_path):
    result = validate_inputs(values(tmp_path, jks_path=str(tmp_path / "missing.jks")))
    assert "jks_path" in result.errors
    assert "alias_index" in validate_inputs(values(tmp_path, alias_index=None)).errors


def test_audit_mode_does_not_require_credentials(tmp_path):
    result = validate_inputs(
        values(tmp_path, mode="audit_only", tax_id="", jks_path="", alias_index=None)
    )
    assert result.valid


def test_rejects_protected_legacy_root_and_read_only_target(tmp_path, monkeypatch):
    protected = tmp_path / "recovery_v2"
    protected.mkdir()
    result = validate_inputs(values(tmp_path, recovery_root=str(protected)), protected_roots=(protected,))
    assert "recovery_root" in result.errors

    target = tmp_path / "readonly"
    target.mkdir()
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda path, mode: False if Path(path) == target else real_access(path, mode))
    result = validate_inputs(values(tmp_path, recovery_root=str(target)))
    assert "recovery_root" in result.errors
