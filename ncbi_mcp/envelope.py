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

from collections.abc import Sequence
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
    calls: Sequence[Any],
    *,
    elapsed_ms: int,
    tool: str | None = None,
    query_translation: str | None = None,
    result_count: int | None = None,
    records_by_database: dict[str, int] | None = None,
    coverage: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Build the provenance block.

    ``query_translation`` is load-bearing, not decoration. E-utilities silently
    rewrites search terms --- it repairs unbalanced brackets and drops field
    names it does not recognize, with no warning in either case. A search for
    ``foo[NOSUCHFIELD]`` runs as a free-text search for ``foo`` and returns
    confident, wrong results. Echoing the translation back is the only way the
    agent can see what was really searched.

    ``calls`` is a sequence of ``eutils.Call``. Plain URL strings are also
    accepted so a caller that has only URLs still gets a valid block, just
    without the per-database breakdown.
    """
    calls = list(calls)
    block: dict[str, Any] = {"source": EUTILS_SOURCE}
    if tool is not None:
        block["tool"] = tool

    sources = _sources(calls, records_by_database)
    if sources:
        block["sources"] = sources
    utilities = _tally(getattr(c, "utility", None) for c in calls)
    if utilities:
        block["utilities_called"] = utilities
    request_cost = _request_cost(calls)
    if request_cost:
        block["request_cost"] = request_cost
    if coverage:
        block["coverage"] = coverage

    block["urls"] = [getattr(c, "url", c) for c in calls]
    if query_translation is not None:
        block["query_translation"] = query_translation
    if result_count is not None:
        block["result_count"] = result_count
    block["elapsed_ms"] = elapsed_ms
    if notes:
        block["notes"] = notes
    return block


def _sources(
    calls: list[Any], records_by_database: dict[str, int] | None
) -> list[dict[str, Any]]:
    """Per-database attribution, built only from calls that produced data.

    Coverage lookups are excluded here even though they are reported under
    ``request_cost``: they describe the result, they do not contribute to it,
    and counting them would credit a database for data it did not supply.
    """
    primary = [
        c
        for c in calls
        if getattr(c, "purpose", "primary") == "primary" and getattr(c, "db", None)
    ]
    if not primary:
        return []

    order: list[str] = []
    per_db: dict[str, dict[str, Any]] = {}
    for call in primary:
        entry = per_db.get(call.db)
        if entry is None:
            order.append(call.db)
            entry = per_db[call.db] = {
                "database": call.db,
                "utilities": [],
                "calls": 0,
            }
        entry["calls"] += 1
        if call.utility not in entry["utilities"]:
            entry["utilities"].append(call.utility)

    # Record attribution. When the caller did not say how the records split,
    # claim it only in the one case where it is not a guess: a single database
    # supplied everything. Inventing a split across several would be a
    # confident number with nothing behind it.
    counts = dict(records_by_database or {})
    if not counts and len(order) == 1:
        counts = {}
    total = sum(counts.values())
    for db in order:
        if db in counts:
            per_db[db]["records"] = counts[db]
            if total:
                per_db[db]["percent_of_result"] = round(counts[db] / total * 100, 1)
    if not counts and len(order) == 1:
        per_db[order[0]]["percent_of_result"] = 100.0

    return [per_db[db] for db in order]


def _request_cost(calls: list[Any]) -> dict[str, int]:
    """How many requests this one result spent, split by what they were for.

    The brief scores execution cost, and coverage lookups are extra requests
    against a per-IP budget the whole venue shares. Reporting only the calls
    that fetched data would understate what the answer cost.
    """
    if not calls:
        return {}
    cost = {"total": len(calls)}
    by_purpose = _tally(getattr(c, "purpose", "primary") for c in calls)
    cost.update(by_purpose)
    return cost


def _tally(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


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
