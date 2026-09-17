"""How much of the database a result actually represents.

A bare count is unreadable without its denominator. Measured on 2026-09-16:
``Staphylococcus aureus[ORGN] AND MRSA`` returns 16,360 BioSamples, which
sounds like a lot until you know there are 230,466 S. aureus BioSamples --- it
is 7%, and the missing 93% are not absent, they are differently worded. An
agent shown only the numerator reports the 16,360 as if it were the population.

So every search result carries ``matched``, a ``denominator``, and the ``basis``
naming exactly what the denominator counts. The basis string is not decoration:
"12.6% of S. aureus BioSamples" and "12.6% of all BioSamples" are different
claims, and only the basis distinguishes them.

Two flavors, because two different questions are worth answering:

* **search** --- matched / the same query with its filters removed. What
  fraction of the candidate pool the query selected.
* **fetch** --- returned / requested. What fraction of the UIDs asked for came
  back. This one earns its keep: NCBI drops unresolvable UIDs from esummary
  silently, so 48-of-50 looks identical to 50-of-50 unless someone counts.

## Cost

The search flavor spends one extra request per call, against a 3/sec limit
shared by everyone behind the same IP. That is a real cost and the reason for
the off switch: ``NCBI_COVERAGE=0``. The fetch flavor is free --- it compares
two numbers already in hand. Database totals come from einfo and are cached for
the life of the process, so they cost one request per database ever.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Organism clauses, the only filter worth stripping automatically. An organism
# is the population a biologist means by "of these, how many"; strategy,
# platform and date are the selection being measured, so removing them too
# would make every query 100% of itself.
#
# Matches a quoted or bare organism name followed by [ORGN] or [Organism],
# which are the two spellings E-utilities accepts.
#
# The case-insensitivity is scoped to the field tag with (?i:...) rather than
# applied to the whole pattern, and that is load-bearing. A global re.IGNORECASE
# also loosens the `[a-z]` that matches a species epithet, which lets the
# capitalized boolean in "Homo sapiens[ORGN] OR Homo sapiens[ORGN]" be absorbed
# into the organism name --- yielding the basis "... OR OR Homo sapiens[ORGN]"
# and a denominator for a query that matches nothing. Caught by
# test_repeated_organisms_are_not_double_counted.
_ORGANISM_CLAUSE = re.compile(
    r"""("[^"]+"|'[^']+'|[A-Za-z][\w.\-]*(?:\s+[a-z][\w.\-]*)*)\s*\[(?i:ORGN|Organism)\]""",
    re.VERBOSE,
)

# einfo db -> total record count. One request per database per process.
_DB_TOTALS: dict[str, int] = {}


def enabled() -> bool:
    """Coverage is on unless switched off. See the cost note in the module docstring."""
    return os.environ.get("NCBI_COVERAGE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def organism_basis(term: str) -> str | None:
    """Reduce a search term to just its organism clauses, or None if it has none.

    ``S. aureus[ORGN] AND (MRSA OR mecA)`` becomes ``S. aureus[ORGN]``: the
    population, without the selection being measured.
    """
    names = _ORGANISM_CLAUSE.findall(term or "")
    if not names:
        return None
    seen: list[str] = []
    for name in names:
        clause = f"{name.strip()}[ORGN]"
        if clause not in seen:
            seen.append(clause)
    return " OR ".join(seen)


async def for_search(client, db: str, term: str, matched: int) -> dict[str, Any] | None:
    """Coverage for a search: what share of the candidate pool matched.

    Returns None rather than raising. Coverage is commentary on a result that
    already succeeded --- a failure to compute it must never turn a good answer
    into an error, so every failure path here degrades to "no coverage block".
    """
    if not enabled():
        return None

    basis_term = organism_basis(term)
    try:
        if basis_term:
            denominator = await _count(client, db, basis_term)
            basis = f"{basis_term} in {db}"
        else:
            denominator = await database_total(client, db)
            basis = f"all records in {db}"
    except Exception:  # noqa: BLE001 -- see the docstring: never break a good result
        return None

    return _block(matched, denominator, basis, db)


def for_fetch(db: str, requested: int, returned: int) -> dict[str, Any] | None:
    """Coverage for a fetch: how many of the requested records came back.

    Free, and the only signal that NCBI quietly dropped UIDs it could not
    resolve --- esummary omits them from ``result`` without comment.
    """
    if not enabled() or requested <= 0:
        return None
    block = _block(returned, requested, f"{requested} record(s) requested", db)
    if returned < requested:
        block["shortfall"] = requested - returned
    return block


def _block(matched: int, denominator: int, basis: str, db: str) -> dict[str, Any]:
    percent = round(matched / denominator * 100, 2) if denominator else None
    return {
        "database": db,
        "matched": matched,
        "denominator": denominator,
        "percent": percent,
        "basis": basis,
    }


async def _count(client, db: str, term: str) -> int:
    """esearch with retmax=0 --- the count without paying for any records."""
    payload = await client.request_json(
        "esearch", {"term": term, "retmax": 0}, db=db, purpose="coverage"
    )
    return int(payload.get("esearchresult", {}).get("count", 0))


async def database_total(client, db: str) -> int:
    """Total records in a database, from einfo. Cached for the process.

    ``einforesult.dbinfo`` is a LIST even for a single database --- indexing it
    as a dict is the obvious mistake and raises TypeError.
    """
    if db in _DB_TOTALS:
        return _DB_TOTALS[db]
    payload = await client.request_json("einfo", {}, db=db, purpose="coverage")
    dbinfo = payload.get("einforesult", {}).get("dbinfo", [])
    if isinstance(dbinfo, list):
        dbinfo = dbinfo[0] if dbinfo else {}
    total = int(dbinfo.get("count", 0))
    _DB_TOTALS[db] = total
    return total
