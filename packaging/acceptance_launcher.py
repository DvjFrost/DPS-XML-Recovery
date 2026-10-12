"""Entry point for the offline-only frozen acceptance executable."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--protocol-version", type=int)
    parser.add_argument("--secret-handle", type=int, default=-1)
    args = parser.parse_args()
    if not args.worker or args.protocol_version not in {None, 1}:
        return 2
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")
    from dps_gui.acceptance_runtime import acceptance_request_runner
    from dps_gui.worker_main import worker_main

    return int(
        worker_main(
            sys.stdin,
            sys.stdout,
            args.secret_handle,
            request_runner=acceptance_request_runner,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
