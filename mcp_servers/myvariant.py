"""MyVariant.info MCP Server

A remote MCP server exposing tools over MyVariant.info, the BioThings variant
annotation service: variant search, annotation retrieval, and batch
identifier mapping across 1.5 billion variants aggregated from ClinVar,
dbSNP, gnomAD, CADD, CIViC, and 14 other sources.

Run over HTTP:  python mcp_servers/myvariant.py
Run over stdio: python mcp_servers/myvariant.py --stdio
"""

import difflib
import json
import re
import time
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP(
    name="MyVariant MCP",
    dependencies=["mcp", "requests"],
    instructions=(
        "Query MyVariant.info for variant annotation: clinical significance, "
        "population frequency, deleteriousness predictions, and functional "
        "consequence, cross-referenced to ClinVar, dbSNP, gnomAD and CIViC"
    ),
    port=8004,
    streamable_http_path="/mcp-myvariant",
)


# MyVariant.info  (BioThings variant annotation service)
#
# MyVariant aggregates 19 upstream sources (ClinVar, dbSNP, gnomAD, ExAC,
# CADD, CIViC and others) into one document per variant, keyed by its
# genomic HGVS id. The tools read the field index at runtime, so no field
# name is hard-coded here.


# ********** shared by the MyVariant tools **********

MYVARIANT_API = "https://myvariant.info/v1"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "federated-data-ecosystem-assistant "
        "(+https://github.com/NIAID-BRC-Codeathons/federated-data-ecosystem-assistant)"
    ),
}

# The field index changes only when MyVariant rebuilds, so refetch it after
# an hour.
SCHEMA_TTL_SECONDS = 3600

# Limits the API enforces. Exceeding one returns HTTP 400, so the tools clamp
# rather than let an agent discover them by trial and error.
MAX_SIZE = 1000
MAX_RESULT_WINDOW = 10000
MAX_FACET_SIZE = 1000
MAX_BATCH_IDS = 1000

# The genome builds MyVariant indexes. hg19 is the default: the query and
# annotation endpoints both take it as the implicit assembly, and switching
# to hg38 changes both the search corpus and the coordinates in every hit.
ASSEMBLIES = {"hg19", "hg38"}

# Elasticsearch refuses an aggregation on an analysed text field, so only a
# field of one of these types can be faceted. Verified against
# /metadata/fields: chrom, clinvar.rcv.clinical_significance, and most gene
# symbol fields are "text" and cannot be faceted; dbnsfp.genename and
# cadd.phred can.
FACETABLE_TYPES = {"keyword", "integer", "long", "boolean", "byte", "date", "float", "double", "short"}

# Fields the API adds to a response rather than reading from a variant
# document.
RESPONSE_ONLY_FIELDS = {"_id", "_score", "_version", "query", "notfound", "all"}

# Human-facing pages, used to build provenance links.
DBSNP_URL = "https://www.ncbi.nlm.nih.gov/snp/{rsid}"
CLINVAR_URL = "https://www.ncbi.nlm.nih.gov/clinvar/variation/{variant_id}/"

# Returned by every search hit: the id, the gene it falls in wherever a
# source names one, and the clinical read if ClinVar has one.
SUMMARY_FIELDS = [
    "chrom", "vcf", "dbsnp.rsid", "clinvar.gene.symbol", "snpeff.ann.genename",
    "clinvar.rcv.clinical_significance", "cadd.phred",
]

# A variant document with every section can be very large: BRAF V600E
# (chr7:g.140453136A>T), one of the most studied variants in oncology, is
# 486 KB, of which CIViC alone is 438 KB. So myvariant_get_variant takes
# sections rather than returning everything, and CIViC is its own section
# rather than folded into "clinical", since asking for ClinVar should not
# silently pull in the largest section by an order of magnitude.
VARIANT_SECTIONS: dict[str, list[str]] = {
    "core": [
        "chrom", "vcf", "hg19", "hg38", "observed",
        "dbsnp.rsid", "dbsnp.gene", "clinvar.gene.symbol", "snpeff.ann.genename",
    ],
    "clinical": ["clinvar", "cosmic", "docm", "emv", "mutdb"],
    "civic": ["civic"],
    "population": ["gnomad_exome", "gnomad_genome", "exac", "exac_nontcga", "evs"],
    "predictions": ["dbnsfp", "cadd"],
    "functional": ["snpeff", "cgi"],
    "other": ["grasp", "gwassnps", "geno2mp", "wellderly", "snpedia"],
}

# ClinVar's rcv list and CIViC's molecularProfiles/evidenceItems lists are
# unbounded: a pharmacogenomic hotspot can carry 100+ ClinVar submissions,
# and BRAF V600E carries 15 CIViC molecular profiles, one of them alone
# holding 100 evidence items. Cap all three, same as generif in mygene.py.
MAX_CLINVAR_RCV = 25
MAX_CIVIC_PROFILES = 5
MAX_CIVIC_EVIDENCE_PER_PROFILE = 5

# A response above this many characters of JSON is cut short, so one call
# cannot exhaust the agent's context.
MAX_RESPONSE_CHARS = 60000

# The note explaining a trim is added after the rows have been measured, so
# trimming leaves room for it rather than overshooting the budget by its
# length. Halving is the floor, so a small budget still trims sensibly.
TRUNCATION_RESERVE = 400

