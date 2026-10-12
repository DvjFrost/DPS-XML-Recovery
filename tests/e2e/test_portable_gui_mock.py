from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import ctypes
from pathlib import Path

import pytest

from dps_gui.path_contract import recovery_root_fingerprint


EXE = os.environ.get("DPS_ACCEPTANCE_EXE")
PUBLIC_EXE = os.environ.get("DPS_PUBLIC_EXE")
pytestmark = pytest.mark.skipif(not EXE, reason="DPS_ACCEPTANCE_EXE is not set")


def _start(root: Path, scenario: str, mode: str = "full"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "acceptance-scenario.json").write_text(
        json.dumps({"scenario": scenario}), encoding="utf-8"
    )
    process = subprocess.Popen(
        [EXE, "--worker", "--protocol-version", "1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        bufsize=1,
    )
    ready = json.loads(process.stdout.readline())
    assert ready["type"] == "worker_ready"
    command = {
        "protocol_version": 1,
        "job_id": "e2e-job",
        "type": "start",
        "payload": {"request": {
            "mode": mode, "root": str(root), "start_year": 2026, "end_year": 2025,
            "root_fingerprint": recovery_root_fingerprint(root),
        }},
    }
    process.stdin.write(json.dumps(command, ensure_ascii=True, separators=(",", ":")) + "\n")
    process.stdin.flush()
    return process


def _events(process, *, stop_after_first=False):
    result = []
    while True:
        line = process.stdout.readline()
        if not line:
            break
        event = json.loads(line)
        result.append(event)
        if stop_after_first and event["type"] == "progress":
            process.stdin.write(json.dumps({
                "protocol_version": 1, "job_id": "e2e-job", "type": "stop", "payload": {}
            }) + "\n")
            process.stdin.flush()
            stop_after_first = False
        if event["type"] in {"completed", "failed"}:
            break
    process.wait(timeout=15)
    return result


def _terminal(events):
    return next(event for event in reversed(events) if event["type"] in {"completed", "failed"})


@pytest.mark.parametrize(
    ("mode", "status", "terminal_type"),
    [("full", "audit_pass", "completed"), ("inventory_only", "inventory_completed", "completed"), ("audit_only", "audit_pass", "completed")],
)
def test_frozen_modes_and_unicode_path(tmp_path, mode, status, terminal_type):
    root = tmp_path / "Кирилиця Ї Є пробіли 🌍" / mode
    events = _events(_start(root, "happy", mode))
    terminal = _terminal(events)
    assert terminal["type"] == terminal_type
    assert terminal["payload"]["status"] == status
    if mode == "full":
        progress = [event["payload"] for event in events if event["type"] == "progress"]
        assert [(item["phase"], item["current"], item["total"]) for item in progress] == [
            ("inventory", 1, 3), ("inventory", 2, 3), ("inventory", 3, 3),
            ("download", 1, 3), ("download", 2, 3), ("download", 3, 3),
        ]


def test_frozen_stop_and_resume(tmp_path):
    root = tmp_path / "зупинка й відновлення"
    stopped = _events(_start(root, "stop_resume"), stop_after_first=True)
    assert _terminal(stopped)["payload"]["status"] == "stopped"
    resumed = _events(_start(root, "stop_resume"))
    first = next(event for event in resumed if event["type"] == "progress")
    assert first["payload"]["current"] == 2
    assert _terminal(resumed)["payload"]["summary"]["audit_pass"] is True


@pytest.mark.parametrize("scenario", ["wrong_password", "authorization_failed", "corrupt_state", "partial_xml"])
def test_frozen_failures_never_report_pass(tmp_path, scenario):
    events = _events(_start(tmp_path / scenario, scenario))
    terminal = _terminal(events)
    assert terminal["type"] == "failed"
    assert terminal["payload"]["summary"]["audit_pass"] is False


def test_frozen_worker_crash_has_no_false_pass_and_next_worker_runs(tmp_path):
    crashed = _start(tmp_path / "crash", "worker_crash")
    output, _ = crashed.communicate(timeout=15)
    assert crashed.returncode == 23
    assert '"audit_pass":true' not in output.casefold()
    recovered = _events(_start(tmp_path / "after crash", "happy"))
    assert _terminal(recovered)["payload"]["summary"]["audit_pass"] is True


def test_frozen_partial_download_is_visible_to_audit(tmp_path):
    root = tmp_path / "partial"
    events = _events(_start(root, "partial_xml"))
    terminal = _terminal(events)
    assert terminal["payload"]["summary"]["part_files"] == 1
    assert len(list(root.glob("*.part"))) == 1


def test_public_frozen_worker_runs_safe_audit_only(tmp_path):
    if not PUBLIC_EXE:
        pytest.skip("DPS_PUBLIC_EXE is not set")
    global EXE
    previous = EXE
    try:
        EXE = PUBLIC_EXE
        events = _events(_start(tmp_path / "публічний аудит", "ignored", "audit_only"))
    finally:
        EXE = previous
    terminal = _terminal(events)
    assert terminal["type"] == "completed"
    assert terminal["payload"]["status"] == "audit_pass"
    assert terminal["payload"]["summary"]["audit_pass"] is True


def test_public_frozen_gui_opens_and_closes_cleanly_from_unicode_path(tmp_path):
    if not PUBLIC_EXE:
        pytest.skip("DPS_PUBLIC_EXE is not set")
    source = Path(PUBLIC_EXE).parent
    target = tmp_path / "Збірка з пробілами Ї Є" / source.name
    shutil.copytree(source, target)
    executable = target / Path(PUBLIC_EXE).name
    process = subprocess.Popen([executable])
    user32 = ctypes.windll.user32
    found = {"hwnd": 0}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process.pid and user32.IsWindowVisible(hwnd):
            found["hwnd"] = hwnd
            return False
        return True

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not found["hwnd"] and process.poll() is None:
        user32.EnumWindows(callback, 0)
        time.sleep(0.05)
    assert found["hwnd"], "GUI window did not become visible"
    assert user32.PostMessageW(found["hwnd"], 0x0010, 0, 0)  # WM_CLOSE
    assert process.wait(timeout=15) == 0
