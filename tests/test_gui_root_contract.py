from __future__ import annotations

import io
import json

from dps_gui.model import CompletionResult, RecoveryRequest
from dps_gui.path_contract import recovery_root_fingerprint
from dps_gui.protocol import PROTOCOL_VERSION
from dps_gui.worker_main import worker_main


def test_root_fingerprint_is_unicode_exact_and_mojibake_changes_it(tmp_path):
    root = tmp_path / "Архів з пробілами Ї Є І 😀"
    root.mkdir()
    corrupted = str(root).encode("utf-8").decode("cp1251", errors="replace")

    assert recovery_root_fingerprint(root) != recovery_root_fingerprint(corrupted)


def test_worker_rejects_changed_root_before_runner_or_writes(tmp_path):
    selected = tmp_path / "Новая папка"
    selected.mkdir()
    corrupted = tmp_path / "РќРѕРІР°СЏ РїР°РїРєР°"
    request = RecoveryRequest(
        mode="full",
        root=str(corrupted),
        start_year=2026,
        end_year=2025,
        tax_id="12345678",
        jks_path="test.jks",
        alias_index=0,
        root_fingerprint=recovery_root_fingerprint(selected),
    )
    control = io.StringIO(json.dumps({
        "protocol_version": PROTOCOL_VERSION,
        "job_id": "job-root",
        "type": "start",
        "payload": {"request": request.__dict__},
    }) + "\n")
    output = io.StringIO()
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        return CompletionResult("job-root", True, "completed", 0)

    result = worker_main(control, output, -1, request_runner=runner)
    events = [json.loads(line) for line in output.getvalue().splitlines()]

    assert result == 2
    assert not called
    assert not corrupted.exists()
    assert events[-1]["payload"]["status"] == "invalid_recovery_root"
    assert events[-1]["payload"]["summary"]["error_type"] == "RecoveryRootMismatch"
