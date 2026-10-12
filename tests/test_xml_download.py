import hashlib

import pytest

from test_xml import build_xml_url, save_exact_xml


def test_builds_exact_official_xml_endpoint():
    assert build_xml_url(2026, 123456789012345) == (
        "https://cabinet.tax.gov.ua/ws/public_api/"
        "reg_doc/doc/2026/123456789012345/xml"
    )


def test_saves_exact_response_bytes_and_reports_root(tmp_path):
    raw = b'<?xml version="1.0" encoding="UTF-8"?><DECLAR xmlns="urn:test"><A>1</A></DECLAR>'
    result = save_exact_xml(
        raw=raw,
        year=2026,
        cod_regdoc=123456789012345,
        form_code="J02/../001",
        output_dir=tmp_path,
    )

    assert result.path.read_bytes() == raw
    assert result.path.name == "2026_123456789012345_J02_.._001.xml"
    assert result.root_element == "{urn:test}DECLAR"
    assert result.size == len(raw)
    assert result.sha256 == hashlib.sha256(raw).hexdigest()


def test_invalid_xml_is_not_saved(tmp_path):
    with pytest.raises(ValueError, match="not well-formed XML"):
        save_exact_xml(
            raw=b"<DECLAR>",
            year=2026,
            cod_regdoc=1,
            form_code="J02001",
            output_dir=tmp_path,
        )

    assert list(tmp_path.iterdir()) == []


def test_rejects_invalid_year_or_document_id():
    with pytest.raises(ValueError):
        build_xml_url(1999, 1)
    with pytest.raises(ValueError):
        build_xml_url(2026, 0)
