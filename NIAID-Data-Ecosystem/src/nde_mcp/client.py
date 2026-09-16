"""Thin async HTTP client for the NIAID Data Ecosystem (NDE) BioThings API."""

from __future__ import annotations

import os
from typing import Any

import httpx

from . import __version__

DEFAULT_BASE_URL = "https://api.data.niaid.nih.gov/v1"

# The portal's human-facing detail page, used to build provenance links.
PORTAL_RESOURCE_URL = "https://data.niaid.nih.gov/resources?id={_id}"

# Hard limits enforced by the upstream API. Exceeding them returns HTTP 400,
# so we clamp rather than let the model discover them by trial and error.
MAX_SIZE = 1000
MAX_RESULT_WINDOW = 10_000


class NDEError(RuntimeError):
    """An error reported by the NDE API or by the transport underneath it."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _format_api_error(status: int, payload: Any) -> str:
    """Turn an NDE error body into something a model can act on.

    Error bodies look like::

        {"code": 400, "success": false, "error": "search_phase_execution_exception",
         "root_cause_line_00": "Failed to parse query [[unclosed]"}
    """
    if not isinstance(payload, dict):
        return f"NDE API returned HTTP {status}: {str(payload)[:500]}"

    parts = [str(payload.get("error", f"HTTP {status}"))]
    causes = [v for k, v in sorted(payload.items()) if k.startswith("root_cause")]
    parts.extend(str(c) for c in causes)

    # Validation errors carry the offending parameter and its bound.
    if "keyword" in payload:
        bound = payload.get("max", payload.get("min"))
        parts.append(f"parameter {payload['keyword']!r} out of range (limit {bound})")

    return f"NDE API error (HTTP {status}): " + "; ".join(p for p in parts if p)


class NDEClient:
    """Async client over the handful of NDE endpoints this server needs."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("NDE_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else float(os.environ.get("NDE_TIMEOUT", "60"))
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "NDEClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": f"nde-mcp/{__version__} (+https://github.com/NIAID-BRC-Codeathons)",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        client = self._ensure_client()
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise NDEError(
                f"Request to {url} timed out after {self.timeout}s. "
                "Large facet or fetch_all queries can be slow; try narrowing the query."
            ) from exc
        except httpx.HTTPError as exc:
            raise NDEError(f"Could not reach the NDE API at {url}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            if response.is_success:
                raise NDEError(f"NDE API returned non-JSON content from {url}")
            raise NDEError(
                f"NDE API returned HTTP {response.status_code} from {url}: {response.text[:500]}",
                status=response.status_code,
            ) from None

        # The API signals failure both via HTTP status and a body-level flag.
        if not response.is_success or (isinstance(payload, dict) and payload.get("success") is False):
            raise NDEError(
                _format_api_error(response.status_code, payload),
                status=response.status_code,
            )
        return payload

    async def query(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET /query -- the main search endpoint."""
        return await self._request("GET", "/query", params=_clean_params(params))

    async def post_query(self, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        """POST /query -- batch lookup of many terms in one round trip."""
        return await self._request(
            "POST", "/query", json=body, params=_clean_params(params or {})
        )

    async def metadata(self) -> dict[str, Any]:
        """GET /metadata -- per-source record counts, versions, and descriptions."""
        return await self._request("GET", "/metadata")

    async def fields(self, prefix: str | None = None, search: str | None = None) -> dict[str, Any]:
        """GET /metadata/fields -- the searchable field index."""
        return await self._request(
            "GET", "/metadata/fields", params=_clean_params({"prefix": prefix, "search": search})
        )


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None values and render booleans the way the API expects."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            if not value:
                continue
            out[key] = ",".join(str(v) for v in value)
        else:
            out[key] = value
    return out
