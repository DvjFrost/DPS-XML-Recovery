"""No-key ABI smoke test for the official x86 EUSignCP.dll."""

from __future__ import annotations

import struct
import sys

from iit_eusign_ctypes import DEFAULT_DLL, EUSignCP


def main() -> int:
    print(f"Python: {sys.version.split()[0]} ({struct.calcsize(chr(80)) * 8}-bit)")
    print(f"DLL: {DEFAULT_DLL.name} (installed x86 component)")
    with EUSignCP() as api:
        if not api.is_initialized():
            raise RuntimeError("EUIsInitialized returned FALSE after EUInitialize")
        print("ABI smoke test: Initialize/IsInitialized succeeded")
    print("ABI smoke test: Finalize/unload succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
