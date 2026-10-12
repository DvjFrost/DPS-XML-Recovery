"""Interactive local sign/verify test. It performs no HTTP request."""

from __future__ import annotations

import hashlib
import hmac
from getpass import getpass
from pathlib import Path

from iit_eusign_ctypes import EUSignCP


def encode_tax_id(value: str) -> bytes:
    if len(value) not in (8, 10) or not value.isascii() or not value.isdigit():
        raise ValueError("Enter exactly 8 or 10 ASCII digits without whitespace")
    return value.encode("ascii")


def main() -> int:
    tax_id = input("EDRPOU/RNOKPP: ")
    original = encode_tax_id(tax_id)
    raw_path = input("JKS path (local): ")
    jks_path = Path(raw_path)
    if not jks_path.is_file():
        raise FileNotFoundError("The selected JKS file does not exist")
    password = getpass("JKS/private-key password: ")
    try:
        with EUSignCP() as api:
            aliases = api.list_jks_aliases(jks_path)
            if not aliases:
                raise RuntimeError("No private-key aliases found in JKS")
            for index, alias in enumerate(aliases, start=1):
                print(f"  {index}: {alias.decode('cp1251', errors='replace')}")
            selected = int(input("Select alias number: "))
            if selected < 1 or selected > len(aliases):
                raise ValueError("Alias number is outside the displayed range")
            cert = api.read_jks_key(jks_path, aliases[selected - 1], password)
            signed = api.sign_data_internal(original, append_certificate=True)
            verified = api.verify_data_internal(signed)
            if not hmac.compare_digest(verified.data, original):
                raise RuntimeError("STOP: recovered signed bytes differ from input")
            if not verified.certificate_present:
                raise RuntimeError("STOP: signer certificate is absent from signature")
            authorization = signed.decode("ascii", errors="strict")
            digest = hashlib.sha256(authorization.encode("ascii")).hexdigest()
            print("Local IIT verification succeeded")
            print(f"Signer CN: {verified.signer_common_name or cert.common_name}")
            print(f"Recovered bytes: {verified.data!r}")
            print(f"Authorization prefix: {authorization[:4]}")
            print(f"Authorization length: {len(authorization)}")
            print(f"Authorization SHA-256: {digest}")
            print("STOP: no DPS request was made")
    finally:
        password = ""
        raw_path = ""
        tax_id = ""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
