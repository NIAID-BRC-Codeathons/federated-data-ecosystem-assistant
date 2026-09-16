"""Talking to E-utilities: identity, pacing, GET/POST, retries, status handling.

Every outbound request goes through ``EUtilsClient.request``. Nothing else in
this package touches the network, so the rate limit has exactly one place to be
enforced and provenance has exactly one place to be recorded.
"""

from __future__ import annotations

import os
import time
from typing import Any, NamedTuple
from urllib.parse import urlencode

import httpx2 as httpx
from typing_extensions import Self

from .envelope import NCBIError, check_for_error
from .limiter import RateLimiter

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI asks every program to identify itself. `tool` is the software, `email`
# is a human they can reach if this server starts misbehaving --- the
# developer, not the end user. They contact you before blocking you, but only
# if these are set.
TOOL_NAME = "fdea-ncbi-mcp"
DEFAULT_EMAIL = "jonathangunti@gmail.com"

# GET fails with HTTP 414 above a URL length measured between 2,137 and 4,151
# characters --- and the 414 body is EMPTY, so the status code is the only
# signal that anything went wrong. Rather than probe for the exact boundary,
# switch to POST well below it. NCBI documents POST for large id lists and the
# responses are byte-identical.
MAX_GET_URL_CHARS = 1800

# esummary rejects more than 500 UIDs per request, as HTTP 200 with a
# top-level "error". Callers chunk to this.
MAX_ESUMMARY_UIDS = 500

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


class TransportError(Exception):
    """A transport-level failure that survived retries."""


class Call(NamedTuple):
    """One outbound request, recorded for provenance.

    ``purpose`` separates the calls that answer the question from the ones
    that describe it: "coverage" marks a denominator lookup. Both are reported
    --- request cost is a scored criterion and hiding a call would understate
    it --- but only "primary" calls count toward which database produced the
    data.
    """

    utility: str
    db: str | None
    url: str
    purpose: str = "primary"


class EUtilsClient:
    """Shared, paced HTTP client for E-utilities.

    One instance per process. All methods are async: see the invariant note in
    ``server.py`` for why no synchronous entry point exists.
    """

    def __init__(self) -> None:
        self.api_key = os.environ.get("NCBI_API_KEY") or None
        self.email = os.environ.get("NCBI_EMAIL") or DEFAULT_EMAIL
        self.limiter = RateLimiter(has_api_key=bool(self.api_key))
        self._client: httpx.AsyncClient | None = None
        # Requests made during the current tool invocation, for provenance.
        self.call_log: list[Call] = []

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0),
                follow_redirects=True,
                headers={"User-Agent": f"{TOOL_NAME} (+{self.email})"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _identity(self) -> dict[str, str]:
        params = {"tool": TOOL_NAME, "email": self.email}
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    async def request(
        self,
        utility: str,
        params: dict[str, Any],
        *,
        db: str | None = None,
        purpose: str = "primary",
    ) -> tuple[str, str]:
        """Issue one E-utilities call. Returns ``(body_text, url_without_secrets)``.

        ``db`` is passed separately and required for every utility that takes
        one, because omitting it is the single most dangerous thing a caller
        can do here: esearch with no ``db`` does not error, it silently
        searches PubMed and returns plausible PubMed UIDs for what the caller
        believed was an SRA query.
        """
        query: dict[str, Any] = {k: v for k, v in params.items() if v is not None}
        if db is not None:
            query["db"] = db
        query.update(self._identity())

        url = f"{BASE_URL}/{utility}.fcgi"
        encoded = urlencode(query, doseq=True)
        use_post = len(url) + len(encoded) + 1 > MAX_GET_URL_CHARS

        # Recorded for provenance and shown to the agent, so the api_key is
        # stripped. Everything else is exactly what went on the wire.
        public_query = {k: v for k, v in query.items() if k != "api_key"}
        public_url = f"{url}?{urlencode(public_query, doseq=True)}"
        self.call_log.append(Call(utility, db, public_url, purpose))

        client = await self._get_client()
        last_status: int | None = None

        for attempt in range(MAX_ATTEMPTS):
            await self.limiter.acquire()
            try:
                if use_post:
                    response = await client.post(url, data=query)
                else:
                    response = await client.get(url, params=query)
            except Exception as exc:  # connect/read/timeout
                self.limiter.note_transport_error()
                if attempt == MAX_ATTEMPTS - 1:
                    raise TransportError(
                        f"Could not reach NCBI after {MAX_ATTEMPTS} attempts: "
                        f"{type(exc).__name__}. Check network connectivity."
                    ) from exc
                continue

            self.limiter.note_response(response.status_code, response.headers)
            last_status = response.status_code

            if response.status_code in RETRYABLE_STATUS:
                # The limiter already absorbed Retry-After; looping re-enters
                # acquire(), which waits it out. Never log the 429 body: it
                # echoes the caller's public IP in a field named "api-key".
                if attempt < MAX_ATTEMPTS - 1:
                    continue
                raise TransportError(_throttle_message(response.status_code))

            if response.status_code == 414:
                # Should be unreachable given MAX_GET_URL_CHARS, and the body
                # is empty so there is nothing to quote.
                raise TransportError(
                    "NCBI rejected the request URL as too long (HTTP 414). "
                    "Request fewer ids at once."
                )

            if response.status_code >= 400:
                raise TransportError(
                    f"NCBI returned HTTP {response.status_code} for {utility}."
                )

            return response.text, public_url

        raise TransportError(
            f"NCBI request failed after {MAX_ATTEMPTS} attempts "
            f"(last status {last_status})."
        )

    async def request_json(
        self,
        utility: str,
        params: dict[str, Any],
        *,
        db: str | None = None,
        purpose: str = "primary",
    ) -> dict[str, Any]:
        """Issue a call with ``retmode=json``, decode it, and screen for errors."""
        params = dict(params)
        params["retmode"] = "json"
        text, _url = await self.request(utility, params, db=db, purpose=purpose)
        try:
            payload = _loads(text)
        except ValueError as exc:
            raise NCBIError(
                f"NCBI returned a non-JSON response for {utility} "
                f"(first 200 chars: {text[:200]!r})"
            ) from exc
        # HTTP 200 does not mean success here. Three distinct error shapes can
        # be hiding in this body; check_for_error knows all three.
        check_for_error(payload)
        return payload

    def reset_call_log(self) -> list[Call]:
        """Take and clear the calls recorded so far. Called once per tool invocation."""
        calls, self.call_log = self.call_log, []
        return calls


def _loads(text: str) -> Any:
    import json

    return json.loads(text)


def _throttle_message(status: int) -> str:
    if status == 429:
        return (
            "NCBI rate limit hit and retries were exhausted. Requests are "
            "capped at 3/second per IP without an API key (10/second with "
            "one, set via NCBI_API_KEY). Wait a few seconds and retry. If "
            "several people share this network, each running copy of this "
            "server counts against the same limit --- lower NCBI_MAX_RPS."
        )
    return (
        f"NCBI returned HTTP {status} repeatedly. The service may be "
        "temporarily unavailable; retry in a minute."
    )


class Timer:
    """Wall-clock elapsed time for the provenance block."""

    def __enter__(self) -> Self:
        self._start = time.monotonic()
        self.elapsed_ms = 0
        return self

    def __exit__(self, *exc_info) -> None:
        self.elapsed_ms = int((time.monotonic() - self._start) * 1000)
