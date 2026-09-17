"""NCBI Pathogen Detection Isolates Browser --- a different service, same IP budget.

Pathogen Detection is not E-utilities. Different host, different query language,
different response envelope, and none of ``eutils.py`` applies to it. What it
does share is **the rate limit**, because NCBI's 3/sec ceiling is per IP address
and NCBI-wide, so this client is handed the same ``RateLimiter`` instance rather
than being given one of its own. Two limiters on one process would each think
they had the whole budget and together spend double.

## The endpoint is undocumented

``https://www.ncbi.nlm.nih.gov/pathogens/pathogens-srv/`` is the backend the
Isolates Browser web UI calls, found hardcoded in the browser's own JavaScript
bundle. NCBI's *published* programmatic routes are BigQuery
(``ncbi-pathogen-detect.pdbrowser.isolates``) and FTP. This one works today,
needs no auth, and is CORS-open --- and it is unversioned with no deprecation
policy, so it can change or vanish without notice. Every claim below was
measured against the live service on 2026-09-16; nothing here is from
documentation, because there is none.

## Two behaviors that produce confidently wrong numbers

**``totalCount`` is roughly twice the isolate count.** The index stores two
byte-identical documents per isolate. Measured: a query pinned to one isolate
(``target_acc==["PDT000007747.3"]``) returns ``totalCount: 2`` and two identical
records. Across all S. aureus mecA/mecC isolates it is 188,671 rows for 94,336
distinct isolates. The official TSV download path duplicates too. So
``totalCount`` is never reported as an isolate count here --- the distinct count
comes from a ``target_acc`` facet's ``numBuckets``, which is measured to agree
exactly with the deduplicated FTP table (171,412 S. aureus isolates, both
sources). Records are deduplicated on ``target_acc`` on the way out.

Whether the duplication is a bug or intentional is **unverified**; the
``numBuckets`` workaround is measured to be correct, but it is a workaround, not
a documented feature.

**The HTTP status is always 200.** Every failure mode --- unknown collection,
malformed filter, bad sort, unknown action --- arrives as 200. Three of them do
not even return JSON:

===========================  ==================================================
Unknown/missing ``action``   200 with a **zero-byte body**
``limit=abc``                200 with a plain-text CGI ``Status: 500`` passthrough
Malformed ``sort``           200 with ``ERROR: CJsonObject:NextChar: ...``
Unknown collection/field     200 with ``{"success": false, "error": ...}``
No matching records          200 with ``success: true`` and ``totalCount: 0``
===========================  ==================================================

Never branch on the status code. ``_parse`` handles all five, and the last one
is a legitimate empty result, not an error.

**Asking for a facet empties ``content``.** Any ``facets`` parameter --- even one
on an unrelated field --- makes the same request return zero records while
``totalCount`` stays correct. Measured: ``limit=4`` with no facet returns 2 rows;
the identical request plus ``facets=taxgroup_name[||1|5]`` returns 0 rows and
``totalCount: 2``. This fails as "that filter matched nothing", which is a
plausible answer, so nothing announces it.

The consequence is that **rows and the echoed Solr query are mutually
exclusive** in one request: ``facets.query`` --- this service's equivalent of
E-utilities' ``querytranslation``, and the only place the implicit
``* -status:withdrawn`` is visible --- exists only when facets were requested,
and the ``facets`` key is absent from ``ngout`` entirely otherwise. Record
fetches therefore report no ``query_translation``; paying a second request to
get one is not worth it when the filter is echoed in the summary anyway.

``count_params`` and ``record_params`` exist so this is a choice made once in
this module rather than a landmine under every future call site.

## Rate limiting

No ``X-RateLimit-*`` headers, no ``Retry-After``, no documented limit; 15
back-to-back requests were served without throttling. ``api_key`` is accepted
and *ignored* --- a garbage key returns normal results, so this is not an
E-utilities-style keyed service and the key is not sent. Absence of an observed
limit is not absence of a limit, so this client stays behind the shared 3/sec
gate anyway.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from .envelope import NCBIError
from .eutils import MAX_ATTEMPTS, TOOL_NAME, Call, TransportError
from .limiter import RateLimiter

BASE_URL = "https://www.ncbi.nlm.nih.gov/pathogens/pathogens-srv/"

SOURCE = "NCBI Pathogen Detection"

# Measured with facets=taxgroup_name[||1|500]: 106 organism groups exist. The
# collection name is not a taxonomy db --- taxgroup_name is a curated grouping
# ("E.coli and Shigella" is one group), so it is matched as a literal string.
DEFAULT_COLLECTION = "isolates"

# The facet spec grammar is field[query|sortIdx|type|limit], reverse-engineered
# from the UI's minified JavaScript. Verified: [||1|1], [||1|20000] and [||0|50]
# work, and [||4] is rejected, so the 4th slot is required. What sortIdx and
# type fully mean is UNVERIFIED.
_DISTINCT_FACET = "target_acc[||1|1]"


class PathogensClient:
    """Requests to pathogens-srv, paced by a limiter it does not own."""

    def __init__(self, limiter: RateLimiter, email: str) -> None:
        # Shared, not constructed. See the module docstring: the 3/sec budget is
        # per IP and NCBI-wide, so this service and E-utilities draw on one gate.
        self.limiter = limiter
        self.email = email
        self.call_log: list[Call] = []
        self._client: httpx.AsyncClient | None = None

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

    async def retrieve(
        self,
        params: dict[str, Any],
        *,
        collection: str = DEFAULT_COLLECTION,
        purpose: str = "primary",
    ) -> dict[str, Any]:
        """One ``action=retrieve`` call. Returns the decoded ``ngout`` object."""
        query: dict[str, Any] = {
            k: v for k, v in params.items() if v is not None and v != ""
        }
        query["action"] = "retrieve"
        query["collection"] = collection

        url = f"{BASE_URL}?{urlencode(query)}"
        # No api_key is ever sent (it is ignored by this service), so unlike
        # eutils.py there is nothing to strip before logging the URL.
        self.call_log.append(
            Call("pathogens:retrieve", collection, url, purpose, SOURCE)
        )

        client = await self._get_client()
        for attempt in range(MAX_ATTEMPTS):
            await self.limiter.acquire()
            try:
                response = await client.get(BASE_URL, params=query)
            except Exception as exc:
                self.limiter.note_transport_error()
                if attempt == MAX_ATTEMPTS - 1:
                    raise TransportError(
                        f"Could not reach NCBI Pathogen Detection after "
                        f"{MAX_ATTEMPTS} attempts: {type(exc).__name__}. "
                        f"Check network connectivity."
                    ) from exc
                continue

            self.limiter.note_response(response.status_code, response.headers)

            # 200 is not success here and a non-200 is not necessarily failure,
            # but a 5xx is still worth retrying rather than parsing.
            if response.status_code >= 500 and attempt < MAX_ATTEMPTS - 1:
                continue

            return _parse(response.text)

        raise TransportError(
            f"NCBI Pathogen Detection request failed after {MAX_ATTEMPTS} attempts."
        )

    def reset_call_log(self) -> list[Call]:
        calls, self.call_log = self.call_log, []
        return calls


def _parse(text: str) -> dict[str, Any]:
    """Decode a pathogens-srv body. Every failure mode arrives as HTTP 200.

    Returns the ``ngout`` object. Raises ``NCBIError`` for anything else, so it
    reaches the agent through the same path as an E-utilities application error.
    """
    import json

    if not text.strip():
        # Measured: an unknown or missing `action` returns 200 and zero bytes.
        raise NCBIError(
            "NCBI Pathogen Detection returned an empty response, which it does "
            "when the request's 'action' is missing or unrecognized."
        )

    try:
        payload = json.loads(text)
    except ValueError as exc:
        # Measured: `limit=abc` returns a plain-text CGI "Status: 500"
        # passthrough, and a malformed `sort` returns "ERROR: CJsonObject...".
        # Neither is JSON and neither carries a non-200 status.
        raise NCBIError(
            f"NCBI Pathogen Detection returned a non-JSON response "
            f"(first 200 chars: {text[:200]!r})"
        ) from exc

    if not isinstance(payload, dict):
        raise NCBIError("NCBI Pathogen Detection returned an unexpected shape.")

    if payload.get("success") is False:
        # The service's only structured error, and it is the same opaque string
        # for an unknown collection, an unknown field, a malformed filter and a
        # negative limit --- so the message says what to check.
        detail = payload.get("error") or "no detail given"
        raise NCBIError(
            f"NCBI Pathogen Detection rejected the query ({detail}). This is "
            f"the service's only error message and it covers an unknown field "
            f"name, an unknown organism field value, and malformed filter "
            f"syntax alike. Check the field names with "
            f"ncbi_pathogen_amr_genes or ncbi_pathogen_organisms."
        )

    ngout = payload.get("ngout")
    if not isinstance(ngout, dict):
        raise NCBIError("NCBI Pathogen Detection returned a body with no 'ngout'.")
    return ngout


# --- query construction ---------------------------------------------------


def quote_value(value: str) -> str:
    """Validate one literal for the ``fq`` DSL.

    Values are interpolated into ``field==["value"]``, so a value containing a
    double quote would break out of the literal and silently change the query
    rather than erroring --- this service reports a malformed filter and an
    unknown field with the same opaque string, so a broken-out query can also
    come back as plausible wrong data.
    """
    if '"' in value or "\\" in value:
        raise NCBIError(
            f"{value!r} cannot be used as a Pathogen Detection filter value: "
            f"double quotes and backslashes are not escapable in the service's "
            f"filter syntax."
        )
    return value


def build_fq(clauses: dict[str, list[str]]) -> str | None:
    """Build an ``fq`` filter from ``{field: [value, ...]}``.

    The DSL is NCBI's own, not Solr: terms are ``field==["a","b"]`` (values OR
    together), and clauses are joined by a literal ``" and "``. Measured limits:
    ``!=`` is rejected outright, and a free-text ``q=="..."`` clause is
    **silently ignored** --- it returns the unfiltered total, which is the worst
    possible failure for a count. So only explicit field equality is built here.
    """
    parts = []
    for field, values in clauses.items():
        if not values:
            continue
        quoted = ",".join(f'"{quote_value(v)}"' for v in values)
        parts.append(f"{field}==[{quoted}]")
    return " and ".join(parts) if parts else None


# --- reading the response -------------------------------------------------


def distinct_count(ngout: dict[str, Any]) -> int | None:
    """The true isolate count, from the ``target_acc`` facet's ``numBuckets``.

    Returns None if the facet was not requested, rather than falling back to
    ``totalCount`` --- that number is ~2x the isolate count and returning it
    here would reintroduce exactly the error this function exists to prevent.
    """
    facets = ngout.get("facets")
    if not isinstance(facets, dict):
        return None
    target = facets.get("target_acc")
    if not isinstance(target, dict):
        return None
    buckets = target.get("numBuckets")
    return int(buckets) if buckets is not None else None


def raw_row_count(ngout: dict[str, Any]) -> int:
    """``totalCount``: the number of index rows, about twice the isolate count.

    Deliberately named so that nothing mistakes it for an isolate count.
    """
    data = ngout.get("data")
    if not isinstance(data, dict):
        return 0
    return int(data.get("totalCount") or 0)


def records(ngout: dict[str, Any]) -> list[dict[str, Any]]:
    """Deduplicated rows. See the module docstring: every isolate appears twice."""
    data = ngout.get("data")
    if not isinstance(data, dict):
        return []
    content = data.get("content")
    if not isinstance(content, list):
        return []

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in content:
        if not isinstance(row, dict):
            continue
        key = row.get("target_acc")
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        unique.append(row)
    return unique


def facet_buckets(ngout: dict[str, Any], field: str) -> list[dict[str, Any]]:
    """``[{"val": ..., "count": ...}, ...]`` for one faceted field.

    The counts here are raw row counts, so they carry the same 2x duplication as
    ``totalCount``. Callers that present them must say so or halve them.
    """
    facets = ngout.get("facets")
    if not isinstance(facets, dict):
        return []
    entry = facets.get(field)
    if not isinstance(entry, dict):
        return []
    buckets = entry.get("buckets")
    return (
        [b for b in buckets if isinstance(b, dict)] if isinstance(buckets, list) else []
    )


def solr_query(ngout: dict[str, Any]) -> str | None:
    """The translated Solr filters the service echoes back.

    The Pathogen Detection equivalent of E-utilities' ``querytranslation``, and
    it earns the same attention: it reveals the implicit ``* -status:withdrawn``
    applied to every query, and it is the only way to see that a filter was
    dropped rather than applied.
    """
    facets = ngout.get("facets")
    if not isinstance(facets, dict):
        return None
    query = facets.get("query")
    if isinstance(query, list):
        return " AND ".join(str(q) for q in query)
    return str(query) if query else None


def count_params(fq: str | None) -> dict[str, Any]:
    """Parameters for a count-only call: no records, just the distinct facet."""
    return {"limit": 0, "facets": _DISTINCT_FACET, "fq": fq}


def record_params(
    fq: str | None, *, limit: int, start: int = 0, fl: str | None = None
) -> dict[str, Any]:
    """Parameters for a record fetch. Never carries ``facets`` --- see the
    module docstring: one would silently empty ``content``.

    ``limit`` is doubled on the way out because the index stores every isolate
    twice, so asking for N rows yields about N/2 distinct isolates after
    ``records`` deduplicates them. Callers pass the isolate count they want.
    """
    return {"limit": limit * 2, "start": start, "fq": fq, "fl": fl}
