"""Frozen executable dispatcher for the GUI and internal worker modes."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DPS XML Recovery")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--protocol-version", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--secret-handle", type=int, default=-1, help=argparse.SUPPRESS)
    parser.add_argument("--preflight-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--aliases-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--jks-path", help=argparse.SUPPRESS)
    return parser


def _run_gui() -> int:
    from .app import run_app

    return int(run_app())


def _run_worker(secret_handle: int = -1) -> int:
    from .worker_main import run_worker

    return int(run_worker(secret_handle))


def _run_preflight_json() -> int:
    from .iit_preflight import locate_iit_dll, run_iit_preflight
    try:
        report = run_iit_preflight(locate_iit_dll(None)).to_public_dict()
    except Exception:
        report = {"success": False}
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report.get("success") is True else 2


def _run_aliases_json(path: str | None) -> int:
    if not path:
        print('{"success":false}')
        return 2
    try:
        from iit_eusign_ctypes import EUSignCP
        with EUSignCP() as api:
            aliases = [value.decode("cp1251", errors="replace") for value in api.list_jks_aliases(path)]
        report = {"success": True, "aliases": aliases}
    except Exception:
        report = {"success": False, "aliases": []}
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["success"] else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.preflight_json:
        return _run_preflight_json()
    if args.aliases_json:
        return _run_aliases_json(args.jks_path)
    if args.worker:
        if args.protocol_version not in {None, 1}:
            return 2
        return _run_worker() if args.secret_handle == -1 else _run_worker(args.secret_handle)
    return _run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
