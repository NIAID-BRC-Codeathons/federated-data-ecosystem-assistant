"""Pathogen Data Network (PDN) MCP Server

A remote MCP server exposing tools over Pathogen Data Network resources:
    - LAPIS - pathogen sequences and metadata from Pathoplexus,
      GenSpectrum Loculus, and CoV-Spectrum open

Run over HTTP:  python mcp_servers/pdn.py
Run over stdio: python mcp_servers/pdn.py --stdio
"""

import difflib
import json
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


# ********** shared by the LAPIS tools **********

LAPIS_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "federated-data-ecosystem-assistant "
        "(+https://github.com/NIAID-BRC-Codeathons/federated-data-ecosystem-assistant)"
    ),
}

# An organism schema changes rarely, so refetch it after an hour.
LAPIS_SCHEMA_TTL_SECONDS = 3600

# No LAPIS endpoint lists the organisms. These names come from each
# database's API documentation page, checked on 2026-09-16.
LAPIS_DATABASES = {
    "pathoplexus": {
        "name": "Pathoplexus",
        "description": "Human viral pathogens",
        "organism_url": "https://lapis.pathoplexus.org/{organism}",
        "record_url": "https://pathoplexus.org/seq/{accession}",
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
        "record_url": "https://loculus.genspectrum.org/seq/{accession}",
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
        "record_url": None,
        "organisms": ["sars-cov-2"],
    },
}

_lapis_schema_cache: dict[str, tuple[float, dict]] = {}

# Filter keys that LAPIS accepts on every organism, beside the field names.
LAPIS_QUERY_KEYS = {
    "advancedQuery", "nucleotideMutations", "aminoAcidMutations",
    "nucleotideInsertions", "aminoAcidInsertions",
}

# Field types that take a "From" or "To" range filter.
LAPIS_RANGE_TYPES = {"int", "float", "date"}


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


class LapisResponseTooLarge(Exception):
    """A LAPIS response passed the size limit that the caller set."""


def _lapis_post(
    url: str, body: dict, max_bytes: int | None = None
) -> tuple[list[dict], str | None]:
    """POST a LAPIS query. Return the data rows and the LAPIS data version.

    A LAPIS error becomes a short tool error. With max_bytes, the read stops
    as soon as the response passes that size.
    """
    with requests.post(
        url, json=body, headers=LAPIS_HEADERS, timeout=60, stream=True
    ) as resp:
        if resp.status_code >= 400:
            try:
                detail = resp.json()["error"]["detail"]
            except (ValueError, KeyError, TypeError):
                detail = resp.text
            raise ToolError(f"LAPIS returned HTTP {resp.status_code}: {detail[:500]}")
        content = bytearray()
        for chunk in resp.iter_content(chunk_size=1 << 16):
            content += chunk
            if max_bytes is not None and len(content) > max_bytes:
                raise LapisResponseTooLarge(max_bytes)
        return json.loads(content)["data"], resp.headers.get("lapis-data-version")


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


# ********** list organisms **********

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


# ********** describe organism **********

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


# ********** aggregate samples **********

LAPIS_MAX_GROUPS = 500


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

    rows, data_version = _lapis_post(f"{url}/sample/aggregated", query)
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
            "data_version": data_version,
        },
    }


# ********** get mutations **********

LAPIS_MAX_MUTATIONS = 500

# The mutation name already encodes mutationFrom, mutationTo, and position,
# so a mutation query asks LAPIS for these columns only.
LAPIS_MUTATION_FIELDS = [
    "mutation", "sequenceName", "position", "count", "coverage", "proportion",
]

# LAPIS cannot filter mutations by gene or segment, so a gene or segment
# filter downloads every mutation above the threshold. At threshold 0 that
# reached 13 MB for dengue nucleotides, so the read stops past this size.
LAPIS_MAX_FILTER_DOWNLOAD_BYTES = 5_000_000


