import pytest

from dps_gui import entrypoint


def test_main_dispatches_to_gui_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, "_run_gui", lambda: calls.append("gui") or 11)
    monkeypatch.setattr(entrypoint, "_run_worker", lambda: calls.append("worker") or 22)

    assert entrypoint.main([]) == 11
    assert calls == ["gui"]


def test_main_dispatches_internal_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, "_run_gui", lambda: calls.append("gui") or 11)
    monkeypatch.setattr(entrypoint, "_run_worker", lambda: calls.append("worker") or 22)

    assert entrypoint.main(["--worker"]) == 22
    assert calls == ["worker"]


def test_worker_switch_is_hidden_from_normal_help():
    help_text = entrypoint._build_parser().format_help()

    assert "--worker" not in help_text


def test_unknown_argument_is_rejected():
    with pytest.raises(SystemExit) as error:
        entrypoint.main(["--unknown"])

    assert error.value.code == 2


def test_worker_dispatch_receives_secret_handle_and_protocol_version(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, "_run_worker", lambda handle: calls.append(handle) or 0)

    assert entrypoint.main(
        ["--worker", "--protocol-version", "1", "--secret-handle", "444"]
    ) == 0
    assert calls == [444]


def test_worker_rejects_unknown_protocol_before_start(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, "_run_worker", lambda handle: calls.append(handle) or 0)

    assert entrypoint.main(
        ["--worker", "--protocol-version", "99", "--secret-handle", "444"]
    ) == 2
    assert calls == []
