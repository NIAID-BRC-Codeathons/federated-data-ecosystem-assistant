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
from datetime import date, timedelta
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

# Pathoplexus records carry these fields. A redistributor must keep them with
# the data, so every record query adds them wherever the schema has them.
LAPIS_TERMS_FIELDS = ["dataUseTerms", "dataUseTermsRestrictedUntil", "dataUseTermsUrl"]

# Pathoplexus states these duties on its terms of use pages. A redistributor
# must pass them on, so a record tool returns them beside the records.
LAPIS_TERMS_OPEN = (
    "Open records carry no use restriction. When you share them onward, keep "
    "the data use terms with them, and link each record to its page. "
    "Acknowledging the people who generated the data is expected: in a "
    "manuscript, create a Pathoplexus SeqSet and cite its DOI."
)

LAPIS_TERMS_RESTRICTED = (
    "The submitters restrict these records until the dates given. Keep the "
    "data use terms with the records, and link each record to its page when "
    "you present it publicly. In a manuscript, create a Pathoplexus SeqSet "
    "and cite its DOI. For any sequence that is central to the analysis, "
    "name the submitters as authors, or get a written authorship waiver. Add "
    "no further access restriction when you pass the records on."
)


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
        # A lineage field holds a hierarchy, so a filter on it can match a
        # whole clade with a "*" suffix.
        "lineage_fields": [
            m["name"] for m in config["metadata"] if m.get("generateLineageIndex")
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
    as soon as the response passes that size. The sequence endpoints answer
    with a bare list, and every other endpoint wraps its rows in "data".
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
        payload = json.loads(content)
        rows = payload if isinstance(payload, list) else payload["data"]
        return rows, resp.headers.get("lapis-data-version")


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


def _lapis_data_use_terms(database: dict, schema: dict, rows: list[dict]) -> dict | None:
    """Describe the data use terms of the rows. None when the database declares none.

    Pathoplexus asks a redistributor to pass the terms on and to link each
    record back, for open records as well as restricted ones.
    """
    if "dataUseTerms" not in schema["fields"] or not database["record_url"]:
        return None
    key = schema["primary_key"]
    records = []
    for row in rows:
        terms = row.get("dataUseTerms")
        if terms is None:
            continue
        record = {
            "accession": row[key],
            "terms": terms,
            "record_url": database["record_url"].format(accession=row[key]),
        }
        if terms == "RESTRICTED":
            record["restricted_until"] = row.get("dataUseTermsRestrictedUntil")
        records.append(record)
    if not records:
        return None
    obligations = []
    if any(r["terms"] != "RESTRICTED" for r in records):
        obligations.append(LAPIS_TERMS_OPEN)
    if any(r["terms"] == "RESTRICTED" for r in records):
        obligations.append(LAPIS_TERMS_RESTRICTED)
    return {
        "records": records,
        "terms_urls": sorted({r["dataUseTermsUrl"] for r in rows if r.get("dataUseTermsUrl")}),
        "obligations": obligations,
    }


# ********** list organisms **********

@mcp.tool()
def lapis_list_organisms() -> dict:
    """List the organisms the LAPIS tools can query, grouped by database.

    Pathoplexus holds human viral pathogens, GenSpectrum Loculus holds
    influenza lineages and dengue serotypes, and CoV-Spectrum holds open
    SARS-CoV-2 data.

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
    """Describe one organism: its fields, genes, segments, and filter syntax.

    Call this before you filter or group. Each organism names its fields
    differently. Pathoplexus has sampleCollectionDate, CoV-Spectrum has date.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox".
        field_search: Text that a field name must contain, e.g. "country".
            Without it, the reply lists every field, often over 100, plus the
            segments and genes.

    Example questions:
        "Which fields can I filter mpox sequences on?"
        "Which genes can I query for measles?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    # A multi-segment organism, or any gene, needs the name before the
    # position. LAPIS rejects a bare nucleotide mutation in that case.
    prefix = f"{schema['segments'][0]}:" if len(schema["segments"]) > 1 else ""
    gene = schema["genes"][0] if schema["genes"] else "GENE"
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
    if schema["lineage_fields"]:
        notes.append(
            "A lineage field holds a hierarchy. A plain value matches that "
            "lineage alone, and a '*' suffix matches the lineage with every "
            "sublineage under it. On sars-cov-2 in September 2026, "
            "pangoLineage='JN.1' matched 29,360 sequences, and "
            "pangoLineage='JN.1*' matched 210,949. Use "
            "the '*' suffix unless you mean the one lineage. The lineage "
            f"fields here: {', '.join(schema['lineage_fields'])}."
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
        "lineage_fields": schema["lineage_fields"],
        "field_search": field_search,
        "fields_matched": len(fields),
        "fields_total": len(schema["fields"]),
        "fields": fields,
        "filter_syntax": {
            "exact": (
                "Pass a field name with a value, or with a list of values to "
                "match any of them."
            ),
            "range": (
                "Add From or To to an int, float, or date field, e.g. "
                "sampleCollectionDateRangeLowerFrom. A string field takes no "
                "range, even when it holds a date."
            ),
            "lineages": (
                "A lineage field takes a '*' suffix to match a lineage with "
                "every sublineage under it, e.g. pangoLineage='JN.1*'. Without "
                "the suffix, only that one lineage matches. The lineage fields "
                "here: "
                + (", ".join(schema["lineage_fields"]) or "none")
                + "."
            ),
            "text and missing values": (
                "Add .regex to a string field for a regular expression. Add "
                ".isNull to any field, with true or false."
            ),
            "mutations": (
                "nucleotideMutations or aminoAcidMutations with a list keeps "
                "the sequences that carry every listed mutation, e.g. "
                f"['{prefix}A123G'] or ['{gene}:N50Y']. The original symbol is "
                f"optional, so '{gene}:50Y' also works."
            ),
            "advancedQuery": (
                "One boolean expression over fields and mutations. It takes "
                "AND, OR, NOT, the symbols & | !, IsNull(field), "
                "field.regex='...', and [2-of: a, b, c] for a count of "
                "matches. Example: \"geoLocCountry='Guinea' AND NOT "
                "IsNull(hostNameScientific)\"."
            ),
        },
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
    """Count samples for one organism, as one total or grouped by fields.

    A count measures sequencing effort, not infection incidence. Never report
    it as a case count.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox".
        group_by: Fields to group by, e.g. ["geoLocCountry"]. Empty gives one
            total.
        filters: Field filters, e.g. {"geoLocCountry": "Brazil",
            "sampleCollectionDateRangeLowerFrom": "2025-01-01"}. A lineage
            value needs a "*" to cover its sublineages, e.g.
            {"pangoLineage": "JN.1*"}. Mutation filters and advancedQuery work
            here too. lapis_describe_organism gives the field names and the
            full syntax.
        latest_version_only: Count each sequence once, skipping older versions
            and revocations (default True).
        order_by: "count" for the largest groups first, or "group" for the
            order of the group values.
        max_groups: Groups to return (default 50, max 500).

    Example questions:
        "How many mpox sequences come from each country?"
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
    """List the substitutions and deletions in one organism's sequences.

    A mutation reads <gene or segment>:<from><position><to>, e.g. S:N501Y, or
    C241T on a single-segment genome. A "-" marks a deletion. For insertions,
    call lapis_get_insertions.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "sars-cov-2".
        sequence_type: "amino_acid" for changes within genes, or "nucleotide".
        gene_or_segment: One gene or segment, e.g. "S". Empty gives all.
        filters: As in lapis_aggregate_samples.
        min_proportion: Drop mutations below this share (default 0.05, the
            LAPIS default). It is a reporting filter, not a biological cutoff.
            Lower it to 0 for rare mutations, though with gene_or_segment a
            low value can pass the size limit.
        latest_version_only: As in lapis_aggregate_samples.
        order_by: "proportion" for the most common first, or "position".
        max_mutations: Mutations to return (default 50, max 500).

    Example questions:
        "Which spike mutations are common in Swiss SARS-CoV-2 since 2025?"
        "Which nucleotide mutations are most common in mpox?"
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
    """Get sample records for one organism, with the fields you name.

    For counts, use lapis_aggregate_samples, which returns far less text. Name
    only the fields you need: a record can carry over 150, and authors alone
    can run to thousands of characters.

    Pathoplexus records arrive with a data_use_terms block. Keep it with them,
    and follow it before you pass them on or publish from them.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox".
        fields: Fields to return, e.g. ["geoLocCountry",
            "sampleCollectionDate"]. The accession and the data use terms come
            as well.
        filters: As in lapis_aggregate_samples.
        latest_version_only: As in lapis_aggregate_samples.
        order_by: A field to sort by, e.g. "sampleCollectionDate". Empty for
            the database order.
        descending: Sort from highest to lowest (default False).
        limit: Records to return (default 10, max 100).
        offset: Records to skip, for the next page (default 0).

    Example questions:
        "Show the 10 most recent mpox sequences from the DRC."
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
    terms = _lapis_data_use_terms(database, schema, rows)
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


# ********** get sequences **********

LAPIS_MAX_SEQUENCE_RECORDS = 50

# One mpox genome runs to about 197,000 characters, so a record limit alone
# cannot keep a response small. This budget caps the sequence characters in
# one response, at roughly 12,000 tokens.
LAPIS_MAX_SEQUENCE_CHARS = 50_000


@mcp.tool()
def lapis_get_sequences(
    organism: str,
    alignment: Literal[
        "unaligned_nucleotide", "aligned_nucleotide", "aligned_amino_acid"
    ] = "unaligned_nucleotide",
    gene_or_segment: str = "",
    filters: dict[str, Any] | None = None,
    latest_version_only: bool = True,
    limit: int = 5,
) -> dict:
    """Get sequences for one organism as FASTA, for a local analysis.

    Sequences are long: one mpox genome runs to 197,000 characters. A response
    carries at most 50,000 characters of sequence and says when that cuts the
    list short, so ask for a gene or a segment. For mutations or counts, use
    lapis_get_mutations or lapis_aggregate_samples, which download none.

    A FASTA header carries no terms of use, so Pathoplexus sequences arrive
    with a data_use_terms block. Keep it with them.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "h5n1".
        alignment: "unaligned_nucleotide" as submitted, "aligned_nucleotide"
            padded to the reference, or "aligned_amino_acid" for a gene.
        gene_or_segment: Required for "aligned_amino_acid", e.g. "S". A
            segment for a nucleotide query on a multi-segment organism, e.g.
            "seg4". Empty gives every segment.
        filters: As in lapis_aggregate_samples. Filter on the accession to
            fetch named records.
        latest_version_only: As in lapis_aggregate_samples.
        limit: Records to fetch (default 5, max 50). The budget can still cut
            the list short.

    Example questions:
        "Give me the HA segment of 5 recent H5N1 sequences from cattle."
        "Fetch spike protein sequences from Swiss SARS-CoV-2 samples."
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    if alignment == "aligned_amino_acid":
        names, kind = schema["genes"], "gene"
        if not names:
            raise ToolError(
                f"{schema['instance_name']} declares no genes, so it has no "
                "amino acid sequences. Ask for a nucleotide alignment instead."
            )
        if not gene_or_segment:
            raise ToolError(
                f"Name the gene to translate. The genes of "
                f"{schema['instance_name']}: {', '.join(names)}."
            )
        endpoint = f"/sample/alignedAminoAcidSequences/{gene_or_segment}"
    else:
        names, kind = schema["segments"], "segment"
        stem = (
            "unalignedNucleotideSequences"
            if alignment == "unaligned_nucleotide"
            else "alignedNucleotideSequences"
        )
        # A single-segment organism has no per-segment route.
        segment = gene_or_segment if len(names) > 1 else ""
        endpoint = f"/sample/{stem}/{segment}" if segment else f"/sample/{stem}"
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
    limit = max(1, min(limit, LAPIS_MAX_SEQUENCE_RECORDS))
    query = {**body, "limit": limit, "dataFormat": "JSON"}

    rows, data_version = _lapis_post(f"{url}{endpoint}", query)
    # A row holds one sequence per segment or gene, beside the primary key.
    key = schema["primary_key"]
    parts = [
        (row[key], name, seq)
        for row in rows
        for name, seq in row.items()
        if name != key and isinstance(seq, str)
    ]
    named = len({name for _, name, _ in parts}) > 1

    fasta, lengths, characters, truncated = [], [], 0, False
    for accession, name, sequence in parts:
        if characters + len(sequence) > LAPIS_MAX_SEQUENCE_CHARS:
            if not fasta:
                raise ToolError(
                    f"One {organism} sequence holds {len(sequence):,} characters, "
                    f"above the budget of {LAPIS_MAX_SEQUENCE_CHARS:,}. Ask for a "
                    f"single gene or segment, or use lapis_get_mutations to compare "
                    "sequences without downloading them."
                )
            truncated = True
            break
        header = f"{accession}|{name}" if named else accession
        fasta.append(f">{header}\n{sequence}")
        lengths.append({"accession": accession, kind: name, "length": len(sequence)})
        characters += len(sequence)

    terms = None
    terms_fields = [f for f in LAPIS_TERMS_FIELDS if f in schema["fields"]]
    accessions = list(dict.fromkeys(row["accession"] for row in lengths))
    if terms_fields and accessions:
        term_rows, _ = _lapis_post(
            f"{url}/sample/details", {key: accessions, "fields": [key, *terms_fields]}
        )
        terms = _lapis_data_use_terms(database, schema, term_rows)

    notes = []
    if alignment.startswith("aligned"):
        notes.append(
            "An aligned sequence is padded to the reference, so it carries N "
            "and gap characters that the submitted sequence does not have."
        )
    if truncated:
        notes.append(
            "The character budget cut the list short. Lower limit, or ask for "
            "one gene or segment, to see the rest."
        )
    if version_note:
        notes.append(version_note)
    result = {
        "organism": organism,
        "database": database["name"],
        "alignment": alignment,
        "gene_or_segment": gene_or_segment,
        "records_returned": len(accessions),
        "sequences_returned": len(fasta),
        "total_characters": characters,
        "truncated": truncated,
        "lengths": lengths,
        "fasta": "\n".join(fasta),
    }
    if terms:
        result["data_use_terms"] = terms
    result.update({
        "notes": notes,
        "query": {
            "url": f"{url}{endpoint}",
            "method": "POST",
            "body": query,
            "data_version": data_version,
        },
    })
    return result


# ********** get insertions **********

LAPIS_MAX_INSERTIONS = 500


@mcp.tool()
def lapis_get_insertions(
    organism: str,
    sequence_type: Literal["nucleotide", "amino_acid"],
    gene_or_segment: str = "",
    filters: dict[str, Any] | None = None,
    latest_version_only: bool = True,
    min_count: int = 1,
    order_by: Literal["count", "position"] = "count",
    max_insertions: int = 50,
) -> dict:
    """List the insertions in one organism's sequences.

    lapis_get_mutations reports substitutions and deletions only, so an
    insertion needs this tool. The SARS-CoV-2 insertion ins_22204:GAGCCAGAA
    marks Omicron BA.1. An insertion reads ins_<position>:<symbols>, or
    ins_<gene or segment>:<position>:<symbols>, e.g. ins_seg8:79:TG. LAPIS
    reports no coverage for an insertion, so a count carries no proportion.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "h5n1".
        sequence_type: "nucleotide" for the genome, or "amino_acid" within
            genes.
        gene_or_segment: One gene or segment, e.g. "S". Empty gives all.
        filters: As in lapis_aggregate_samples.
        latest_version_only: As in lapis_aggregate_samples.
        min_count: Drop an insertion that fewer sequences carry (default 1).
        order_by: "count" for the most common first, or "position".
        max_insertions: Insertions to return (default 50, max 500).

    Example questions:
        "Which insertions do SARS-CoV-2 spike sequences carry?"
        "Which mpox insertions are most common?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    if sequence_type == "amino_acid":
        endpoint = "/sample/aminoAcidInsertions"
        names, kind = schema["genes"], "gene"
        if not names:
            raise ToolError(
                f"{schema['instance_name']} declares no genes, so it has no "
                "amino acid insertions. Use sequence_type 'nucleotide' instead."
            )
    else:
        endpoint = "/sample/nucleotideInsertions"
        names, kind = schema["segments"], "segment"
    # A single-segment genome leaves sequenceName empty, so there is nothing
    # to filter on.
    name_filter = gene_or_segment if len(names) > 1 or kind == "gene" else ""
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
    max_insertions = max(1, min(max_insertions, LAPIS_MAX_INSERTIONS))
    query = dict(body)
    if order_by == "count":
        order = [{"field": "count", "type": "descending"}]
    else:
        order = [
            {"field": "sequenceName", "type": "ascending"},
            {"field": "position", "type": "ascending"},
        ]
    # LAPIS cannot filter insertions by gene or segment, so a name filter
    # fetches them all and filters here, as lapis_get_mutations does.
    if not name_filter:
        query.update({"orderBy": order, "limit": max_insertions})

    try:
        rows, data_version = _lapis_post(
            f"{url}{endpoint}",
            query,
            max_bytes=LAPIS_MAX_FILTER_DOWNLOAD_BYTES if name_filter else None,
        )
    except LapisResponseTooLarge:
        raise ToolError(
            f"The {sequence_type.replace('_', ' ')} insertions of {organism} pass "
            f"{LAPIS_MAX_FILTER_DOWNLOAD_BYTES // 1_000_000} MB, which is too much "
            f"to filter down to {kind} {gene_or_segment}. Narrow the filters."
        ) from None
    fetched = len(rows)
    if name_filter:
        rows = [r for r in rows if r["sequenceName"] == name_filter]
    rows = [r for r in rows if r["count"] >= min_count]
    if name_filter:
        rows.sort(key=lambda r: -r["count"] if order_by == "count" else r["position"])
    # LAPIS already cut the rows without a name filter, so a full page means
    # more insertions remain.
    truncated = len(rows) > max_insertions if name_filter else fetched == max_insertions
    rows = rows[:max_insertions]
    # The insertion name already carries the inserted symbols.
    rows = [{k: v for k, v in r.items() if k != "insertedSymbols"} for r in rows]

    notes = [
        "A count is the number of sequences that carry the insertion, among "
        "the sequences that match the filters. LAPIS reports no coverage for "
        "an insertion, so there is no proportion.",
        "An insertion that one or two sequences carry can be an assembly "
        "artifact.",
    ]
    if version_note:
        notes.append(version_note)
    return {
        "organism": organism,
        "database": database["name"],
        "sequence_type": sequence_type,
        "gene_or_segment": gene_or_segment,
        "min_count": min_count,
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


# ********** get mutations over time **********

LAPIS_MAX_PERIODS = 60

# LAPIS counts over a date field, and the field must hold a date. Pathoplexus
# stores sampleCollectionDate as text, so it keeps a date copy beside it.
LAPIS_PREFERRED_DATE_FIELDS = ["date", "sampleCollectionDateRangeLower"]


def _lapis_date_field(schema: dict, date_field: str) -> str:
    """Return the date field to count over, or explain what the organism has."""
    dates = [f for f, t in schema["fields"].items() if t == "date"]
    if not dates:
        raise ToolError(f"{schema['instance_name']} declares no date field to count over.")
    if date_field:
        if date_field not in dates:
            raise ToolError(
                f"'{date_field}' is not a date field of {schema['instance_name']}. "
                f"Its date fields: {', '.join(dates)}."
            )
        return date_field
    for preferred in LAPIS_PREFERRED_DATE_FIELDS:
        if preferred in dates:
            return preferred
    return dates[0]


def _lapis_date_ranges(date_from: str, date_to: str, interval: str) -> list[dict]:
    """Cut a span into periods. The first starts on date_from, the rest on a boundary."""
    try:
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise ToolError(
            "date_from and date_to take an ISO date, e.g. '2025-01-01'."
        ) from None
    if end < start:
        raise ToolError("date_to falls before date_from.")
    step = {"month": 1, "quarter": 3, "year": 12}[interval]
    ranges, cursor = [], start
    while cursor <= end:
        month = cursor.month - 1 + step
        following = date(cursor.year + month // 12, month % 12 + 1, 1)
        ranges.append({
            "dateFrom": cursor.isoformat(),
            "dateTo": min(following - timedelta(days=1), end).isoformat(),
        })
        if len(ranges) > LAPIS_MAX_PERIODS:
            raise ToolError(
                f"That span holds over {LAPIS_MAX_PERIODS} periods of one "
                f"{interval}. Use a wider interval, or a shorter span."
            )
        cursor = following
    return ranges


@mcp.tool()
def lapis_get_mutations_over_time(
    organism: str,
    sequence_type: Literal["nucleotide", "amino_acid"],
    mutations: list[str],
    date_from: str,
    date_to: str,
    interval: Literal["month", "quarter", "year"] = "month",
    filters: dict[str, Any] | None = None,
    latest_version_only: bool = True,
    date_field: str = "",
) -> dict:
    """Track named mutations over time, to see whether each one grows or fades.

    For each mutation and each period, the reply gives the sequences that carry
    it, the sequences that cover its position, and the proportion between them.
    A rising proportion is the signal to look for. lapis_get_mutations finds
    which mutations to name.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "sars-cov-2".
        sequence_type: "amino_acid" for changes within genes, or "nucleotide".
        mutations: The mutations to track, e.g. ["S:N501Y", "S:F456L"]. Name
            the gene, or the segment on a multi-segment organism.
        date_from: The first date, e.g. "2025-01-01".
        date_to: The last date, e.g. "2025-12-31".
        interval: "month", "quarter", or "year" (default "month").
        filters: As in lapis_aggregate_samples.
        latest_version_only: As in lapis_aggregate_samples.
        date_field: The date field to count over. Empty picks one, usually the
            collection date.

    Example questions:
        "Is S:F456L growing in Switzerland through 2025?"
        "How did the mpox clade Ib mutations spread over the last two years?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    if not mutations:
        raise ToolError(
            "Name at least one mutation. lapis_get_mutations lists the common ones."
        )
    if sequence_type == "amino_acid":
        endpoint = "/component/aminoAcidMutationsOverTime"
        names, kind = schema["genes"], "gene"
    else:
        endpoint = "/component/nucleotideMutationsOverTime"
        names, kind = schema["segments"], "segment"
    # LAPIS answers a malformed mutation with "Failed to read request", so
    # check the prefix here instead.
    shown = ", ".join(names[:10]) + (", ..." if len(names) > 10 else "")
    for mutation in mutations:
        prefix = mutation.split(":")[0] if ":" in mutation else ""
        if prefix and prefix not in names:
            raise ToolError(
                f"'{mutation}' starts with '{prefix}', which is not a {kind} of "
                f"{schema['instance_name']}. Its {kind}s: {shown}."
            )
        if not prefix and (kind == "gene" or len(names) > 1):
            example = f"{names[0]}:N50Y" if kind == "gene" else f"{names[0]}:A123G"
            raise ToolError(
                f"'{mutation}' needs a {kind} before the position, e.g. '{example}'."
            )

    body = dict(filters or {})
    _lapis_check_filters(schema, body)
    version_note = _lapis_version_filter(schema, body, latest_version_only)
    query = {
        "filters": body,
        "includeMutations": mutations,
        "dateRanges": _lapis_date_ranges(date_from, date_to, interval),
        "dateField": _lapis_date_field(schema, date_field),
    }

    result, data_version = _lapis_post(f"{url}{endpoint}", query)
    periods = [
        {"from": r["dateFrom"], "to": r["dateTo"], "sequences_with_a_date": total}
        for r, total in zip(result["dateRanges"], result["totalCountsByDateRange"])
    ]
    rows = []
    for mutation, series in zip(result["mutations"], result["data"]):
        rows.append({
            "mutation": mutation,
            "count": [p["count"] for p in series],
            "coverage": [p["coverage"] for p in series],
            "proportion": [
                round(p["count"] / p["coverage"], 4) if p["coverage"] else None
                for p in series
            ],
        })

    notes = [
        "Each count, coverage, and proportion follows the order of the "
        "periods. A proportion divides the count by the coverage of that "
        "period, so compare proportions, not counts.",
        "A period with a small coverage moves a proportion a long way. Read "
        "the coverage beside every proportion.",
    ]
    if version_note:
        notes.append(version_note)
    return {
        "organism": organism,
        "database": database["name"],
        "sequence_type": sequence_type,
        "date_field": query["dateField"],
        "periods": periods,
        "rows": rows,
        "notes": notes,
        "query": {
            "url": f"{url}{endpoint}",
            "method": "POST",
            "body": query,
            "data_version": data_version,
        },
    }


if __name__ == "__main__":
    import sys
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print("PDN MCP Server starting on http://localhost:8001/mcp-pdn ...")
        mcp.run(transport="streamable-http")