@mcp.tool()
def lapis_get_mutations(
    organism: str,
    sequence_type: Literal["nucleotide", "amino_acid"],
    gene_or_segment: str = "",
    filters: dict[str, Any] | None = None,
    min_proportion: float = 0.05,
    latest_version_only: bool = True,
    order_by: Literal["proportion", "position"] = "proportion",
    max_mutations: int = 50,
) -> dict:
    """List the mutations found in the sequences of one organism.

    Use this for which substitutions and deletions occur, and how common each
    one is among the sequences that match the filters. Each row gives the
    mutation, the number of sequences that carry it, the number of sequences
    with coverage at its position, and the proportion between the two.

    A mutation reads <gene or segment>:<reference><position><new>, e.g.
    S:N501Y, or C241T for a nucleotide on a single-segment genome. A "-" as
    the new symbol marks a deletion.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "sars-cov-2".
        sequence_type: "amino_acid" for protein changes within genes, or
            "nucleotide" for changes in the genome.
        gene_or_segment: Only return mutations in this gene (amino_acid) or
            segment (nucleotide), e.g. "S" or "seg4". lapis_describe_organism
            lists the genes and segments. Leave empty for all of them.
        filters: Field filters, in the same form as lapis_aggregate_samples.
        min_proportion: Leave out mutations below this proportion (default
            0.05, the LAPIS default). This is a reporting filter, not a
            biological cutoff. Lower it, down to 0, to look for rare or
            emerging mutations. With gene_or_segment set, a very low value can
            make the query too large, and the tool then returns an error.
        latest_version_only: Use only the latest version of each sequence and
            skip revocations (default True).
        order_by: "proportion" lists the most common mutations first.
            "position" lists them in genome order within each gene or segment.
        max_mutations: Maximum number of mutations to return (default 50,
            max 500).

    Returns:
        Rows of mutations with count, coverage, and proportion, whether
        max_mutations cut the rows off, notes on how to read a proportion, and
        the exact query sent to LAPIS.

    Example questions:
        "Which spike mutations occur in over 5% of SARS-CoV-2 sequences from
        Switzerland since 2025?"
        "Which HA amino acid changes are common in H5N1 from cattle?"
        "Which nucleotide mutations are most common in mpox sequences?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    if sequence_type == "amino_acid":
        endpoint = "/sample/aminoAcidMutations"
        names, kind = schema["genes"], "gene"
    else:
        endpoint = "/sample/nucleotideMutations"
        names, kind = schema["segments"], "segment"
    # A single-segment genome leaves sequenceName empty, so there is nothing
    # to filter on.
    name_filter = gene_or_segment if len(names) > 1 or kind == "gene" else ""
    if kind == "gene" and not names:
        raise ToolError(
            f"{schema['instance_name']} declares no genes, so it has no amino "
            "acid mutations. Use sequence_type 'nucleotide' instead."
        )
    if gene_or_segment and gene_or_segment not in names:
        close = difflib.get_close_matches(gene_or_segment, names, n=3, cutoff=0.5)
        hint = f" Close matches: {', '.join(close)}." if close else ""
        raise ToolError(
            f"'{gene_or_segment}' is not a {kind} of {schema['instance_name']}.{hint} "
            f"Its {kind}s: {', '.join(names)}."
        )

    body = dict(filters or {})
    _lapis_check_filters(schema, body)
    version_note = _lapis_version_filter(schema, body, latest_version_only)
    min_proportion = max(0.0, min(min_proportion, 1.0))
    max_mutations = max(1, min(max_mutations, LAPIS_MAX_MUTATIONS))
    query = {**body, "minProportion": min_proportion, "fields": LAPIS_MUTATION_FIELDS}
    if order_by == "proportion":
        order = [{"field": "proportion", "type": "descending"}]
    else:
        order = [
            {"field": "sequenceName", "type": "ascending"},
            {"field": "position", "type": "ascending"},
        ]
    # LAPIS cannot filter mutations by gene or segment. With a name filter,
    # fetch every mutation above the threshold and filter here, up to a size
    # limit. Without one, let LAPIS sort and cut, because a full nucleotide
    # list can pass 20 MB.
    if not name_filter:
        query.update({"orderBy": order, "limit": max_mutations})

    try:
        rows, data_version = _lapis_post(
            f"{url}{endpoint}",
            query,
            max_bytes=LAPIS_MAX_FILTER_DOWNLOAD_BYTES if name_filter else None,
        )
    except LapisResponseTooLarge:
        raise ToolError(
            f"The {sequence_type.replace('_', ' ')} mutations at or above "
            f"proportion {min_proportion} pass "
            f"{LAPIS_MAX_FILTER_DOWNLOAD_BYTES // 1_000_000} MB, which is too "
            f"much to filter down to {kind} {gene_or_segment}. Raise "
            "min_proportion, e.g. to 0.001, or narrow the filters."
        ) from None
    if name_filter:
        rows = [r for r in rows if r["sequenceName"] == name_filter]
        if order_by == "proportion":
            rows.sort(key=lambda r: -r["proportion"])
        else:
            rows.sort(key=lambda r: r["position"])
    truncated = len(rows) > max_mutations if name_filter else len(rows) == max_mutations
    rows = rows[:max_mutations]
    for row in rows:
        row["proportion"] = round(row["proportion"], 4)

    notes = [
        "A proportion is the count divided by the coverage, among the "
        "sequences that match the filters. It shows how common a mutation is "
        "in sequenced samples, not among infections.",
        "A proportion from a small coverage is unreliable. Check the coverage "
        "before you compare two mutations.",
        "A mutation seen in only one or two sequences can be a sequencing or "
        "assembly artifact. Check the count before you treat it as real.",
    ]
    if version_note:
        notes.append(version_note)
    return {
        "organism": organism,
        "database": database["name"],
        "sequence_type": sequence_type,
        "gene_or_segment": gene_or_segment,
        "min_proportion": min_proportion,
        "rows_returned": len(rows),
        "truncated": truncated,
        "rows": rows,
        "notes": notes,
        "query": {
            "url": f"{url}{endpoint}",
            "method": "POST",
            "body": query,
            "data_version": data_version,
        },
    }


# ********** get sample details **********

LAPIS_MAX_RECORDS = 100

# Pathoplexus records carry these fields. A redistributor must keep them with
# the data, so every record query adds them wherever the schema has them.
LAPIS_TERMS_FIELDS = ["dataUseTerms", "dataUseTermsRestrictedUntil", "dataUseTermsUrl"]

LAPIS_RESTRICTED_TERMS = (
    "The submitters restrict these records until the dates given. Keep the "
    "data use terms with the records. Link each record to its page when you "
    "present it. Read the terms before you publish results that use them."
)


def _lapis_restricted_terms(database: dict, schema: dict, rows: list[dict]) -> dict | None:
    """Collect the data use terms of the restricted rows. None when no row is restricted."""
    restricted = [r for r in rows if r.get("dataUseTerms") == "RESTRICTED"]
    if not restricted:
        return None
    key = schema["primary_key"]
    return {
        "restricted_records": [
            {
                "accession": r[key],
                "restricted_until": r.get("dataUseTermsRestrictedUntil"),
                "record_url": database["record_url"].format(accession=r[key]),
            }
            for r in restricted
        ],
        "terms_url": restricted[0].get("dataUseTermsUrl"),
        "obligations": LAPIS_RESTRICTED_TERMS,
    }


@mcp.tool()
def lapis_get_sample_details(
    organism: str,
    fields: list[str],
    filters: dict[str, Any] | None = None,
    latest_version_only: bool = True,
    order_by: str = "",
    descending: bool = False,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """Get individual sample records for one organism, with the fields you name.

    Use this to list or inspect specific samples, such as the most recent
    sequences from one country. For counts, use lapis_aggregate_samples, which
    returns far less text.

    Name only the fields you need. A record can carry over 150 fields, and a
    field such as authors can run to thousands of characters.
    lapis_describe_organism lists the field names. The tool always adds the
    primary key, and on Pathoplexus it adds the data use terms fields.

    Records under RESTRICTED data use terms come with a data_use_terms block.
    Keep those terms with the records, and link each record to its page when
    you present it.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox".
        fields: Field names to return, e.g. ["geoLocCountry",
            "sampleCollectionDate"].
        filters: Field filters, in the same form as lapis_aggregate_samples.
        latest_version_only: Return only the latest version of each sequence
            and skip revocations (default True).
        order_by: A field to sort the records by, e.g. "sampleCollectionDate".
            Leave empty for the database order.
        descending: Sort from highest to lowest (default False).
        limit: Maximum number of records to return (default 10, max 100).
        offset: Number of matching records to skip, to get the next page
            (default 0).

    Returns:
        The records, the total number of matching records, the offset of the
        next page, the data use terms of restricted records, notes, and the
        exact query sent to LAPIS.

    Example questions:
        "Show the 10 most recent mpox sequences from the Democratic Republic
        of the Congo."
        "List the H5N1 samples from cattle with their collection dates."
        "Which measles records on Pathoplexus are under restricted terms?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    if not fields:
        raise ToolError(
            "Name at least one field. lapis_describe_organism lists the fields."
        )
    for name in [*fields, *([order_by] if order_by else [])]:
        if name not in schema["fields"]:
            raise _lapis_unknown_field(schema, name)
    body = dict(filters or {})
    _lapis_check_filters(schema, body)
    version_note = _lapis_version_filter(schema, body, latest_version_only)

    terms_fields = [f for f in LAPIS_TERMS_FIELDS if f in schema["fields"]]
    returned_fields = list(dict.fromkeys([schema["primary_key"], *fields, *terms_fields]))
    limit = max(1, min(limit, LAPIS_MAX_RECORDS))
    offset = max(0, offset)
    query = {**body, "fields": returned_fields, "limit": limit, "offset": offset}
    if order_by:
        query["orderBy"] = [
            {"field": order_by, "type": "descending" if descending else "ascending"}
        ]

    rows, data_version = _lapis_post(f"{url}/sample/details", query)
    # A count over the same filters tells the agent how many records remain.
    count_rows, _ = _lapis_post(f"{url}/sample/aggregated", body)
    total = count_rows[0]["count"]
    next_offset = offset + len(rows)

    notes = []
    if version_note:
        notes.append(version_note)
    result = {
        "organism": organism,
        "database": database["name"],
        "total": total,
        "offset": offset,
        "rows_returned": len(rows),
        "next_offset": next_offset if next_offset < total else None,
        "rows": rows,
    }
    terms = _lapis_restricted_terms(database, schema, rows)
    if terms:
        result["data_use_terms"] = terms
    result.update({
        "notes": notes,
        "query": {
            "url": f"{url}/sample/details",
            "method": "POST",
            "body": query,
            "data_version": data_version,
        },
    })
    return result


if __name__ == "__main__":
    import sys
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print("PDN MCP Server starting on http://localhost:8001/mcp-pdn ...")
        mcp.run(transport="streamable-http")
