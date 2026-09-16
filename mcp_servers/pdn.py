"""Pathogen Data Network (PDN) MCP Server

A remote MCP server exposing tools over Pathogen Data Network resources:
    - LAPIS - pathogen sequences and metadata from Pathoplexus,
      GenSpectrum Loculus, and CoV-Spectrum open

Run over HTTP:  python mcp_servers/pdn.py
Run over stdio: python mcp_servers/pdn.py --stdio
"""

import difflib
import time
from typing import Any, Literal

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP(
    name="PDN MCP",
    dependencies=["mcp", "requests"],
    instructions=(
        "Query Pathogen Data Network (PDN) databases of pathogen sequences "
        "through LAPIS"
    ),
    port=8001,
    streamable_http_path="/mcp-pdn",
)


# LAPIS  (Pathoplexus, GenSpectrum Loculus, CoV-Spectrum open)
#
# LAPIS serves one URL per organism, and each organism declares its own
# metadata fields. The tools read those fields at runtime, so no field name
# is hard-coded here.

LAPIS_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "federated-data-ecosystem-assistant "
        "(+https://github.com/NIAID-BRC-Codeathons/federated-data-ecosystem-assistant)"
    ),
}

# An organism schema changes rarely, so refetch it after an hour.
LAPIS_SCHEMA_TTL_SECONDS = 3600

# Filter keys that LAPIS accepts on every organism, beside the field names.
LAPIS_QUERY_KEYS = {
    "advancedQuery", "nucleotideMutations", "aminoAcidMutations",
    "nucleotideInsertions", "aminoAcidInsertions",
}

# Field types that take a "From" or "To" range filter.
LAPIS_RANGE_TYPES = {"int", "float", "date"}

LAPIS_MAX_GROUPS = 500

# No LAPIS endpoint lists the organisms. These names come from each
# database's API documentation page, checked on 2026-09-16.
LAPIS_DATABASES = {
    "pathoplexus": {
        "name": "Pathoplexus",
        "description": "Human viral pathogens",
        "organism_url": "https://lapis.pathoplexus.org/{organism}",
        "organisms": [
            "andv", "cchf", "dengue", "ebola-bdbv", "ebola-sudan", "ebola-zaire",
            "hmpv", "marburg", "measles", "mpox", "rsv-a", "rsv-b", "west-nile",
            "yellow-fever", "zika",
        ],
    },
    "genspectrum": {
        "name": "GenSpectrum Loculus",
        "description": "Influenza lineages and dengue serotypes",
        "organism_url": "https://api.loculus.genspectrum.org/{organism}",
        "organisms": [
            "b-victoria", "denv1", "denv2", "denv3", "denv4", "h1n1pdm", "h3n2",
            "h5n1", "influenza-a", "influenza-b",
        ],
    },
    "cov-spectrum": {
        "name": "CoV-Spectrum open",
        "description": "Open SARS-CoV-2 data from Nextstrain",
        # A single dataset, so the URL takes no organism name.
        "organism_url": "https://lapis.cov-spectrum.org/open/v2",
        "organisms": ["sars-cov-2"],
    },
}

_lapis_schema_cache: dict[str, tuple[float, dict]] = {}


def _lapis_organism(organism: str) -> tuple[dict, str]:
    """Return the database and the LAPIS URL for an organism name."""
    for database in LAPIS_DATABASES.values():
        if organism in database["organisms"]:
            return database, database["organism_url"].format(organism=organism)
    known = [o for d in LAPIS_DATABASES.values() for o in d["organisms"]]
    close = difflib.get_close_matches(organism, known, n=3)
    hint = f" Close matches: {', '.join(close)}." if close else ""
    raise ToolError(
        f"Unknown organism '{organism}'.{hint} "
        "Call lapis_list_organisms for the full list."
    )


