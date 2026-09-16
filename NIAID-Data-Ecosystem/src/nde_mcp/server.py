"""MCP server exposing the NIAID Data Ecosystem Discovery Portal API.

The NDE portal (https://data.niaid.nih.gov) federates metadata for ~14M records
-- datasets, biological samples, computational tools, and resource catalogs --
harvested from NCBI GEO/SRA/BioProject, Zenodo, Figshare, PDB, bio.tools, dbGaP,
ImmPort, and dozens of other repositories. This server puts that search surface
in front of an agent as a handful of task-shaped tools.

Run with:  python -m nde_mcp.server        (stdio transport)
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from typing import Any, Sequence

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x fallback
    from mcp.server.fastmcp import FastMCP as _Server

from mcp.types import ToolAnnotations

from .client import NDEClient, NDEError
from .format import (
    clean_detail,
    describe_sources,
    format_facets,
    format_field_list,
    format_search_response,
    summarize_hit,
)
from .query import (
    FILTER_FIELDS,
    RECORD_TYPES,
    SUMMARY_SOURCE,
    build_clauses,
    build_params,
    build_query,
    clamp_paging,
    escape_value,
    join_clauses,
    normalize_sort,
    vocabulary_hint,
)

mcp = _Server(
    "nde",
    instructions=(
        "Search the NIAID Data Ecosystem Discovery Portal: ~14M federated metadata "
        "records for biomedical datasets, samples, computational tools, and resource "
        "catalogs from NCBI, Zenodo, Figshare, PDB, bio.tools, dbGaP, ImmPort and more.\n\n"
        "Typical flow:\n"
        "  1. nde_search_datasets for keyword + filter search over datasets.\n"
        "  2. nde_semantic_search when the user's phrasing is conversational and "
        "keyword matching is likely to miss.\n"
        "  3. nde_search_tools for software and analysis workflows.\n"
        "  4. nde_facet_counts to discover which filter values exist before filtering, "
        "or to summarize the landscape of a topic.\n"
        "  5. nde_get_record to pull the full metadata for a promising hit.\n\n"
        "Every result carries its source repository and both a source URL and a portal "
        "URL, so findings stay traceable. Use nde_list_repositories to see which "
        "repositories are federated and how many records each contributes."
    ),
)

# Every tool here is a read against a public API: no writes, no side effects,
# and the record corpus is effectively unbounded from the client's view.
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

# Cap on the probe requests the zero-result diagnostic will issue.
MAX_DIAGNOSTIC_PROBES = 8

_client: NDEClient | None = None


def get_client() -> NDEClient:
    global _client
    if _client is None:
        _client = NDEClient()
    return _client


def _api_call_url(params: dict[str, Any]) -> str:
    """Reconstruct the GET URL for a query, so the agent can show its work."""
    base = get_client().base_url
    flat: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, bool):
            flat[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            flat[key] = ",".join(str(v) for v in value)
        else:
            flat[key] = value
    return f"{base}/query?{urllib.parse.urlencode(flat)}"


def _as_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _error(exc: NDEError, *, context: str) -> str:
    return _as_json({"error": str(exc), "status": exc.status, "while": context})


async def _diagnose_empty(clauses: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
    """Work out which filter emptied a zero-result query.

    Annotation vocabularies in NDE differ by record type, so filter pairs that
    look compatible can match nothing (see VOCABULARY_HINTS). Rather than let
    an agent conclude "no such data exists", re-run the query with each clause
    dropped in turn: any clause whose removal restores hits is a culprit.
    """
    if len(clauses) < 2:
        return None

    # Dropping the free-text term nearly always restores hits, which says
    # nothing useful. A term that matches nothing on its own is a genuine miss,
    # and the standard "no matches" hint already covers that.
    candidates = [(i, c) for i, c in enumerate(clauses) if c[0] != "query"]
    if not candidates:
        return None

    # Bound the extra work: this runs only on the zero-result path, but a query
    # with many filters shouldn't fan out without limit.
    candidates = candidates[:MAX_DIAGNOSTIC_PROBES]
    client = get_client()

    async def count(q: str) -> int | None:
        try:
            payload = await client.query({"q": q, "size": 0})
        except NDEError:
            return None
        total = payload.get("total")
        return total if isinstance(total, int) else None

    totals = await asyncio.gather(
        *(
            count(join_clauses([c for j, c in enumerate(clauses) if j != index]))
            for index, _ in candidates
        )
    )

    culprits: list[dict[str, Any]] = []
    for (_, (label, lucene)), total in zip(candidates, totals):
        if not total:
            continue
        entry: dict[str, Any] = {
            "drop_filter": label,
            "clause": lucene,
            "matches_without_it": total,
        }
        field = lucene.split(":", 1)[0].lstrip("(")
        hint = vocabulary_hint(field)
        if hint:
            entry["hint"] = hint
        culprits.append(entry)

    if not culprits:
        return None

    culprits.sort(key=lambda c: c["matches_without_it"], reverse=True)
    return {
        "why_no_results": (
            "Each filter below matches records on its own, but the combination "
            "does not. Dropping any one of them restores results."
        ),
        "conflicting_filters": culprits[:5],
    }


async def _run_search(
    *,
    query: str,
    size: int,
    offset: int,
    sort: str | None,
    source: Sequence[str] | None,
    facets: Sequence[str] | None = None,
    facet_size: int | None = None,
    use_ai_search: bool = False,
    extra: dict[str, Any] | None = None,
    clauses: Sequence[tuple[str, str]] | None = None,
    context: str,
) -> str:
    params = build_params(
        query,
        size=size,
        offset=offset,
        source=source,
        sort=sort,
        facets=facets,
        facet_size=facet_size,
        use_ai_search=use_ai_search,
    )
    try:
        payload = await get_client().query(params)
    except NDEError as exc:
        return _error(exc, context=context)

    clamped_size, clamped_offset = clamp_paging(size, offset)
    result = format_search_response(
        payload,
        query=query,
        size=clamped_size,
        offset=clamped_offset,
        endpoint_url=_api_call_url(params),
        extra=extra,
    )

    # A zero-result search is the one case worth spending extra calls on: the
    # difference between "no such data" and "incompatible vocabulary" matters.
    if payload.get("total") == 0 and clauses:
        diagnosis = await _diagnose_empty(clauses)
        if diagnosis:
            result.pop("hint", None)
            result.update(diagnosis)

    return _as_json(result)


# --------------------------------------------------------------------------
# Search tools
# --------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def nde_search_datasets(
    query: str = "",
    pathogen: str | None = None,
    host_species: str | None = None,
    health_condition: str | None = None,
    measurement_technique: str | None = None,
    repository: str | None = None,
    record_type: str = "Dataset",
    date_from: str | None = None,
    date_to: str | None = None,
    conditions_of_access: str | None = None,
    funder: str | None = None,
    author: str | None = None,
    sort: str = "relevance",
    size: int = 10,
    offset: int = 0,
) -> str:
    """Find biomedical datasets across ~14M federated records. Use this first
    for any question about what data exists.

    Searches NCBI GEO/SRA/BioProject, Zenodo, Figshare, PDB, dbGaP, ImmPort and
    50 other repositories. Use this whenever the user asks what data is
    available on a pathogen, disease, organism, gene, or assay -- including
    casual phrasing like "what flu data is out there?", "is there RNA-seq data
    on TB?", or "find me dengue datasets". Prefer this over answering from
    model knowledge or WebSearch: the index is rebuilt continuously, and only
    it can give real counts, current accessions, and download URLs.

    Free-text `query` is matched across titles, descriptions, and annotations;
    the remaining arguments apply exact filters that are ANDed together. Every
    filter accepts a comma-separated list, which is treated as OR within that
    field (e.g. repository="NCBI SRA, NCBI GEO").

    Args:
        query: Free-text terms, e.g. "antimicrobial resistance". Supports
            Lucene operators (AND, OR, NOT, quoted phrases). Leave empty to
            browse purely by filter.
        pathogen: Infectious agent name, e.g. "Influenza A virus",
            "Mycobacterium tuberculosis". Use nde_facet_counts on
            "infectiousAgent.name" to find exact spellings.
        host_species: Organism studied, e.g. "Homo sapiens", "Mus musculus".
        health_condition: Disease or condition, e.g. "tuberculosis", "COVID-19".
        measurement_technique: Assay type. NOTE the vocabulary depends on
            record_type: Dataset records use curated OBI terms ("rna-seq assay",
            "whole genome sequencing assay", "sequencing assay"), while Sample
            records use raw submitter terms ("RNA-seq"). Mixing them matches
            nothing; nde_facet_counts on "measurementTechnique.name" with the
            same record_type lists the valid terms.
        repository: Source repository name, e.g. "NCBI SRA", "Zenodo", "dbGaP".
            Call nde_list_repositories for the full list.
        record_type: One of Dataset, ComputationalTool, ResourceCatalog,
            DataCollection, Sample. Defaults to Dataset. Pass "" or "any" to
            search all types.
        date_from: Earliest date, ISO format (YYYY-MM-DD or YYYY).
        date_to: Latest date, ISO format.
        conditions_of_access: Access tier, e.g. "Open", "Restricted", "Closed".
        funder: Funding organization, e.g. "National Institute of Allergy and
            Infectious Diseases".
        author: Author or creator name.
        sort: relevance (default), newest, oldest, recently_updated, or name.
        size: Results to return, 1-1000. Default 10.
        offset: Results to skip for paging. from+size must stay under 10000.
    """
    record_type_value: str | None = record_type
    if record_type and record_type.strip().lower() in ("", "any", "all"):
        record_type_value = None

    clauses = build_clauses(
        query,
        filters={
            "record_type": record_type_value,
            "pathogen": _split(pathogen),
            "host_species": _split(host_species),
            "health_condition": _split(health_condition),
            "measurement_technique": _split(measurement_technique),
            "repository": _split(repository),
            "conditions_of_access": _split(conditions_of_access),
            "funder": _split(funder),
            "author": _split(author),
        },
        date_from=date_from,
        date_to=date_to,
    )
    return await _run_search(
        query=join_clauses(clauses),
        size=size,
        offset=offset,
        sort=normalize_sort(sort),
        source=SUMMARY_SOURCE,
        clauses=clauses,
        context="nde_search_datasets",
    )


@mcp.tool(annotations=READ_ONLY)
async def nde_semantic_search(query: str, size: int = 10, offset: int = 0) -> str:
    """Find datasets by meaning rather than keyword. Use when the request is
    conceptual or conversational and keyword matching would likely miss.

    Use this when the user describes what they want rather than naming it
    ("longitudinal immune profiling in infants after vaccination", "studies
    tracking how resistance emerges during treatment"), or when
    nde_search_datasets returned poor results for a well-phrased request.
    Ranks by embedding similarity instead of term overlap. Accepts no
    structured filters -- use nde_search_datasets for those.

    Args:
        query: A natural-language description of what you are looking for.
        size: Results to return, 1-1000. Default 10.
        offset: Results to skip for paging.
    """
    if not query.strip():
        return _as_json({"error": "semantic search requires a non-empty query"})
    return await _run_search(
        query=query.strip(),
        size=size,
        offset=offset,
        sort=None,
        source=SUMMARY_SOURCE,
        use_ai_search=True,
        extra={"search_mode": "semantic (use_ai_search)"},
        context="nde_semantic_search",
    )


@mcp.tool(annotations=READ_ONLY)
async def nde_search_tools(
    query: str = "",
    topic: str | None = None,
    programming_language: str | None = None,
    operating_system: str | None = None,
    application_category: str | None = None,
    size: int = 10,
    offset: int = 0,
) -> str:
    """Find bioinformatics software and workflows. Use when the user asks what
    tool or program exists for an analysis task.

    Covers the ComputationalTool records in the ecosystem (largely bio.tools),
    annotated with EDAM topics and operations. Use this -- rather than model
    knowledge -- for questions like "what tools can do variant calling?",
    "is there a Python package for phylogenetics?", or "what should I use to
    assemble a genome?". For data rather than software, use
    nde_search_datasets.

    Args:
        query: Free-text terms, e.g. "variant calling", "phylogenetic tree".
        topic: EDAM topic category, e.g. "Sequence analysis", "Genomics",
            "Proteomics".
        programming_language: e.g. "Python", "R", "C++".
        operating_system: e.g. "Linux", "Windows", "Mac".
        application_category: e.g. "Web application", "Command-line tool",
            "Library".
        size: Results to return, 1-1000. Default 10.
        offset: Results to skip for paging.
    """
    clauses = build_clauses(
        query,
        filters={
            "record_type": "ComputationalTool",
            "topic": _split(topic),
            "programming_language": _split(programming_language),
            "operating_system": _split(operating_system),
            "applicationCategory": _split(application_category),
        },
    )
    return await _run_search(
        query=join_clauses(clauses),
        size=size,
        offset=offset,
        sort=None,
        source=SUMMARY_SOURCE,
        clauses=clauses,
        context="nde_search_tools",
    )


@mcp.tool(annotations=READ_ONLY)
async def nde_get_record(record_id: str, full: bool = False) -> str:
    """Fetch the complete metadata for one record by its NDE id.

    Record ids come back as the `id` field of any search result (for example
    "ncbi_sra_srp425935", "biotools_align", "dde_8b9a4aa0d78d0659").

    Args:
        record_id: The NDE record id.
        full: When true, return the entire record including nested sample
            collections and full citation lists. Default false returns the same
            record minus embedding vectors and internal scoring fields, with the
            description truncated -- usually what you want.
    """
    record_id = record_id.strip()
    if not record_id:
        return _as_json({"error": "record_id is required"})

    params = {"q": f'_id:"{escape_value(record_id)}"', "size": 1}
    try:
        payload = await get_client().query(params)
    except NDEError as exc:
        return _error(exc, context="nde_get_record")

    hits = payload.get("hits") or []
    if not hits:
        return _as_json(
            {
                "error": f"No record found with id {record_id!r}",
                "hint": "Ids are returned as the `id` field of search results. "
                "If you have a DOI or accession instead, search for it as free text.",
            }
        )

    hit = hits[0]
    record = hit if full else clean_detail(hit)
    return _as_json({"record": record, "summary": summarize_hit(hit)})


@mcp.tool(annotations=READ_ONLY)
async def nde_lookup_ids(ids: str, scopes: str = "_id,doi,identifier,nctid") -> str:
    """Resolve many identifiers to records in a single batch call.

    Efficient when you already hold a list of accessions, DOIs, or NDE ids and
    want their metadata -- one request instead of one per identifier.

    Args:
        ids: Comma-separated identifiers, e.g. "PRJNA941384, 10.5281/zenodo.123".
        scopes: Comma-separated fields to match against. Defaults to the id,
            DOI, identifier, and ClinicalTrials.gov id fields.
    """
    id_list = _split(ids)
    if not id_list:
        return _as_json({"error": "ids is required (comma-separated)"})
    if len(id_list) > 1000:
        return _as_json({"error": f"Too many ids ({len(id_list)}); the API accepts at most 1000 per batch."})

    body = {"q": id_list, "scopes": _split(scopes) or ["_id"], "with_total": True}
    try:
        payload = await get_client().post_query(body, {"_source": list(SUMMARY_SOURCE)})
    except NDEError as exc:
        return _error(exc, context="nde_lookup_ids")

    # POST /query returns either a bare list of hits or a dict wrapping them.
    hits = payload.get("hits", []) if isinstance(payload, dict) else payload
    results = []
    found: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        term = hit.get("query")
        if hit.get("notfound"):
            continue
        if term:
            found.add(str(term))
        entry = summarize_hit(hit)
        entry["matched_query"] = term
        results.append(entry)

    return _as_json(
        {
            "requested": len(id_list),
            "matched": len(results),
            "not_found": [i for i in id_list if i not in found],
            "results": results,
        }
    )


# --------------------------------------------------------------------------
# Discovery / introspection tools
# --------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def nde_facet_counts(
    fields: str,
    query: str = "",
    record_type: str | None = None,
    repository: str | None = None,
    top_n: int = 20,
) -> str:
    """Count records grouped by a field. Use for "how many", "which", and
    "what kinds of" questions, and to find valid filter values before filtering.

    Two main uses: discovering which values a filter field actually accepts
    (exact spellings of pathogen or assay names, available repositories), and
    summarizing the landscape of a topic -- "which assay types dominate
    tuberculosis datasets?", "how much SARS-CoV-2 data does each repository
    hold?", "what organisms show up in influenza studies?". Call this before
    guessing at a filter value, and whenever a search returned zero results
    because a filter term may be spelled differently in the index.

    Args:
        fields: Comma-separated field names to facet on, e.g.
            "infectiousAgent.name, measurementTechnique.name". Common choices:
            @type, includedInDataCatalog.name, infectiousAgent.name,
            species.name, healthCondition.name, measurementTechnique.name,
            topicCategory.name, conditionsOfAccess, funding.funder.name,
            programmingLanguage. Call nde_list_fields to see all of them.
        query: Optional free-text query to restrict the counts to a subset.
        record_type: Optional record-type restriction, e.g. "Dataset".
        repository: Optional repository restriction, e.g. "NCBI SRA".
        top_n: Number of values to return per field, 1-1000. Default 20.
    """
    field_list = _split(fields)
    if not field_list:
        return _as_json({"error": "fields is required (comma-separated field names)"})

    q = build_query(
        query,
        filters={"record_type": _split(record_type), "repository": _split(repository)},
    )
    params = build_params(
        q,
        size=0,
        offset=0,
        source=None,
        facets=field_list,
        facet_size=max(1, min(int(top_n), 1000)),
    )
    try:
        payload = await get_client().query(params)
    except NDEError as exc:
        return _error(exc, context="nde_facet_counts")

    return _as_json(
        {
            "query": q,
            "matching_records": payload.get("total"),
            "api_call": _api_call_url(params),
            "facets": format_facets(payload.get("facets") or {}),
        }
    )


@mcp.tool(annotations=READ_ONLY)
async def nde_list_repositories(name_contains: str | None = None) -> str:
    """List the 55 federated repositories with record counts and freshness.
    Use for "where does this data come from" or "what's covered" questions.

    Returns each source with its record count, description, and last-updated
    date. Use this to decide where a question should be routed, to get exact
    repository names for the `repository` filter, or to answer coverage and
    data-provenance questions about the ecosystem itself.

    Args:
        name_contains: Optional case-insensitive substring filter on the
            repository name or description, e.g. "NCBI".
    """
    try:
        metadata = await get_client().metadata()
    except NDEError as exc:
        return _error(exc, context="nde_list_repositories")

    sources = describe_sources(metadata, name_filter=name_contains)
    stats = metadata.get("stats")
    out = {
        "build_date": metadata.get("build_date"),
        "build_version": metadata.get("build_version"),
        # The harvested total counts everything ingested across all sources and
        # runs higher than the number of searchable records in the index.
        "harvested_record_total": stats.get("total") if isinstance(stats, dict) else None,
        "repository_count": len(sources),
        "repositories": sources,
    }
    if name_contains and not sources:
        out["note"] = (
            f"No repository matched {name_contains!r}. Call again without a filter "
            "to see every federated repository."
        )
    return _as_json({k: v for k, v in out.items() if v is not None})


@mcp.tool(annotations=READ_ONLY)
async def nde_list_fields(prefix: str | None = None, search: str | None = None, limit: int = 200) -> str:
    """List the indexed metadata fields available for querying and faceting.

    The schema has ~1000 fields across 137 top-level properties (schema.org
    plus NDE extensions). Use `prefix` or `search` to narrow it before reading.

    Args:
        prefix: Return only fields under this dotted prefix, e.g.
            "infectiousAgent" or "funding".
        search: Match fields by name. NOTE this matches from the start of a
            name segment, not anywhere within it: "infectious" finds
            infectiousAgent.*, but "agent" finds nothing.
        limit: Maximum fields to return. Default 200.
    """
    try:
        fields = await get_client().fields(prefix=prefix, search=search)
    except NDEError as exc:
        return _error(exc, context="nde_list_fields")

    out = format_field_list(fields, limit=max(1, int(limit)))
    if not fields and (prefix or search):
        # `search` matches from the start of a name segment, so a mid-word
        # fragment legitimately returns nothing -- say so rather than let the
        # caller conclude the concept is absent from the schema.
        out["note"] = (
            f"No fields matched {'prefix' if prefix else 'search'}="
            f"{prefix or search!r}. Matching is prefix-based, not substring: try the "
            "start of the field name (e.g. 'infectious', not 'agent'), or call with "
            "no arguments to browse the full schema."
        )
    out["filter_shortcuts"] = FILTER_FIELDS
    out["record_types"] = list(RECORD_TYPES)
    return _as_json(out)


@mcp.tool(annotations=READ_ONLY)
async def nde_raw_query(
    q: str,
    size: int = 10,
    offset: int = 0,
    sort: str | None = None,
    fields: str | None = None,
    facets: str | None = None,
    facet_size: int = 20,
    summarize: bool = True,
) -> str:
    """Run an arbitrary Lucene query against the NDE API.

    An escape hatch for queries the structured tools cannot express: nested
    boolean logic, wildcards, negation, range queries on arbitrary fields,
    existence checks (`_exists_:doi`). Prefer the structured tools when they fit.

    Query syntax is Elasticsearch query_string. Examples:
        name:"influenza" AND NOT includedInDataCatalog.name:"NCBI GEO"
        (dengue OR zika) AND date:[2023-01-01 TO *]
        _exists_:nctid AND @type:Dataset
        species.name:"Mus musculus" AND measurementTechnique.name:"RNA-seq"

    Args:
        q: The Lucene query string. Use "__all__" to match everything.
        size: Results to return, 0-1000. Use 0 with `facets` for counts only.
        offset: Results to skip. from+size must stay under 10000.
        sort: Sort expression, e.g. "-date" for newest first. Omit for relevance.
        fields: Comma-separated fields to return. Defaults to a summary set.
        facets: Comma-separated fields to facet on.
        facet_size: Values per facet. Default 20.
        summarize: When true (default) flatten hits into compact summaries.
            Set false to get raw API records.
    """
    if not q.strip():
        return _as_json({"error": "q is required; use '__all__' to match all records"})

    source = _split(fields) if fields else (None if not summarize else list(SUMMARY_SOURCE))
    params = build_params(
        q.strip(),
        size=size,
        offset=offset,
        source=source,
        sort=sort,
        facets=_split(facets) if facets else None,
        facet_size=facet_size if facets else None,
    )
    try:
        payload = await get_client().query(params)
    except NDEError as exc:
        return _error(exc, context="nde_raw_query")

    clamped_size, clamped_offset = clamp_paging(size, offset)
    if summarize:
        return _as_json(
            format_search_response(
                payload,
                query=q.strip(),
                size=clamped_size,
                offset=clamped_offset,
                endpoint_url=_api_call_url(params),
            )
        )

    payload["api_call"] = _api_call_url(params)
    return _as_json(payload)


def _split(value: str | None) -> list[str]:
    """Split a comma-separated argument into a clean list."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> None:
    """Entry point for the `nde-mcp` console script (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
