from __future__ import annotations

import json
from email.message import Message

import pytest

from dps_api import APIResponse, AuthFatalError, DPSClient, PaginationSafetyError


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, timeout):
        self.calls.append((method, url, dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(status: int, payload, headers=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return APIResponse(status=status, headers=headers or {}, body=body)


def page(number: int, ids: list[int], total_pages: int = 2):
    return {
        "content": [{"codRegdoc": item, "periodYear": 2026, "periodMonth": 7} for item in ids],
        "number": number,
        "size": 1,
        "totalElements": 2,
        "totalPages": total_pages,
        "first": number == 0,
        "last": number + 1 >= total_pages,
        "numberOfElements": len(ids),
    }


def test_client_only_issues_get_and_never_exposes_authorization_in_url():
    events = []
    transport = FakeTransport([response(200, page(0, [10], 1))])
    client = DPSClient(
        "SECRET", transport=transport, sleeper=lambda _: None,
        event_callback=lambda level, message: events.append((level, message)),
    )
    client.list_page(2026, 7, page=0)

    method, url, headers, _ = transport.calls[0]
    assert method == "GET"
    assert "SECRET" not in url
    assert headers["Authorization"] == "SECRET"
    assert "periodYear=2026" in url and "periodMonth=7" in url
    assert "page=" not in url and "size=" not in url
    assert any("HTTP 200" in message and "SECRET" not in message for _, message in events)


def test_transient_status_retries_and_honors_retry_after():
    sleeps = []
    transport = FakeTransport([
        response(429, b"busy", {"Retry-After": "7"}),
        response(200, page(0, [10], 1)),
    ])
    client = DPSClient(
        "SECRET", transport=transport, sleeper=sleeps.append, backoff=(2,), request_delay=0
    )
    client.list_page(2026, 7, 0)
    assert sleeps == [7.0]
    assert len(transport.calls) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_aborts_without_retry(status):
    transport = FakeTransport([response(status, b"denied")])
    client = DPSClient("SECRET", transport=transport, sleeper=lambda _: None)
    with pytest.raises(AuthFatalError):
        client.list_page(2026, 7, 0)
    assert len(transport.calls) == 1


def test_later_page_uses_only_page_parameter_and_never_size():
    transport = FakeTransport([response(200, page(1, [11]))])
    DPSClient("SECRET", transport=transport, sleeper=lambda _: None).list_page(2026, 7, 1)
    url = transport.calls[0][1]
    assert "page=1" in url
    assert "size=" not in url


def test_list_page_rejects_internal_count_mismatch():
    invalid = page(0, [10])
    invalid["numberOfElements"] = 2
    transport = FakeTransport([response(200, invalid)])
    with pytest.raises(PaginationSafetyError):
        DPSClient("SECRET", transport=transport, sleeper=lambda _: None).list_page(2026, 7, 0)


def test_related_and_xml_use_documented_get_endpoints():
    transport = FakeTransport([response(200, []), response(200, b"<DECLAR/>", {"Content-Type": "application/xml"})])
    client = DPSClient("SECRET", transport=transport, sleeper=lambda _: None)
    assert client.related_documents(2025, 10) == []
    xml = client.document_xml(2025, 20)
    assert xml.body == b"<DECLAR/>"
    assert "/reg_doc/docs/2025/10" in transport.calls[0][1]
    assert "/reg_doc/doc/2025/20/xml" in transport.calls[1][1]
