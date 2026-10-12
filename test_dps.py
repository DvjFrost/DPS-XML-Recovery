"""One-shot read-only DPS report-list request after local IIT verification."""

from __future__ import annotations

import hmac
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass
from pathlib import Path

from iit_eusign_ctypes import EUSignCP
from test_sign import encode_tax_id


BASE_URL = "https://cabinet.tax.gov.ua/ws/public_api/reg_doc/list"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_period(year_text: str, month_text: str) -> tuple[int, int]:
    if not year_text.isascii() or not year_text.isdigit():
        raise ValueError("Year must contain ASCII digits only")
    if not month_text.isascii() or not month_text.isdigit():
        raise ValueError("Month must contain ASCII digits only")
    year, month = int(year_text), int(month_text)
    if year < 2000 or year > 2100 or month < 1 or month > 12:
        raise ValueError("Period is outside the allowed range")
    return year, month


def build_url(year: int, month: int) -> str:
    query = urllib.parse.urlencode(
        {"periodYear": str(year), "periodMonth": str(month)}
    )
    return f"{BASE_URL}?{query}"


def summarize_json(payload):
    if isinstance(payload, dict):
        content = payload.get("content")
        records = len(content) if isinstance(content, list) else None
        first = content[0] if isinstance(content, list) and content else None
        safe_first = None
        if isinstance(first, dict):
            allowed = (
                "doc", "periodYear", "periodMonth", "period",
                "flagName", "docName", "cntAppendix",
            )
            safe_first = {key: first.get(key) for key in allowed if key in first}
        return {
            "json_type": "object",
            "keys": sorted(payload.keys()),
            "record_count": records,
            "totalElements": payload.get("totalElements"),
            "first_record_safe": safe_first,
        }
    if isinstance(payload, list):
        return {"json_type": "array", "record_count": len(payload)}
    return {"json_type": type(payload).__name__, "record_count": None}


def one_get(authorization: str, year: int, month: int):
    request = urllib.request.Request(
        build_url(year, month),
        method="GET",
        headers={
            "Authorization": authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "DPS-XML-Recovery-Prototype/1.0",
        },
    )
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Response exceeds the safety limit")
        return response.status, response.headers.get("Content-Type", ""), body


def main() -> int:
    tax_id = input("EDRPOU/RNOKPP: ")
    original = encode_tax_id(tax_id)
    year, month = validate_period(input("Period year: "), input("Period month: "))
    raw_path = input("JKS path (local): ")
    jks_path = Path(raw_path)
    if not jks_path.is_file():
        raise FileNotFoundError("The selected JKS file does not exist")
    password = getpass("JKS/private-key password: ")
    try:
        with EUSignCP() as api:
            aliases = api.list_jks_aliases(jks_path)
            for index, alias in enumerate(aliases, start=1):
                print(f"  {index}: {alias.decode('cp1251', errors='replace')}")
            selected = int(input("Select alias number: "))
            if selected < 1 or selected > len(aliases):
                raise ValueError("Alias number is outside the displayed range")
            api.read_jks_key(jks_path, aliases[selected - 1], password)
            signed = api.sign_data_internal(original, append_certificate=True)
            verified = api.verify_data_internal(signed)
            if not hmac.compare_digest(verified.data, original):
                raise RuntimeError("STOP: recovered signed bytes differ from input")
            if not verified.certificate_present:
                raise RuntimeError("STOP: signer certificate is absent")
            authorization = signed.decode("ascii", errors="strict")
            print("Local verification passed; performing exactly one GET")
            status, content_type, body = one_get(authorization, year, month)
            print(f"HTTP status: {status}")
            print(f"Content-Type: {content_type}")
            if "json" in content_type.lower():
                summary = summarize_json(json.loads(body.decode("utf-8-sig")))
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                print(f"Non-JSON response bytes: {len(body)}")
            print("STOP: no further requests will be made")
    finally:
        password = ""
        raw_path = ""
        tax_id = ""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
