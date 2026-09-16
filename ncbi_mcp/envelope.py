"""The result shape every tool returns, and the error shapes NCBI hides in HTTP 200.

Two separate jobs that belong together because they are both about "what did
the agent actually get back".

**Errors.** E-utilities reports application-level failures with HTTP 200 and an
error buried in the body, in three mutually exclusive shapes (all measured):

    {"esearchresult": {"ERROR": "Can't run executor"}}      # esearch
    {"esummaryresult": ["Invalid uid ... at position 0"]}   # esummary, a LIST
    {"header": {...}, "error": "UID list is empty"}         # top-level sibling

Transport-level failures are ordinary status codes (429, 414, 5xx) and are
handled in ``eutils.py``. Both checks are required: neither subsumes the other.

**Provenance.** The project README makes this a scored criterion --- the agent
must be able to show "its resource-selection rationale, generated queries, API
calls, and intermediate outputs rather than acting as an opaque chatbot". So
every result carries the exact URLs called and the query NCBI actually ran.
"""

from __future__ import annotations

from typing import Any

EUTILS_SOURCE = "NCBI E-utilities"


class NCBIError(Exception):
    """An application-level error NCBI returned inside an HTTP 200 body."""


def check_for_error(payload: Any) -> None:
    """Raise ``NCBIError`` if an E-utilities JSON body carries an error.

    Call this on every decoded JSON response before touching its contents.
    """
    if not isinstance(payload, dict):
        return

    # Shape 3: top-level "error", a sibling of "header". Seen for an empty UID
    # list, a malformed accession passed where a UID was expected, and an
    # esummary request above the 500-UID server cap.
    top_level = payload.get("error")
    if top_level:
        raise NCBIError(_stringify(top_level))

    # Shape 2: esummary reports errors as a LIST of strings under a key that
    # replaces the usual "result".
    summary_errors = payload.get("esummaryresult")
    if summary_errors:
        raise NCBIError(_stringify(summary_errors))

    # Shape 1: esearch nests an uppercase ERROR inside its result object.
    search_result = payload.get("esearchresult")
    if isinstance(search_result, dict):
        nested = search_result.get("ERROR")
        if nested:
            raise NCBIError(_stringify(nested))

    # Deliberately NOT treated as an error: esearchresult.errorlist. A search
    # that matched nothing populates errorlist.phrasesnotfound while returning
    # count "0" and HTTP 200. That is a legitimate empty result, not a failure,
    # and raising on it would turn "no data" into "the tool is broken".


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return str(value)


def provenance(
    urls: list[str],
    *,
    elapsed_ms: int,
    query_translation: str | None = None,
    result_count: int | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Build the provenance block.

    ``query_translation`` is load-bearing, not decoration. E-utilities silently
    rewrites search terms --- it repairs unbalanced brackets and drops field
    names it does not recognize, with no warning in either case. A search for
    ``foo[NOSUCHFIELD]`` runs as a free-text search for ``foo`` and returns
    confident, wrong results. Echoing the translation back is the only way the
    agent can see what was really searched.
    """
    block: dict[str, Any] = {"source": EUTILS_SOURCE, "urls": urls}
    if query_translation is not None:
        block["query_translation"] = query_translation
    if result_count is not None:
        block["result_count"] = result_count
    block["elapsed_ms"] = elapsed_ms
    if notes:
        block["notes"] = notes
    return block


def envelope(
    summary: str,
    data: Any,
    prov: dict[str, Any],
    *,
    truncated: bool = False,
    next_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a tool result.

    Key order matters: a model reading a long result may not reach the end, so
    the human-readable ``summary`` comes first and the bulky ``data`` sits
    between it and the provenance block.

    ``truncated`` is never silent. When a result is cut short the envelope says
    so and ``next`` carries the parameters to fetch the following page ---
    otherwise the agent reports a partial answer as if it were complete, which
    is the failure mode that looks most like success.
    """
    result: dict[str, Any] = {"summary": summary, "data": data}
    if truncated:
        result["truncated"] = True
        if next_params:
            result["next"] = next_params
    result["provenance"] = prov
    return result