def _lapis_get(url: str) -> dict:
    resp = requests.get(url, headers=LAPIS_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _lapis_schema(organism: str) -> dict:
    """Fetch the fields, segments, and genes for an organism, with a cache."""
    cached = _lapis_schema_cache.get(organism)
    if cached and time.monotonic() - cached[0] < LAPIS_SCHEMA_TTL_SECONDS:
        return cached[1]
    _, url = _lapis_organism(organism)
    config = _lapis_get(f"{url}/sample/databaseConfig")["schema"]
    reference = _lapis_get(f"{url}/sample/referenceGenome")
    schema = {
        "instance_name": config["instanceName"],
        "primary_key": config["primaryKey"],
        "fields": {m["name"]: m["type"] for m in config["metadata"]},
        "phylo_tree_fields": [
            m["name"] for m in config["metadata"] if m.get("isPhyloTreeField")
        ],
        # The reference genome carries full sequences. Keep only the names.
        "segments": [s["name"] for s in reference["nucleotideSequences"]],
        "genes": [g["name"] for g in reference["genes"]],
    }
    _lapis_schema_cache[organism] = (time.monotonic(), schema)
    return schema


def _lapis_post(url: str, body: dict) -> requests.Response:
    """POST a LAPIS query, and turn a LAPIS error into a short tool error."""
    resp = requests.post(url, json=body, headers=LAPIS_HEADERS, timeout=60)
    if resp.status_code >= 400:
        try:
            detail = resp.json()["error"]["detail"]
        except (ValueError, KeyError, TypeError):
            detail = resp.text
        raise ToolError(f"LAPIS returned HTTP {resp.status_code}: {detail[:500]}")
    return resp


def _lapis_unknown_field(schema: dict, name: str) -> ToolError:
    names = list(schema["fields"])
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    close += [n for n in names if name.lower() in n.lower() and n not in close]
    hint = f" Close matches: {', '.join(close[:5])}." if close else ""
    return ToolError(
        f"'{name}' is not a field of {schema['instance_name']}.{hint} "
        "Call lapis_describe_organism with field_search to find field names."
    )


def _lapis_check_filters(schema: dict, filters: dict) -> None:
    """Reject a filter key that the organism does not declare.

    LAPIS answers an unknown key with a list of every valid key, often more
    than 400. A short error with close matches serves the agent better.
    """
    fields = schema["fields"]
    for key in filters:
        if key in fields or key in LAPIS_QUERY_KEYS:
            continue
        for suffix in (".regex", ".isNull", "From", "To"):
            field = key.removesuffix(suffix)
            if field != key and field in fields:
                break
        else:
            raise _lapis_unknown_field(schema, key)
        field_type = fields[field]
        if suffix in ("From", "To") and field_type not in LAPIS_RANGE_TYPES:
            date_fields = [f for f, t in fields.items() if t == "date"]
            raise ToolError(
                f"'{key}' does not exist. '{field}' has type {field_type}, and "
                "only int, float, and date fields take From or To. "
                f"Date fields: {', '.join(date_fields)}."
            )
        if suffix == ".regex" and field_type != "string":
            raise ToolError(
                f"'{key}' does not exist. '{field}' has type {field_type}, and "
                "only string fields take .regex."
            )


def _lapis_version_filter(schema: dict, filters: dict, latest_version_only: bool) -> str | None:
    """Add the latest-version filters to filters in place. Return a note on them.

    Loculus keeps every version of a sequence, and a revocation stays as its
    own record. Without these filters, one sequence can count several times.
    """
    fields = schema["fields"]
    if "versionStatus" not in fields:
        return None
    if latest_version_only:
        filters.setdefault("versionStatus", "LATEST_VERSION")
        if "isRevocation" in fields:
            filters.setdefault("isRevocation", False)
    if filters.get("versionStatus") == "LATEST_VERSION" and filters.get("isRevocation") is False:
        return (
            "Counts only the latest version of each sequence and skips "
            "revocations, so each sequence counts once."
        )
    return (
        "Includes older versions or revocations, so one sequence can count "
        "more than once."
    )


@mcp.tool()
def lapis_list_organisms() -> dict:
    """List the pathogens that the LAPIS tools can query, grouped by database.

    Every other LAPIS tool takes one of these organism names. Pathoplexus
    holds human viral pathogens. GenSpectrum Loculus holds influenza lineages
    and dengue serotypes. CoV-Spectrum open holds open SARS-CoV-2 data.

    Returns:
        Each database with its description, organism names, and LAPIS URL
        pattern.

    Example questions:
        "Which pathogens does the Pathogen Data Network have sequences for?"
        "Can I query H5N1 influenza sequences?"
    """
    return {
        "databases": [
            {
                "name": d["name"],
                "description": d["description"],
                "organisms": d["organisms"],
                "url_pattern": d["organism_url"],
            }
            for d in LAPIS_DATABASES.values()
        ],
        "next_step": (
            "Call lapis_describe_organism before you filter. Each organism "
            "declares its own fields."
        ),
    }


@mcp.tool()
def lapis_describe_organism(organism: str, field_search: str = "") -> dict:
    """Describe the metadata fields, segments, and genes for one organism.

    Call this before you filter or group. Each organism declares its own
    fields, and a field name that works for one organism can fail for another.
    For example, Pathoplexus stores the collection date in
    sampleCollectionDate, but CoV-Spectrum stores it in date.

    Without field_search, the response lists every field of the organism,
    often more than 100. Pass field_search to list only the fields you need.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox",
            "h5n1", "sars-cov-2".
        field_search: Case-insensitive text that a field name must contain,
            e.g. "country", "date", "host". Leave empty to list every field.
            A search also leaves out the segments and genes.

    Returns:
        The matching fields with their types, the phylogenetic tree fields,
        and notes on versions and data use terms. Without field_search, the
        segments and genes too.

    Example questions:
        "Which metadata fields can I filter mpox sequences on?"
        "Which date fields does the H5N1 database have?"
        "Which genes can I query for measles?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    needle = field_search.lower()
    fields = [
        {"name": name, "type": field_type}
        for name, field_type in schema["fields"].items()
        if needle in name.lower()
    ]
    notes = []
    if "versionStatus" in schema["fields"]:
        notes.append(
            "The database keeps every revision of a sequence, and a revocation "
            "stays as its own record. Filter on versionStatus='LATEST_VERSION' "
            "and isRevocation=false to count each sequence once."
        )
    if "dataUseTerms" in schema["fields"]:
        notes.append(
            "Some records carry RESTRICTED data use terms. Read dataUseTerms, "
            "dataUseTermsRestrictedUntil, and dataUseTermsUrl before you reuse "
            "a record."
        )
    description = {
        "organism": organism,
        "database": database["name"],
        "lapis_url": url,
        "instance_name": schema["instance_name"],
        "primary_key": schema["primary_key"],
    }
    # A field search is a field lookup, so it skips the segments and genes.
    if not field_search:
        description["segments"] = schema["segments"]
        description["genes"] = schema["genes"]
    description.update({
        "phylo_tree_fields": schema["phylo_tree_fields"],
        "field_search": field_search,
        "fields_matched": len(fields),
        "fields_total": len(schema["fields"]),
        "fields": fields,
        "notes": notes,
    })
    return description


@mcp.tool()
def lapis_aggregate_samples(
    organism: str,
    group_by: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    latest_version_only: bool = True,
    order_by: Literal["count", "group"] = "count",
    max_groups: int = 50,
) -> dict:
    """Count sequenced samples for one organism, optionally grouped by fields.

    Use this for how many sequences exist, and how they split across
    countries, dates, lineages, or hosts. A count measures sequencing effort,
    not infection incidence. Never report it as a case count.

    By default, each sequence counts once: the tool keeps only the latest
    version of each sequence and skips revocations. Field names differ between
    databases, so call lapis_describe_organism first to find them.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox".
        group_by: Field names to group by, e.g. ["geoLocCountry"]. Leave empty
            for one total count.
        filters: Field filters, e.g. {"geoLocCountry": "Brazil"}. A list value
            matches any of its items. Add "From" or "To" to an int, float, or
            date field for an inclusive range, e.g.
            {"sampleCollectionDateRangeLowerFrom": "2025-01-01"}. A string
            field takes no range, even when it holds a date.
        latest_version_only: Count only the latest version of each sequence
            and skip revocations (default True). False counts every version.
        order_by: "count" sorts groups from largest to smallest. "group" sorts
            by the group_by fields, which suits a count over dates.
        max_groups: Maximum number of groups to return (default 50, max 500).

    Returns:
        Rows of counts with their group values, whether max_groups cut the
        rows off, notes on how to read the counts, and the exact query sent to
        LAPIS.

    Example questions:
        "How many mpox sequences come from each country?"
        "How many dengue genomes were collected since the start of 2025?"
        "Which SARS-CoV-2 lineages were sequenced most in Switzerland?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    group_by = group_by or []
    for name in group_by:
        if name not in schema["fields"]:
            raise _lapis_unknown_field(schema, name)
    body = dict(filters or {})
    _lapis_check_filters(schema, body)
    version_note = _lapis_version_filter(schema, body, latest_version_only)

    query = {**body, "fields": group_by}
    max_groups = max(1, min(max_groups, LAPIS_MAX_GROUPS))
    if group_by:
        # LAPIS rejects a limit on grouped counts unless an order is set.
        if order_by == "count":
            query["orderBy"] = [{"field": "count", "type": "descending"}]
        else:
            query["orderBy"] = [{"field": f, "type": "ascending"} for f in group_by]
        query["limit"] = max_groups

    resp = _lapis_post(f"{url}/sample/aggregated", query)
    rows = resp.json()["data"]
    notes = [
        "A count measures sequencing effort, not infection incidence. A "
        "difference between places or dates often reflects how much each one "
        "sequences."
    ]
    if version_note:
        notes.append(version_note)
    return {
        "organism": organism,
        "database": database["name"],
        "group_by": group_by,
        "rows_returned": len(rows),
        "truncated": bool(group_by) and len(rows) == max_groups,
        "rows": rows,
        "notes": notes,
        "query": {
            "url": f"{url}/sample/aggregated",
            "method": "POST",
            "body": query,
            "data_version": resp.headers.get("lapis-data-version"),
        },
    }


if __name__ == "__main__":
    import sys
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print("PDN MCP Server starting on http://localhost:8001/mcp-pdn ...")
        mcp.run(transport="streamable-http")
