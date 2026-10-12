"""Read-only DPS private API client with bounded retry and pagination proof."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping


API_ROOT = "https://cabinet.tax.gov.ua/ws/public_api"
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
AUTH_STATUSES = {401, 403}


class APIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, endpoint: str | None = None):
        super().__init__(message)
        self.status = status
        self.endpoint = endpoint


class AuthFatalError(APIError):
    pass


class PaginationSafetyError(APIError):
    pass


@dataclass(frozen=True)
class APIResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", self.headers.get("content-type", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> APIResponse:
    if method != "GET":
        raise ValueError("DPS recovery transport permits GET only")
    request = urllib.request.Request(url, method="GET", headers=dict(headers))
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return APIResponse(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        return APIResponse(error.code, dict(error.headers.items()), error.read())


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Retry-After", headers.get("retry-after"))
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class DPSClient:
    def __init__(
        self,
        authorization: str,
        *,
        transport: Callable[[str, str, Mapping[str, str], float], APIResponse] = _urllib_transport,
        sleeper: Callable[[float], None] = time.sleep,
        backoff: tuple[float, ...] = (2, 5, 10, 20, 60),
        timeout: float = 60,
        request_delay: float = 0.25,
        event_callback: Callable[[str, str], None] | None = None,
    ):
        self._authorization = authorization
        self._transport = transport
        self._sleep = sleeper
        self._backoff = backoff
        self.timeout = timeout
        self.request_delay = request_delay
        self._event = event_callback or (lambda level, message: None)
        self.request_count = 0

    def close(self) -> None:
        self._authorization = ""

    def _get(self, endpoint: str, query: dict[str, Any] | None = None) -> APIResponse:
        url = API_ROOT + endpoint
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Authorization": self._authorization,
            "Accept": "application/json, application/xml, application/octet-stream",
            "User-Agent": "DPS-XML-Recovery/1.0",
        }
        attempts = len(self._backoff) + 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            try:
                response = self._transport("GET", url, headers, self.timeout)
                self.request_count += 1
                self._event("INFO", f"GET {endpoint} HTTP {response.status}")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                last_error = error
                if attempt >= len(self._backoff):
                    raise APIError(f"network error after {attempts} attempts: {error}", endpoint=endpoint) from error
                delay = self._backoff[attempt]
                self._event("WARNING", f"retry network error endpoint={endpoint} delay={delay}s")
                self._sleep(delay)
                continue

            if response.status in AUTH_STATUSES:
                raise AuthFatalError(
                    f"DPS authorization rejected with HTTP {response.status}",
                    status=response.status,
                    endpoint=endpoint,
                )
            if response.status in TRANSIENT_STATUSES:
                if attempt >= len(self._backoff):
                    raise APIError(
                        f"HTTP {response.status} after {attempts} attempts",
                        status=response.status,
                        endpoint=endpoint,
                    )
                delay = max(self._backoff[attempt], _retry_after(response.headers) or 0.0)
                self._event("WARNING", f"retry HTTP {response.status} endpoint={endpoint} delay={delay}s")
                self._sleep(delay)
                continue
            if response.status != 200:
                excerpt = response.body[:300].decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
                raise APIError(
                    f"HTTP {response.status}: {excerpt}", status=response.status, endpoint=endpoint
                )
            if self.request_delay:
                self._sleep(self.request_delay)
            return response
        raise APIError(f"request failed: {last_error}", endpoint=endpoint)

    @staticmethod
    def _json(response: APIResponse, endpoint: str) -> Any:
        try:
            return json.loads(response.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise APIError(f"invalid JSON response: {error}", status=response.status, endpoint=endpoint) from error

    def list_page(self, year: int, month: int, page: int) -> dict[str, Any]:
        endpoint = "/reg_doc/list"
        query: dict[str, Any] = {"periodYear": year, "periodMonth": month}
        if page > 0:
            query["page"] = page
        payload = self._json(
            self._get(endpoint, query),
            endpoint,
        )
        required = {"content", "number", "size", "totalElements", "totalPages", "first", "last"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise APIError("inventory response lacks required pagination fields", endpoint=endpoint)
        if not isinstance(payload["content"], list):
            raise APIError("inventory content is not an array", endpoint=endpoint)
        if int(payload.get("numberOfElements", len(payload["content"]))) != len(payload["content"]):
            raise PaginationSafetyError("numberOfElements differs from content length", endpoint=endpoint)
        if int(payload["number"]) != page:
            raise PaginationSafetyError(
                f"requested page {page}, received page {payload['number']}", endpoint=endpoint
            )
        return payload

    def probe_pagination(self, year: int, month: int) -> dict[str, int]:
        first = self.list_page(year, month, page=0)
        if int(first["totalPages"]) > 1:
            second = self.list_page(year, month, page=1)
            first_ids = {item.get("codRegdoc") for item in first["content"]}
            second_ids = {item.get("codRegdoc") for item in second["content"]}
            if None in first_ids or None in second_ids or first_ids & second_ids:
                raise PaginationSafetyError("DPS returned duplicate content across page 0 and page 1")
            if int(second["totalElements"]) != int(first["totalElements"]):
                raise PaginationSafetyError("pagination totals changed during the probe")
        return {
            "year": year,
            "month": month,
            "page_size": int(first["size"]),
            "total_elements": int(first["totalElements"]),
            "total_pages": int(first["totalPages"]),
        }

    def related_documents(self, year: int, document_id: int) -> list[dict[str, Any]]:
        endpoint = f"/reg_doc/docs/{year}/{document_id}"
        payload = self._json(self._get(endpoint), endpoint)
        if not isinstance(payload, list):
            raise APIError("related documents response is not an array", endpoint=endpoint)
        return payload

    def document_xml(self, year: int, document_id: int) -> APIResponse:
        endpoint = f"/reg_doc/doc/{year}/{document_id}/xml"
        return self._get(endpoint)