# A bare id such as "chr7:g.140453136A>T" or "NM_007294.4:c.5074G>A" holds a
# colon, which the Lucene query_string parser reads as field:value. Sent
# unquoted, "NM_007294.4" is read as a field name that does not exist, and
# the query silently matches nothing. This detects a q that is nothing but
# such an id, so it can be quoted into a phrase query instead of misread.
_HGVS_LIKE = re.compile(r"^\S+:[a-zA-Z]\.\S+$")

_schema_cache: dict[str, tuple[float, Any]] = {}


def _clean_params(params: dict) -> dict:
    """Drop empty values and render booleans and lists the way the API expects."""
    out = {}
    for key, value in params.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            out[key] = ",".join(str(v) for v in value)
        else:
            out[key] = value
    return out


def _api_error(status: int, payload: Any) -> ToolError:
    """Turn a BioThings error body into a short error an agent can act on.

    An error body carries the failure and, for a validation error, the
    offending parameter and its bound::

        {"code": 400, "success": false, "error": "Bad Request",
         "keyword": "size", "max": 1000, "num": 1001}
        {"code": 400, "success": false, "error": "search_phase_execution_exception",
         "root_cause_line_00": "Fielddata is disabled on [chrom] ..."}
    """
    if not isinstance(payload, dict):
        return ToolError(f"MyVariant.info returned HTTP {status}: {str(payload)[:300]}")
    parts = [str(payload.get("error", f"HTTP {status}"))]
    parts += [str(v) for k, v in sorted(payload.items()) if k.startswith("root_cause")]
    if "details" in payload:
        parts.append(str(payload["details"]))
    if "keyword" in payload:
        bound = payload.get("max", payload.get("min"))
        parts.append(f"parameter '{payload['keyword']}' out of range (limit {bound})")
    detail = "; ".join(p for p in parts if p)
    return ToolError(f"MyVariant.info error (HTTP {status}): {detail[:500]}")


def _request(method: str, path: str, **kwargs: Any) -> Any:
    """Call the API. A MyVariant error becomes a short tool error.

    The API signals failure both with an HTTP status and with a body-level
    "success": false, so both are checked. The index holds 1.5 billion
    documents, so an unscoped facet or query can take tens of seconds; the
    timeout is longer than a typical REST call to leave room for that.
    """
    url = f"{MYVARIANT_API}{path}"
    try:
        resp = requests.request(method, url, headers=HEADERS, timeout=90, **kwargs)
    except requests.Timeout as exc:
        raise ToolError(
            f"Request to {url} timed out after 90s. The index holds 1.5 "
            "billion variants, so an unscoped query or facet can be slow. "
            "Add a chrom, gene, or rsid filter to narrow it."
        ) from exc
    except requests.RequestException as exc:
        raise ToolError(f"Could not reach MyVariant.info at {url}: {exc}") from exc

    try:
        payload = resp.json()
    except ValueError:
        raise ToolError(
            f"MyVariant.info returned non-JSON content from {url} "
            f"(HTTP {resp.status_code}): {resp.text[:300]}"
        ) from None

    if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("success") is False):
        raise _api_error(resp.status_code, payload)
    return payload


def _get(path: str, params: dict | None = None) -> Any:
    return _request("GET", path, params=_clean_params(params or {}))


def _post(path: str, body: dict) -> Any:
    return _request("POST", path, json=body)


def _field_index() -> dict[str, dict]:
    """Fetch the searchable field index, with a cache.

    Returns 1,621 fields, each with its type and whether it is indexed. A
    field that is not indexed can be returned but not searched or faceted.
    """
    cached = _schema_cache.get("fields")
    if cached and time.monotonic() - cached[0] < SCHEMA_TTL_SECONDS:
        return cached[1]
    fields = _get("/metadata/fields")
    _schema_cache["fields"] = (time.monotonic(), fields)
    return fields


def _unknown_field(name: str) -> ToolError:
    """Build an error naming the closest real fields to an unknown one."""
    names = list(_field_index())
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    close += [n for n in names if name.lower() in n.lower() and n not in close]
    hint = f" Close matches: {', '.join(close[:5])}." if close else ""
    return ToolError(
        f"'{name}' is not a field of MyVariant.info.{hint} "
        "Call myvariant_describe_fields with field_search to find field names."
    )


def _check_fields(names: list[str], *, must_be_indexed: bool = False) -> None:
    """Reject a field the index does not declare.

    MyVariant ignores an unknown name in "fields" without complaint, so
    fields=chrom,nosuchfield silently returns only chrom. Validating here
    turns a typo into an error rather than missing data.
    """
    index = _field_index()
    for name in names:
        if name in RESPONSE_ONLY_FIELDS:
            continue
        if name not in index:
            raise _unknown_field(name)
        if must_be_indexed and not index[name].get("index", True):
            raise ToolError(
                f"'{name}' is stored but not indexed, so it cannot be searched "
                "or faceted. It can still be requested in fields."
            )


