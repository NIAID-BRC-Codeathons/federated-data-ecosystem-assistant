#!/usr/bin/env python3
"""End-to-end smoke test: call every tool against the live NDE API.

Run with:  .venv/bin/python scripts/smoke_test.py [-v]

Prints a PASS/FAIL line per tool. With -v, also dumps each response so you can
eyeball the shaping. Exits non-zero if any tool errors or returns nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys

from nde_mcp import server as S

VERBOSE = "-v" in sys.argv

CASES: list[tuple[str, object, dict]] = [
    (
        "nde_search_datasets (tuberculosis RNA-seq, recent)",
        S.nde_search_datasets,
        {
            "query": "tuberculosis",
            "measurement_technique": "RNA-seq",
            "date_from": "2024-01-01",
            "size": 3,
        },
    ),
    (
        "nde_search_datasets (pathogen filter + repository)",
        S.nde_search_datasets,
        {"pathogen": "Influenza A virus", "repository": "NCBI SRA", "size": 3},
    ),
    (
        "nde_search_datasets (sort=newest, any type)",
        S.nde_search_datasets,
        {"query": "malaria", "record_type": "any", "sort": "newest", "size": 3},
    ),
    (
        "nde_semantic_search",
        S.nde_semantic_search,
        {"query": "immune response profiling in infants after vaccination", "size": 3},
    ),
    (
        "nde_search_tools (python phylogenetics)",
        S.nde_search_tools,
        {"query": "phylogenetic", "programming_language": "Python", "size": 3},
    ),
    (
        "nde_get_record",
        S.nde_get_record,
        {"record_id": "ncbi_sra_srp425935"},
    ),
    (
        "nde_get_record (missing id -> graceful)",
        S.nde_get_record,
        {"record_id": "definitely_not_a_real_id_12345"},
    ),
    (
        "nde_lookup_ids",
        S.nde_lookup_ids,
        {"ids": "ncbi_sra_srp425935, biotools_align, bogus_id_xyz"},
    ),
    (
        "nde_facet_counts (assays in TB data)",
        S.nde_facet_counts,
        {
            "fields": "measurementTechnique.name, includedInDataCatalog.name",
            "query": "tuberculosis",
            "top_n": 5,
        },
    ),
    (
        "nde_list_repositories (filtered)",
        S.nde_list_repositories,
        {"name_contains": "NCBI"},
    ),
    (
        "nde_list_fields (prefix)",
        S.nde_list_fields,
        {"prefix": "infectiousAgent", "limit": 20},
    ),
    (
        "nde_raw_query (negation + exists)",
        S.nde_raw_query,
        {"q": '_exists_:nctid AND @type:Dataset AND NOT includedInDataCatalog.name:"NCBI GEO"', "size": 3},
    ),
    (
        "nde_raw_query (bad lucene -> graceful error)",
        S.nde_raw_query,
        {"q": "[unclosed", "size": 1},
    ),
    (
        "nde_search_datasets (no matches -> hint)",
        S.nde_search_datasets,
        {"query": "zzzzqqqq_nonexistent_term_xyzzy", "size": 3},
    ),
    (
        "paging clamp (offset near window edge)",
        S.nde_raw_query,
        {"q": "malaria", "size": 20, "offset": 9990},
    ),
]

# Cases where an `error` key in the response is the correct outcome.
EXPECT_ERROR = {
    "nde_get_record (missing id -> graceful)",
    "nde_raw_query (bad lucene -> graceful error)",
}


async def run_case(label: str, fn, kwargs: dict) -> bool:
    try:
        raw = await fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - smoke test reports everything
        print(f"FAIL  {label}\n        raised {type(exc).__name__}: {exc}")
        return False

    try:
        data = json.loads(raw)
    except ValueError:
        print(f"FAIL  {label}\n        response was not valid JSON")
        return False

    has_error = isinstance(data, dict) and "error" in data
    expects_error = label in EXPECT_ERROR

    if has_error and not expects_error:
        print(f"FAIL  {label}\n        {data['error']}")
        return False
    if expects_error and not has_error:
        print(f"FAIL  {label}\n        expected a graceful error, got a normal response")
        return False

    print(f"PASS  {label}{_summary(data)}")
    if VERBOSE:
        print(json.dumps(data, indent=2)[:3000])
        print("-" * 70)
    return True


def _summary(data: dict) -> str:
    """One-line description of what came back, for the PASS line."""
    if "error" in data:
        return f"  (expected error: {str(data['error'])[:60]})"
    bits = []
    if "total" in data:
        bits.append(f"total={data['total']}")
    if "returned" in data:
        bits.append(f"returned={data['returned']}")
    if "matching_records" in data:
        bits.append(f"matching={data['matching_records']}")
    if "repository_count" in data:
        bits.append(f"repos={data['repository_count']}")
    if "total_fields" in data:
        bits.append(f"fields={data['total_fields']}")
    if "matched" in data:
        bits.append(f"matched={data['matched']}/{data.get('requested')}")
    if "record" in data:
        name = (data.get("summary") or {}).get("name") or ""
        bits.append(f"record={name[:50]!r}")
    if "next_offset" in data:
        bits.append(f"next={data['next_offset']}")
    if "paging_note" in data:
        bits.append("paging_note")
    if "hint" in data:
        bits.append("hint")
    return "  (" + ", ".join(bits) + ")" if bits else ""


async def main() -> int:
    print(f"Testing {len(CASES)} cases against {S.get_client().base_url}\n")
    results = [await run_case(*case) for case in CASES]
    await S.get_client().aclose()

    passed, total = sum(results), len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
