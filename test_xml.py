"""Stage 2A: save exactly one original XML response, then stop."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from iit_eusign_ctypes import EUSignCP
from test_dps import NoRedirect, one_get, validate_period
from test_sign import encode_tax_id


API_ROOT = "https://cabinet.tax.gov.ua/ws/public_api"
OUTPUT_DIR = Path.cwd() / "test_output"
MAX_XML_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class SavedXML:
    path: Path
    root_element: str
    size: int
    sha256: str


def build_xml_url(year: int, cod_regdoc: int) -> str:
    if year < 2000 or year > 2100:
        raise ValueError("Year is outside the allowed range")
    if cod_regdoc <= 0:
        raise ValueError("codRegdoc must be a positive integer")
    return f"{API_ROOT}/reg_doc/doc/{year}/{cod_regdoc}/xml"


def _safe_form_code(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    cleaned = cleaned.strip(" .")
    return cleaned[:80] or "unknown_form"


def save_exact_xml(
    *,
    raw: bytes,
    year: int,
    cod_regdoc: int,
    form_code: str,
    output_dir: Path = OUTPUT_DIR,
) -> SavedXML:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Response is not well-formed XML: {exc}") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{year}_{cod_regdoc}_{_safe_form_code(form_code)}.xml"
    destination = output_dir / filename

    fd, temporary_name = tempfile.mkstemp(
        prefix=filename + ".", suffix=".tmp", dir=output_dir
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    return SavedXML(
        path=destination,
        root_element=root.tag,
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def one_xml_get(authorization: str, url: str):
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": authorization,
            "Accept": "application/xml, text/xml, */*",
            "User-Agent": "DPS-XML-Recovery-Prototype/2A",
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
        body = response.read(MAX_XML_BYTES + 1)
        if len(body) > MAX_XML_BYTES:
            raise RuntimeError("XML response exceeds the safety limit")
        return response.status, response.headers.get("Content-Type", ""), body


def _safe_error_excerpt(body: bytes) -> str:
    text = body[:500].decode("utf-8", errors="replace")
    return "".join(char if char.isprintable() else " " for char in text)


def main() -> int:
    tax_id = input("EDRPOU/RNOKPP: ")
    original = encode_tax_id(tax_id)
    year, month = validate_period(input("List year: "), input("List month: "))
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
            selected_alias = int(input("Select alias number: "))
            if selected_alias < 1 or selected_alias > len(aliases):
                raise ValueError("Alias number is outside the displayed range")
            api.read_jks_key(
                jks_path, aliases[selected_alias - 1], password
            )
            signed = api.sign_data_internal(original, append_certificate=True)
            verified = api.verify_data_internal(signed)
            if not hmac.compare_digest(verified.data, original):
                raise RuntimeError("STOP: recovered signed bytes differ from input")
            if not verified.certificate_present:
                raise RuntimeError("STOP: signer certificate is absent")
            authorization = signed.decode("ascii", errors="strict")

            print("Local verification passed; requesting the list once")
            list_status, list_type, list_body = one_get(
                authorization, year, month
            )
            print(f"List HTTP status: {list_status}")
            print(f"List Content-Type: {list_type}")
            if list_status != 200:
                print(f"Safe error: {_safe_error_excerpt(list_body)}")
                print("STOP: XML request was not made")
                return 1
            payload = json.loads(list_body.decode("utf-8-sig"))
            records = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(records, list) or not records:
                raise RuntimeError("STOP: list response has no selectable records")

            print("Available documents:")
            for index, record in enumerate(records, start=1):
                print(
                    f"  {index}: id={record.get('codRegdoc')} "
                    f"form={record.get('doc')} "
                    f"name={record.get('docName')}"
                )
            selected_record = int(input("Select ONE ordinary report number: "))
            if selected_record < 1 or selected_record > len(records):
                raise ValueError("Document number is outside the displayed range")
            record = records[selected_record - 1]
            cod_regdoc = int(record["codRegdoc"])
            document_year = int(record.get("periodYear", year))
            form_code = str(record.get("doc") or "unknown_form")
            document_name = str(record.get("docName") or "")

            xml_url = build_xml_url(document_year, cod_regdoc)
            print("Performing exactly one XML GET")
            status, content_type, raw_xml = one_xml_get(authorization, xml_url)
            print(f"HTTP status: {status}")
            print(f"Content-Type: {content_type}")
            if status != 200:
                print(f"Safe error: {_safe_error_excerpt(raw_xml)}")
                print("STOP: no other document will be tried")
                return 1

            saved = save_exact_xml(
                raw=raw_xml,
                year=document_year,
                cod_regdoc=cod_regdoc,
                form_code=form_code,
            )
            print(f"Response bytes: {saved.size}")
            print(f"Root XML element: {saved.root_element}")
            print(f"SHA-256: {saved.sha256}")
            print(f"Saved path: {saved.path}")
            print(f"Form code: {form_code}")
            print(f"Form name: {document_name}")
            print(f"Document id: {cod_regdoc}")
            print("STOP: exactly one XML was saved; no further requests")
    finally:
        password = ""
        raw_path = ""
        tax_id = ""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
