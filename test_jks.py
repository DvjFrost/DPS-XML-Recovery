"""Interactive JKS read test. It deliberately performs no signing or HTTP."""

from __future__ import annotations

from getpass import getpass
from pathlib import Path

from iit_eusign_ctypes import EUSignCP


def main() -> int:
    raw_path = input("JKS path (kept local and not logged): ")
    jks_path = Path(raw_path)
    if not jks_path.is_file():
        raise FileNotFoundError("The selected JKS file does not exist")

    password = getpass("JKS/private-key password: ")
    try:
        with EUSignCP() as api:
            aliases = api.list_jks_aliases(jks_path)
            if not aliases:
                raise RuntimeError("No private-key aliases found in JKS")
            print("Available key aliases:")
            for index, alias in enumerate(aliases, start=1):
                print(f"  {index}: {alias.decode("cp1251", errors="replace")}")
            selected = int(input("Select alias number: "))
            if selected < 1 or selected > len(aliases):
                raise ValueError("Alias number is outside the displayed range")
            summary = api.read_jks_key(jks_path, aliases[selected - 1], password)
            absent = "[not present]"
            print("JKS key read successfully")
            print(f"Certificate CN: {summary.common_name or absent}")
            print(f"Organization: {summary.organization or absent}")
            print(f"EDRPOU: {summary.edrpou or absent}")
            print(f"DRFO: {summary.drfo or absent}")
            print(f"Certificates in JKS entry: {summary.certificate_count}")
            print("STOP: private key will be reset; signing is not started")
    finally:
        password = ""
        raw_path = ""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