def _check_facetable(name: str) -> None:
    """Reject a field that cannot be faceted, and name one that can.

    An aggregation on a text field fails deep inside Elasticsearch. Fields
    that read as free text elsewhere in this schema -- chrom,
    clinvar.rcv.clinical_significance, most *.genename fields -- are all
    "text" and cannot be faceted, which the error alone does not make clear.
    """
    index = _field_index()
    if name not in index:
        raise _unknown_field(name)
    spec = index[name]
    if not spec.get("index", True):
        raise ToolError(
            f"'{name}' is stored but not indexed, so it cannot be searched or "
            "faceted. It can still be requested in fields."
        )
    field_type = spec.get("type")
    if field_type in FACETABLE_TYPES:
        return
    parent = name.rsplit(".", 1)[0] if "." in name else name
    siblings = [
        f for f, spec in index.items()
        if f.startswith(f"{parent}.")
        and spec.get("type") in FACETABLE_TYPES
        and spec.get("index", True)
    ]
    hint = f" Try one of: {', '.join(sorted(siblings)[:5])}." if siblings else ""
    raise ToolError(
        f"'{name}' has type {field_type} and only "
        f"{', '.join(sorted(FACETABLE_TYPES))} fields can be faceted.{hint} "
        "Call myvariant_describe_fields to see field types."
    )


def _resolve_assembly(assembly: str) -> str:
    """Validate an assembly argument. Empty means the server default, hg19."""
    value = assembly.strip().lower()
    if not value:
        return ""
    if value not in ASSEMBLIES:
        raise ToolError(
            f"'{assembly}' is not a genome build MyVariant indexes. "
            f"Assemblies: {', '.join(sorted(ASSEMBLIES))}."
        )
    return value


def _split(value: str) -> list[str]:
    """Split a comma-separated argument into a clean list."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _clamp_paging(size: int, offset: int) -> tuple[int, int, str | None]:
    """Hold size and offset inside the API's limits. Return a note when clamped."""
    clamped_size = max(0, min(size, MAX_SIZE))
    # offset alone above the result window is rejected by the API outright
    # (HTTP 400 on "from"), not just in combination with size, so it is
    # capped here rather than only checked against size below.
    clamped_offset = max(0, min(offset, MAX_RESULT_WINDOW))
    note = None
    if clamped_offset + clamped_size > MAX_RESULT_WINDOW:
        clamped_size = max(0, MAX_RESULT_WINDOW - clamped_offset)
        note = (
            f"offset + size must stay at or under {MAX_RESULT_WINDOW}, so size "
            f"was cut to {clamped_size}. Narrow the query instead of paging "
            "deeper."
        )
    if size > MAX_SIZE:
        note = f"size was cut from {size} to the {MAX_SIZE} the API allows."
    return clamped_size, clamped_offset, note


def _quote_if_hgvs_like(q: str) -> tuple[str, str | None]:
    """Quote a bare HGVS id so its colon is not read as a field separator.

    "NM_007294.4:c.5074G>A" unquoted is parsed as field "NM_007294.4" with
    value "c.5074G>A", which does not exist, and matches nothing. Quoted, it
    is a phrase query that matches. Only a q that is nothing but such an id is
    touched, so a genuine field:value query from the caller is untouched.
    """
    stripped = q.strip()
    if stripped.startswith('"') or not _HGVS_LIKE.match(stripped):
        return q, None
    return f'"{stripped}"', (
        f"'{stripped}' looks like an HGVS id, and its colon would otherwise be "
        "read as a field name. It was quoted as a phrase. For a single "
        "authoritative match rather than a text search, use "
        "myvariant_get_variant for a genomic HGVS id (chr7:g.140453136A>T), "
        "or myvariant_map_ids for a transcript HGVS id "
        "(NM_007294.4:c.5074G>A), which a text search can miss or misrank."
    )


def _provenance(hit: dict) -> dict:
    """Build the outbound links for a variant, from whichever ids it carries."""
    links = {}
    dbsnp = hit.get("dbsnp")
    dbsnp = dbsnp[0] if isinstance(dbsnp, list) and dbsnp else dbsnp
    if isinstance(dbsnp, dict) and dbsnp.get("rsid"):
        links["dbsnp_url"] = DBSNP_URL.format(rsid=dbsnp["rsid"])
    clinvar = hit.get("clinvar")
    clinvar = clinvar[0] if isinstance(clinvar, list) and clinvar else clinvar
    if isinstance(clinvar, dict) and clinvar.get("variant_id"):
        links["clinvar_url"] = CLINVAR_URL.format(variant_id=clinvar["variant_id"])
    if hit.get("_id"):
        links["myvariant_url"] = f"{MYVARIANT_API}/variant/{hit['_id']}"
    return links


def _trim_list(container: dict, key: str, max_items: int, label: str) -> str | None:
    """Cut a long list down to max_items. Return a note when it was cut."""
    items = container.get(key)
    if not isinstance(items, list) or len(items) <= max_items:
        return None
    dropped = len(items) - max_items
    container[key] = items[:max_items]
    return f"{label} held {len(items)} entries. Kept {max_items} and dropped {dropped}."


def _trim_clinvar(doc: dict) -> str | None:
    """Cap ClinVar's rcv list: a pharmacogenomic hotspot can carry 100+."""
    clinvar = doc.get("clinvar")
    if not isinstance(clinvar, dict):
        return None
    note = _trim_list(clinvar, "rcv", MAX_CLINVAR_RCV, "clinvar.rcv")
    return f"{note} Each rcv is one ClinVar submission for this variant." if note else None


