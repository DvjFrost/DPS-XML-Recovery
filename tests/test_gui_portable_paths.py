import json
from pathlib import Path

import pytest

from dps_gui import portable_paths
from dps_gui.settings import GUISettings, load_settings, save_settings_atomic


def test_uses_writable_portable_data_next_to_executable(tmp_path):
    result = portable_paths.resolve_portable_data(tmp_path, None)
    assert result == tmp_path / "portable-data"
    assert result.is_dir()


def test_explicit_unicode_long_path_wins(tmp_path):
    target = tmp_path / " ".join(["дані з пробілами"] * 8)
    result = portable_paths.resolve_portable_data(tmp_path / "app", target)
    assert result == target.resolve()
    assert result.is_dir()


def test_read_only_adjacent_directory_requires_explicit_path_without_appdata(tmp_path, monkeypatch):
    executable_dir = tmp_path / "readonly-app"
    executable_dir.mkdir()
    monkeypatch.setattr(portable_paths, "_ensure_writable", lambda path: False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "must-not-be-used"))
    with pytest.raises(portable_paths.PortableDataError, match="недоступен для записи"):
        portable_paths.resolve_portable_data(executable_dir, None)
    assert not (tmp_path / "must-not-be-used").exists()


def test_settings_roundtrip_is_atomic_and_jks_is_opt_in(tmp_path, monkeypatch):
    path = tmp_path / "portable-data" / "settings.json"
    replace_calls = []
    real_replace = portable_paths.os.replace

    def observed_replace(source, target):
        replace_calls.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr("dps_gui.settings.os.replace", observed_replace)
    settings = GUISettings(
        language="ru", tax_id="12345678", start_year=2020, end_year=2026,
        recovery_root=str(tmp_path / "Архів"), mode="full",
        remember_jks=False, jks_path=str(tmp_path / "секретний ключ.jks"),
    )
    save_settings_atomic(settings, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "jks_path" not in document
    assert replace_calls and replace_calls[0][1] == path
    assert not list(path.parent.glob("*.tmp"))
    loaded = load_settings(path)
    assert loaded.tax_id == "12345678"
    assert loaded.jks_path is None

    save_settings_atomic(GUISettings(remember_jks=True, jks_path=r"D:\Ключі\key.jks"), path)
    assert load_settings(path).jks_path == r"D:\Ключі\key.jks"


def test_corrupt_or_unknown_settings_are_safe_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    assert load_settings(path) == GUISettings()
    path.write_text(json.dumps({"password": "NO", "authorization": "NO", "mode": "full"}), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.mode == "full"
    assert not hasattr(loaded, "password")