def _trim_civic(doc: dict) -> str | None:
    """Cap CIViC's molecular profiles, and the evidence within each one.

    BRAF V600E carries 15 profiles, and its first alone holds 100 evidence
    items: capping only the outer list would still leave the response over
    budget, so both levels are capped.
    """
    civic = doc.get("civic")
    if not isinstance(civic, dict):
        return None
    notes = []
    outer = _trim_list(civic, "molecularProfiles", MAX_CIVIC_PROFILES, "civic.molecularProfiles")
    if outer:
        notes.append(outer)
    for profile in civic.get("molecularProfiles", []):
        if isinstance(profile, dict):
            inner = _trim_list(
                profile, "evidenceItems", MAX_CIVIC_EVIDENCE_PER_PROFILE,
                f"civic.molecularProfiles[{profile.get('name', '?')}].evidenceItems",
            )
            if inner:
                notes.append(inner)
    return " ".join(notes) if notes else None


def _fit(root: dict, container: dict, rows_key: str) -> tuple[int, int]:
    """Trim container[rows_key] until the JSON of root fits the budget.

    Works on a list of rows or a mapping of them, and measures the whole root
    payload rather than the rows alone, so the reported size is the size the
    caller receives. Returns how many rows were kept and how many there were.
    """
    rows = container.get(rows_key)
    if not isinstance(rows, (list, dict)):
        return 0, 0
    # Test the type rather than the keys: an empty mapping has no keys, and
    # taking it for a list would slice a dict.
    is_mapping = isinstance(rows, dict)
    keys = list(rows) if is_mapping else []

    def head(count: int):
        return {k: rows[k] for k in keys[:count]} if is_mapping else rows[:count]

    budget = max(MAX_RESPONSE_CHARS - TRUNCATION_RESERVE, MAX_RESPONSE_CHARS // 2)
    total = len(rows)
    kept = total
    while kept > 1:
        container[rows_key] = head(kept)
        if len(json.dumps(root, default=str)) <= budget:
            break
        kept -= max(1, kept // 10)
    container[rows_key] = head(kept)
    return kept, total


def _budget(payload: dict, rows_key: str, count_key: str | None = None) -> None:
    """Hold a response inside the budget by dropping rows off the end.

    Corrects the count the response reports, so it never overstates how many
    rows came back.
    """
    kept, total = _fit(payload, payload, rows_key)
    if kept < total:
        payload["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it carries "
            f"{kept} of {total} rows. Ask for fewer fields or a smaller size."
        )
        if count_key:
            payload[count_key] = kept


# ********** describe fields **********

@mcp.tool()
def myvariant_describe_fields(field_search: str = "") -> dict:
    """Describe the variant document fields, id formats, and query syntax.

    Call this before you filter, facet, or name fields. MyVariant declares
    1,621 fields, and an unknown name in a query returns nothing rather than
    an error, so guessing a field name produces a silently empty answer.

    Args:
        field_search: Case-insensitive text a field name must contain, e.g.
            "clinvar", "gnomad", "cadd". Leave empty to list all 1,621.
    """
    index = _field_index()
    needle = field_search.lower()
    fields = [
        {
            "name": name,
            "type": spec.get("type"),
            "indexed": spec.get("index", True),
            "facetable": spec.get("type") in FACETABLE_TYPES and spec.get("index", True),
        }
        for name, spec in sorted(index.items())
        if needle in name.lower()
    ]
    description = {
        "field_search": field_search,
        "fields_matched": len(fields),
        "fields_total": len(index),
        "fields": fields,
        "assemblies": sorted(ASSEMBLIES),
        "id_format": {
            "genomic HGVS": (
                "The document id and the default query result, e.g. "
                "chr7:g.140453136A>T. Fetch one directly with "
                "myvariant_get_variant."
            ),
            "rsid": "A dbSNP id, e.g. rs334. One rsid can match several "
                    "variants: a position can carry more than one alternate "
                    "allele.",
            "transcript HGVS": (
                "Coding (NM_007294.4:c.5074G>A) or protein (p.) notation. "
                "Neither resolves through myvariant_get_variant or a text "
                "search; map it to a genomic id first with myvariant_map_ids "
                "using from_type=\"clinvar.hgvs.coding\" or "
                "\"clinvar.hgvs.protein\"."
            ),
        },
        "query_syntax": {
            "free text": "A bare gene symbol or rsid searches the fields "
                         "sources index by default, e.g. 'BRAF' or 'rs334'.",
            "field": 'A field name with a value, e.g. \'chrom:7\'.',
            "quoting": (
                "Any value containing a colon must be quoted, e.g. "
                "'\"NM_007294.4:c.5074G>A\"', or the parser reads the text "
                "before the colon as a field name instead of a value."
            ),
            "boolean": "AND, OR and NOT between clauses, with parentheses to "
                       "group.",
            "existence": "'_exists_:civic' for variants with CIViC evidence, "
                         "'_missing_:clinvar' for those without ClinVar.",
        },
        "notes": [
            "A field that is not indexed can be returned in fields but not "
            "searched or faceted.",
            "Only a field marked facetable can be counted with "
            "myvariant_facet_counts. chrom, clinvar.rcv.clinical_significance, "
            "and most gene-symbol fields are text and cannot be, so a "
            "chromosome or clinical-significance breakdown is not available "
            "as a facet; filter on them in q instead.",
            "hg19 is the default assembly. Switching to hg38 changes both "
            "which variants match and the coordinates on each hit.",
            "A variant with no CADD score but a 'cadd' section missing "
            "entirely, versus one present with a score, both mean the "
            "variant is in CADD's precomputed universe or not; CADD scores "
            "nearly every possible substitution, observed or not.",
        ],
    }
    _budget(description, "fields")
    return description


# ********** search variants **********

@mcp.tool()
def myvariant_search_variants(
    q: str = "",
    gene: str = "",
    rsid: str = "",
    chrom: str = "",
    clinical_significance: str = "",
    assembly: str = "",
    size: int = 10,
    offset: int = 0,
    fields: str = "",
    sort: str = "",
) -> dict:
    """Search variants by gene, rsid, chromosome, clinical significance, or free text.

    At least one of q, gene, rsid, or chrom is required. Use q="__all__" to
    match the whole 1.5 billion-variant index, which is rarely useful without
    another filter alongside it and can be slow.

    Args:
        q: Free text or field-qualified Lucene, e.g. 'clinvar.rcv.conditions.name:melanoma'.
            A value with a colon, such as an HGVS id, is auto-quoted if q is
            nothing else; myvariant_describe_fields covers the syntax.
        gene: A gene symbol, e.g. "BRAF". Matched against the gene name as
            SnpEff, dbNSFP, and ClinVar each record it, which can differ for
            a read-through or overlapping gene.
        rsid: A dbSNP id, e.g. "rs334". One rsid can match more than one hit:
            a position can carry several alternate alleles.
        chrom: A chromosome, e.g. "7", "X", "MT". A "chr" prefix is stripped.
        clinical_significance: A ClinVar term, e.g. "Pathogenic",
            "Likely_benign", "Uncertain_significance". Matched as free text,
            so "Pathogenic" also matches "Pathogenic/Likely_pathogenic".
        assembly: "hg19" (default) or "hg38". Changes both which variants
            match and the coordinates on each hit.
        size: Hits to return, 0 to 1000. Use 0 for a count only.
        offset: Hits to skip. offset + size must stay at or under 10000.
        fields: Comma-separated fields to return. Empty returns chrom, vcf,
            dbsnp.rsid, gene symbols, clinical significance, and CADD score.
        sort: A field to sort on, e.g. "-cadd.phred". Empty sorts by relevance.

    Example questions:
        "Find pathogenic BRAF variants"
        "What is known about rs334?"
    """
    clauses = []
    if q.strip():
        quoted, quote_note = _quote_if_hgvs_like(q)
        clauses.append(quoted)
    else:
        quote_note = None
    if gene.strip():
        g = gene.strip()
        clauses.append(f'(snpeff.ann.genename:"{g}" OR dbnsfp.genename:"{g}" OR clinvar.gene.symbol:"{g}")')
    if rsid.strip():
        r = rsid.strip()
        if not r.lower().startswith("rs"):
            raise ToolError(f"'{rsid}' is not an rsid. An rsid starts with 'rs', e.g. rs334.")
        clauses.append(f'dbsnp.rsid:"{r}"')
    if chrom.strip():
        c = chrom.strip().removeprefix("chr").removeprefix("Chr").removeprefix("CHR")
        clauses.append(f'chrom:"{c}"')
    if clinical_significance.strip():
        clauses.append(f'clinvar.rcv.clinical_significance:"{clinical_significance.strip()}"')

    if not clauses:
        raise ToolError(
            "At least one of q, gene, rsid, or chrom is required. Use "
            'q="__all__" to match every variant.'
        )
    query = " AND ".join(clauses)

    requested = _split(fields) if fields else list(SUMMARY_FIELDS)
    _check_fields(requested)

    clamped_size, clamped_offset, paging_note = _clamp_paging(size, offset)
    resolved_assembly = _resolve_assembly(assembly)
    params = {
        "q": query,
        "assembly": resolved_assembly,
        "size": clamped_size,
        "from": clamped_offset or None,
        "fields": requested,
        "sort": sort.strip() or None,
    }
    payload = _get("/query", params)

    hits = payload.get("hits", [])
    for hit in hits:
        hit.update(_provenance(hit))

    notes = [n for n in (quote_note, paging_note) if n]
    total = payload.get("total", 0)
    result = {
        "query": query,
        "assembly": resolved_assembly or "hg19",
        "total": total,
        "hits_returned": len(hits),
        "hits": hits,
        "notes": notes,
        "api_call": {
            "url": f"{MYVARIANT_API}/query",
            "method": "GET",
            "params": _clean_params(params),
        },
    }
    # Budget trimming can shrink hits further, so the "N are shown" note is
    # built from the count that actually survives, not the count fetched.
    _budget(result, "hits", "hits_returned")
    shown = result["hits_returned"]
    if shown == 0 and total and clamped_offset >= total:
        notes.append(
            f"offset {clamped_offset} is past the end of this result set, which "
            f"holds {total} variants, so no hits came back. Lower offset."
        )
    elif total > clamped_offset + shown:
        notes.append(
            f"{total} variants match and {shown} are shown. Raise size, "
            "or page with offset, or narrow the query."
        )
    return result


# ********** get variant **********

@mcp.tool()
def myvariant_get_variant(
    variant_id: str,
    sections: str = "core",
    assembly: str = "",
) -> dict:
    """Fetch the annotation for one variant, by section.

    A full variant document can be very large: BRAF V600E is 486 KB, of
    which CIViC alone is 438 KB. So this tool returns only the sections you
    name, and never the whole document by default.

    Args:
        variant_id: A genomic HGVS id, e.g. "chr7:g.140453136A>T", from
            myvariant_search_variants or myvariant_map_ids. A transcript
            HGVS id (NM_...:c. or p.) does not resolve here; map it to a
            genomic id first with myvariant_map_ids.
        sections: Comma-separated sections to return. Available:
            "core" (chromosome, position, dbSNP id, gene, observed flag),
            "clinical" (ClinVar, COSMIC, DoCM, EMV, MutDB),
            "civic" (CIViC clinical evidence; can be large even after capping),
            "population" (gnomAD exome and genome, ExAC, ESP),
            "predictions" (dbNSFP, CADD deleteriousness scores),
            "functional" (SnpEff, Cancer Genome Interpreter),
            "other" (GRASP, GWAS Catalog, Geno2MP, Wellderly, SNPedia).
            Pass "all" for every section, which can be very large.
        assembly: "hg19" (default) or "hg38". The id must be in this build's
            coordinates.

    Example questions:
        "What does ClinVar say about chr7:g.140453136A>T?"
        "Is there CIViC evidence for this variant?"
    """
    if not variant_id.strip():
        raise ToolError("variant_id is required.")

    names = _split(sections) or ["core"]
    if "all" in names:
        chosen = sorted(VARIANT_SECTIONS)
    else:
        unknown = [n for n in names if n not in VARIANT_SECTIONS]
        if unknown:
            close = difflib.get_close_matches(unknown[0], list(VARIANT_SECTIONS), n=3)
            hint = f" Close matches: {', '.join(close)}." if close else ""
            raise ToolError(
                f"'{unknown[0]}' is not a section.{hint} "
                f"Sections: {', '.join(sorted(VARIANT_SECTIONS))}, or 'all'."
            )
        chosen = names

    requested: list[str] = []
    for name in chosen:
        for field in VARIANT_SECTIONS[name]:
            if field not in requested:
                requested.append(field)
    # Core ids travel with every response, so provenance always resolves.
    for needed in ("chrom", "vcf", "dbsnp.rsid", "clinvar.variant_id"):
        if needed not in requested:
            requested.append(needed)

    resolved_assembly = _resolve_assembly(assembly)
    params = {"fields": requested, "assembly": resolved_assembly}
    doc = _get(f"/variant/{variant_id.strip()}", params)

    notes = []
    clinvar_note = _trim_clinvar(doc)
    if clinvar_note:
        notes.append(clinvar_note)
    civic_note = _trim_civic(doc)
    if civic_note:
        notes.append(civic_note)
    if "civic" not in doc and "civic" in chosen:
        pass  # no CIViC evidence for this variant; nothing to trim or report
    if "observed" not in doc:
        notes.append(
            "This document carries no 'observed' flag, which usually means "
            "it exists only as a CADD-precomputed score for a possible "
            "substitution, not as a variant confirmed in dbSNP, ClinVar, or "
            "a population database."
        )
    missing = [
        name for name in chosen
        if name != "core" and not any(doc.get(f.split(".")[0]) for f in VARIANT_SECTIONS[name])
    ]
    if missing:
        notes.append(
            f"This variant carries no annotation for: {', '.join(missing)}. "
            "MyVariant aggregates upstream sources, and coverage is uneven, "
            "especially for CIViC and COSMIC, which cover mainly cancer "
            "variants."
        )

    result = {
        "variant_id": variant_id.strip(),
        "assembly": resolved_assembly or "hg19",
        "sections": chosen,
        "variant": doc,
        "links": _provenance(doc),
        "notes": notes,
        "api_call": {
            "url": f"{MYVARIANT_API}/variant/{variant_id.strip()}",
            "method": "GET",
            "params": _clean_params(params),
        },
    }
    oversize = len(json.dumps(result, default=str))
    if oversize > MAX_RESPONSE_CHARS:
        result["truncated"] = (
            f"The response is {oversize} characters, past the "
            f"{MAX_RESPONSE_CHARS} budget. It is returned whole because a "
            f"variant document cannot be split further safely. Ask for fewer "
            f"of {', '.join(chosen)} to shrink it."
        )
    return result


# ********** map ids **********

@mcp.tool()
def myvariant_map_ids(
    ids: list[str],
    from_type: str = "dbsnp.rsid",
    assembly: str = "",
    fields: str = "",
) -> dict:
    """Translate a list of variant identifiers into genomic ids, in one call.

    The tool for turning a variant list into ids MyVariant can look up
    directly: rsids to genomic HGVS, or a transcript-level HGVS id (which
    myvariant_get_variant cannot take directly) to one. Maps up to 1000 ids
    per call.

    Two results need reading, not just the matches. An id that matched
    nothing is listed under "not_found". An id that matched several variants
    is listed under "ambiguous": a single rsid commonly does this, because a
    position can carry more than one alternate allele and the rsid does not
    say which.

    Args:
        ids: The identifiers to map, up to 1000.
        from_type: The field the ids are in. Common values: "dbsnp.rsid"
            (default), "clinvar.rcv.accession", "clinvar.hgvs.coding",
            "clinvar.hgvs.protein", "_id" (genomic HGVS, for confirming an id
            exists rather than looking one up).
        assembly: "hg19" (default) or "hg38".
        fields: Comma-separated fields to return for each match. Empty
            returns chrom, vcf, dbsnp.rsid, gene symbols, clinical
            significance, and CADD score.

    Example questions:
        "Convert these rsids to genomic coordinates"
        "Which variant is NM_007294.4:c.5074G>A?"
    """
    notes = []
    submitted = [str(i).strip() for i in ids if str(i).strip()]
    if not submitted:
        raise ToolError("ids is required and must hold at least one identifier.")

    # A repeated id comes back once per copy, which would otherwise look like
    # an ambiguous match.
    clean_ids = list(dict.fromkeys(submitted))
    if len(clean_ids) < len(submitted):
        notes.append(
            f"{len(submitted) - len(clean_ids)} of the {len(submitted)} ids "
            "passed were repeats, and each id is mapped once."
        )

    if len(clean_ids) > MAX_BATCH_IDS:
        notes.append(
            f"{len(clean_ids)} ids were passed and the API takes {MAX_BATCH_IDS} "
            f"per call, so the first {MAX_BATCH_IDS} were mapped. Call again "
            "with the rest."
        )
        clean_ids = clean_ids[:MAX_BATCH_IDS]

    scopes = _split(from_type) or ["dbsnp.rsid"]
    _check_fields(scopes, must_be_indexed=True)
    requested = _split(fields) if fields else list(SUMMARY_FIELDS)
    _check_fields(requested)

    resolved_assembly = _resolve_assembly(assembly)
    body = {
        # q stays a JSON array: comma-joining it would split any id that
        # holds a comma.
        "q": clean_ids,
        "scopes": scopes,
        "fields": requested,
        "assembly": resolved_assembly,
    }
    rows = _post("/query", _clean_params(body) | {"q": clean_ids})
    if isinstance(rows, dict):
        rows = [rows]

    matches: dict[str, list[dict]] = {}
    not_found = []
    for row in rows:
        queried = row.get("query")
        if row.get("notfound"):
            not_found.append(queried)
            continue
        hit = {k: v for k, v in row.items() if k != "query"}
        hit.update(_provenance(hit))
        matches.setdefault(queried, []).append(hit)

    ambiguous = {k: v for k, v in matches.items() if len(v) > 1}
    unique = {k: v[0] for k, v in matches.items() if len(v) == 1}

    if not_found:
        notes.append(
            f"{len(not_found)} of {len(clean_ids)} ids matched nothing in "
            f"'{','.join(scopes)}'. A merged or retired rsid is a common "
            "cause; dbSNP tracks merges under dbsnp.dbsnp_merges.rsid."
        )
    if ambiguous:
        notes.append(
            f"{len(ambiguous)} ids matched more than one variant, most often "
            "because one rsid covers several alternate alleles at the same "
            "position. Read 'ambiguous' and pick by allele, not by rsid alone."
        )

    result = {
        "from_type": ",".join(scopes),
        "assembly": resolved_assembly or "hg19",
        "ids_submitted": len(clean_ids),
        "matched": len(unique) + len(ambiguous),
        "matches": unique,
        "ambiguous": ambiguous,
        "not_found": not_found,
        "notes": notes,
        "api_call": {
            "url": f"{MYVARIANT_API}/query",
            "method": "POST",
            "body": {**_clean_params(body), "q": f"<{len(clean_ids)} ids>"},
        },
    }
    # Trim the matches first and the ambiguous set only if that is not enough:
    # an id that matched several variants is a diagnostic the caller has to
    # act on.
    kept, total = _fit(result, result, "matches")
    if kept < total:
        result["matched"] = kept + len(result["ambiguous"])
        result["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it "
            f"carries {kept} of {total} matches. Map fewer ids per call, or "
            "ask for fewer fields."
        )
    kept_ambiguous, total_ambiguous = _fit(result, result, "ambiguous")
    if kept_ambiguous < total_ambiguous:
        result["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it "
            f"carries {kept} of {total} matches and {kept_ambiguous} of "
            f"{total_ambiguous} ambiguous ids. Map fewer ids per call, or "
            "ask for fewer fields."
        )
    return result


# ********** facet counts **********

@mcp.tool()
def myvariant_facet_counts(
    facet_by: str,
    q: str = "__all__",
    assembly: str = "",
    facet_size: int = 20,
) -> dict:
    """Count variants by the values of a field, for a query.

    Scope q narrowly: the index holds 1.5 billion variants, and a facet over
    the whole thing, or over a loosely filtered slice of it, can take tens of
    seconds. A gene or chromosome filter keeps it fast.

    Args:
        facet_by: The field to count by, e.g. "dbnsfp.genename",
            "dbnsfp.polyphen2.hdiv.pred", "cadd.phred". Must be an indexed,
            aggregatable field; chrom and clinvar.rcv.clinical_significance
            are text and cannot be faceted. Call myvariant_describe_fields to
            check.
        q: The query to count within. Defaults to "__all__", every variant;
            scope it with a gene or chrom clause for a query that returns in
            reasonable time.
        assembly: "hg19" (default) or "hg38".
        facet_size: Values to return, 1 to 1000. Default 20.

    Example questions:
        "Which genes have the most predicted-damaging missense variants?"
        "What is the PolyPhen-2 breakdown of variants in TP53?"
    """
    field = facet_by.strip()
    if not field:
        raise ToolError("facet_by is required.")
    _check_facetable(field)

    clamped_facet_size = max(1, min(facet_size, MAX_FACET_SIZE))
    resolved_assembly = _resolve_assembly(assembly)
    params = {
        "q": q.strip() or "__all__",
        "assembly": resolved_assembly,
        "facets": field,
        "facet_size": clamped_facet_size,
        "size": 0,
    }
    payload = _get("/query", params)

    facet = payload.get("facets", {}).get(field, {})
    terms = [{"value": t.get("term"), "count": t.get("count")} for t in facet.get("terms", [])]
    notes = []
    if facet.get("missing"):
        notes.append(f"{facet['missing']} matching variants carry no value for '{field}'.")
    if not terms and payload.get("total"):
        notes.append(
            f"{payload['total']} variants match the query but '{field}' "
            "produced no values. Either no matching variant carries the "
            "field, or the field sits under a nested object the facet API "
            "cannot aggregate over."
        )

    result = {
        "facet_by": field,
        "query": params["q"],
        "assembly": resolved_assembly or "hg19",
        "total_matching_variants": payload.get("total", 0),
        "values_returned": len(terms),
        "terms": terms,
        "variants_outside_returned_values": facet.get("other", 0),
        "variants_missing_field": facet.get("missing", 0),
        "notes": notes,
        "api_call": {
            "url": f"{MYVARIANT_API}/query",
            "method": "GET",
            "params": _clean_params(params),
        },
    }
    # A client-side budget trim can shrink terms further than the server's
    # own facet_size cutoff did, so this note is built from what survives.
    _budget(result, "terms", "values_returned")
    if facet.get("other"):
        notes.append(
            f"{facet['other']} matching variants fall outside the "
            f"{result['values_returned']} values shown. Raise facet_size to "
            "see more."
        )
    return result


# ********** raw query **********

@mcp.tool()
def myvariant_raw_query(
    q: str,
    fields: str = "chrom,vcf,dbsnp.rsid,clinvar.rcv.clinical_significance",
    size: int = 10,
    offset: int = 0,
    sort: str = "",
    assembly: str = "",
) -> dict:
    """Run a query against MyVariant.info with no shaping of the response.

    An escape hatch for a query the other tools cannot express, such as one
    mixing a range on cadd.phred with an existence check on civic. Prefer
    myvariant_search_variants, which validates fields, quotes an HGVS-like q,
    and adds provenance links. Field names are not validated here, and a
    colon in q (an HGVS id) is not auto-quoted here either.

    Args:
        q: The query string, in Elasticsearch query_string syntax. Use
            "__all__" to match every variant. A value with a colon must be
            quoted, e.g. '"NM_007294.4:c.5074G>A"'.
        fields: Comma-separated fields to return, or "all" for the whole
            variant document, which can be very large.
        size: Hits to return, 0 to 1000.
        offset: Hits to skip. offset + size must stay at or under 10000.
        sort: A field to sort on, e.g. "-cadd.phred". Empty sorts by relevance.
        assembly: "hg19" (default) or "hg38".

    Example questions:
        "Find variants with a CADD score over 30 but no ClinVar entry"
        "Which BRAF variants have conflicting ClinVar interpretations?"
    """
    if not q.strip():
        raise ToolError("q is required. Use '__all__' to match every variant.")

    clamped_size, clamped_offset, paging_note = _clamp_paging(size, offset)
    params = {
        "q": q.strip(),
        "fields": fields.strip() or None,
        "size": clamped_size,
        "from": clamped_offset or None,
        "sort": sort.strip() or None,
        "assembly": _resolve_assembly(assembly),
    }
    payload = _get("/query", params)

    result = {
        "response": payload,
        "notes": [n for n in (paging_note,) if n],
        "api_call": {
            "url": f"{MYVARIANT_API}/query",
            "method": "GET",
            "params": _clean_params(params),
        },
    }
    if isinstance(payload, dict):
        kept, total = _fit(result, payload, "hits")
        if kept < total:
            result["truncated"] = (
                f"The response passed {MAX_RESPONSE_CHARS} characters, so it "
                f"carries {kept} of {total} hits. Ask for fewer fields than "
                "'all', or a smaller size."
            )
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MyVariant.info MCP server")
    parser.add_argument("--stdio", action="store_true", help="run over stdio")
    parser.add_argument("--port", type=int, default=8004, help="HTTP port")
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.port = args.port
        print(f"MyVariant MCP Server starting on http://localhost:{args.port}/mcp-myvariant ...")
        mcp.run(transport="streamable-http")
