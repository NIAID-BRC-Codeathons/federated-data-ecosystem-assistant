"""MyGene.info MCP Server

A remote MCP server exposing tools over MyGene.info, the BioThings gene
annotation service: gene search, annotation retrieval, and batch identifier
mapping across 93M genes from 53,000 species.

Run over HTTP:  python mcp_servers/mygene.py
Run over stdio: python mcp_servers/mygene.py --stdio
"""

import difflib
import json
import time
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP(
    name="MyGene MCP",
    dependencies=["mcp", "requests"],
    instructions=(
        "Query MyGene.info for gene annotation: symbols, names, genomic "
        "position, Gene Ontology terms, pathways, orthologs, and cross-references "
        "to Entrez, Ensembl, RefSeq, UniProt and HGNC"
    ),
    port=8002,
    streamable_http_path="/mcp-mygene",
)


# MyGene.info  (BioThings gene annotation service)
#
# MyGene aggregates 32 upstream sources (NCBI Entrez, Ensembl, UniProt,
# Gene Ontology, ConsensusPathDB, Alliance of Genome Resources and others)
# into one gene document per gene. The tools read the field index at runtime,
# so no field name is hard-coded here.


# ********** shared by the MyGene tools **********

MYGENE_API = "https://mygene.info/v3"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "federated-data-ecosystem-assistant "
        "(+https://github.com/NIAID-BRC-Codeathons/federated-data-ecosystem-assistant)"
    ),
}

# The field index and the species alias table change only when MyGene
# rebuilds, so refetch them after an hour.
SCHEMA_TTL_SECONDS = 3600

# Limits the API enforces. Exceeding one returns HTTP 400, so the tools clamp
# rather than let an agent discover them by trial and error.
MAX_SIZE = 1000
MAX_RESULT_WINDOW = 10000
MAX_FACET_SIZE = 1000
MAX_BATCH_IDS = 1000

# Human-facing pages, used to build provenance links.
NCBI_GENE_URL = "https://www.ncbi.nlm.nih.gov/gene/{entrezgene}"
ENSEMBL_GENE_URL = "https://ensembl.org/Gene/Summary?g={ensembl_gene}"

# Returned by every search hit, so a result is readable without a second call.
SUMMARY_FIELDS = ["symbol", "name", "taxid", "entrezgene", "ensembl.gene", "type_of_gene"]

# A gene document with every field is large: CDK2 (Entrez 1017) is 100 KB, of
# which generif alone is 65 KB. So mygene_get_gene takes sections rather than
# returning everything, and never returns a field the caller did not ask for.
GENE_SECTIONS: dict[str, list[str]] = {
    "core": [
        "symbol", "name", "taxid", "entrezgene", "ensembl.gene", "type_of_gene",
        "alias", "other_names", "summary", "map_location", "genomic_pos",
        "HGNC", "MIM", "AllianceGenome",
    ],
    "go": ["go"],
    "pathway": ["pathway"],
    "protein": ["uniprot", "pdb", "pfam", "interpro", "prosite", "ec", "pharos", "chembl"],
    "sequence": ["refseq", "accession", "exons"],
    "homologs": ["homologene", "agr"],
    "clinical": ["MIM", "umls", "pharmgkb", "exac"],
    "literature": ["generif", "wikipedia"],
}

# GeneRIF lists every curated literature annotation, often several hundred for
# a well-studied gene. Keep the newest few and say how many were dropped.
MAX_GENERIF = 25

# A response above this many characters of JSON is cut short, so one call
# cannot exhaust the agent's context.
MAX_RESPONSE_CHARS = 60000

# The note explaining a trim is added after the rows have been measured, so
# trimming leaves room for it rather than overshooting the budget by its
# length. Halving is the floor, so a small budget still trims sensibly.
TRUNCATION_RESERVE = 400

# Values of type_of_gene, from a facet over the whole index, most common first.
GENE_TYPES = [
    "protein-coding", "ncrna", "trna", "pseudo", "rrna", "snrna", "snorna",
    "biological-region", "miscrna", "other", "unknown",
]

# Elasticsearch refuses an aggregation on an analysed text field, so only a
# field of one of these types can be faceted. A "text" or "object" field fails
# with "Fielddata is disabled", which does not say what to use instead.
FACETABLE_TYPES = {"keyword", "integer", "long", "boolean", "byte", "date"}

# Fields the API adds to a response rather than reading from a gene document.
RESPONSE_ONLY_FIELDS = {"_id", "_score", "_version", "query", "notfound", "all"}

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
         "root_cause_line_00": "parse_exception: Encountered '<EOF>' ..."}
    """
    if not isinstance(payload, dict):
        return ToolError(f"MyGene.info returned HTTP {status}: {str(payload)[:300]}")
    parts = [str(payload.get("error", f"HTTP {status}"))]
    parts += [str(v) for k, v in sorted(payload.items()) if k.startswith("root_cause")]
    if "details" in payload:
        parts.append(str(payload["details"]))
    if "keyword" in payload:
        bound = payload.get("max", payload.get("min"))
        parts.append(f"parameter '{payload['keyword']}' out of range (limit {bound})")
    detail = "; ".join(p for p in parts if p)
    return ToolError(f"MyGene.info error (HTTP {status}): {detail[:500]}")


def _request(method: str, path: str, **kwargs: Any) -> Any:
    """Call the API. A MyGene error becomes a short tool error.

    The API signals failure both with an HTTP status and with a body-level
    "success": false, so both are checked.
    """
    url = f"{MYGENE_API}{path}"
    try:
        resp = requests.request(method, url, headers=HEADERS, timeout=60, **kwargs)
    except requests.Timeout as exc:
        raise ToolError(
            f"Request to {url} timed out after 60s. Narrow the query, or ask "
            "for fewer fields."
        ) from exc
    except requests.RequestException as exc:
        raise ToolError(f"Could not reach MyGene.info at {url}: {exc}") from exc

    try:
        payload = resp.json()
    except ValueError:
        raise ToolError(
            f"MyGene.info returned non-JSON content from {url} "
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

    Returns 366 fields, each with its type and whether it is indexed. A field
    that is not indexed can be returned but not searched or faceted.
    """
    cached = _schema_cache.get("fields")
    if cached and time.monotonic() - cached[0] < SCHEMA_TTL_SECONDS:
        return cached[1]
    fields = _get("/metadata/fields")
    _schema_cache["fields"] = (time.monotonic(), fields)
    return fields


def _species_aliases() -> dict[str, int]:
    """Fetch the common-name-to-taxid table MyGene accepts, with a cache."""
    cached = _schema_cache.get("taxonomy")
    if cached and time.monotonic() - cached[0] < SCHEMA_TTL_SECONDS:
        return cached[1]
    taxonomy = _get("/metadata").get("taxonomy", {})
    _schema_cache["taxonomy"] = (time.monotonic(), taxonomy)
    return taxonomy


def _unknown_field(name: str) -> ToolError:
    """Build an error naming the closest real fields to an unknown one."""
    names = list(_field_index())
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    close += [n for n in names if name.lower() in n.lower() and n not in close]
    hint = f" Close matches: {', '.join(close[:5])}." if close else ""
    return ToolError(
        f"'{name}' is not a field of MyGene.info.{hint} "
        "Call mygene_describe_fields with field_search to find field names."
    )


def _check_fields(names: list[str], *, must_be_indexed: bool = False) -> None:
    """Reject a field the index does not declare.

    MyGene ignores an unknown name in "fields" without complaint, so
    fields=symbol,nosuchfield silently returns only the symbol. Validating
    here turns a typo into an error rather than missing data.
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

    An aggregation on a text field fails deep inside Elasticsearch. Many text
    fields have a keyword sibling holding the same annotation as an id, so the
    error points there when one exists.
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
        "Call mygene_describe_fields to see field types."
    )


def _resolve_species(species: str) -> str:
    """Validate a species argument against the alias table.

    MyGene accepts a common name ("human"), a taxid ("9606"), or a
    comma-separated mix. An unmappable name returns a bare "cannot map some
    species to taxids", which does not say which one failed.
    """
    if not species.strip():
        return ""
    aliases = _species_aliases()
    resolved = []
    for token in (t.strip() for t in species.split(",")):
        if not token:
            continue
        if token.isdigit() or token == "all":
            resolved.append(token)
        elif token.lower() in aliases:
            resolved.append(token.lower())
        else:
            close = difflib.get_close_matches(token.lower(), list(aliases), n=3)
            hint = f" Close matches: {', '.join(close)}." if close else ""
            raise ToolError(
                f"'{token}' is not a species MyGene names.{hint} "
                f"Named species: {', '.join(sorted(aliases))}. "
                "Any other species takes its NCBI taxid, e.g. 246437."
            )
    return ",".join(resolved)


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


def _provenance(hit: dict) -> dict:
    """Build the outbound links for a gene, from whichever ids it carries."""
    links = {}
    entrez = hit.get("entrezgene")
    if entrez:
        links["ncbi_gene_url"] = NCBI_GENE_URL.format(entrezgene=entrez)
    ensembl = hit.get("ensembl")
    if isinstance(ensembl, dict):
        gene = ensembl.get("gene")
    elif isinstance(ensembl, list):
        gene = ensembl[0].get("gene") if ensembl else None
    else:
        gene = None
    if gene:
        links["ensembl_gene_url"] = ENSEMBL_GENE_URL.format(ensembl_gene=gene)
    if hit.get("_id"):
        links["mygene_url"] = f"{MYGENE_API}/gene/{hit['_id']}"
    return links


def _species_breakdown(hits: list[dict], species: str) -> tuple[dict | None, str | None]:
    """Count the taxids in a result set. Return the counts and a warning.

    MyGene searches all 53,939 species when no species is given, so a bare
    symbol query mixes orthologs from many organisms into one ranked list. An
    agent that reads only the top hits will not notice.
    """
    taxids: dict[int, int] = {}
    for hit in hits:
        taxid = hit.get("taxid")
        if taxid is not None:
            taxids[taxid] = taxids.get(taxid, 0) + 1
    if len(taxids) < 2:
        return (taxids or None), None
    if not species:
        return taxids, (
            f"No species was given, so this searched all species and the hits "
            f"span {len(taxids)} taxids. Pass species to scope the search, and "
            "read taxid on every hit before you compare genes."
        )
    return taxids, (
        f"The hits span {len(taxids)} taxids. Read taxid on every hit before "
        "you compare genes."
    )


def _trim_generif(doc: dict) -> str | None:
    """Cut a long generif list down. Return a note when it was cut."""
    generif = doc.get("generif")
    if not isinstance(generif, list) or len(generif) <= MAX_GENERIF:
        return None
    dropped = len(generif) - MAX_GENERIF
    doc["generif"] = generif[:MAX_GENERIF]
    return (
        f"generif held {len(generif)} entries. Kept {MAX_GENERIF} and dropped "
        f"{dropped}. Search PubMed for the full literature on this gene."
    )


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
def mygene_describe_fields(field_search: str = "") -> dict:
    """Describe the gene document fields, species names, and query syntax.

    Call this before you filter, facet, or name fields. MyGene declares 366
    fields, and an unknown name in a query returns nothing rather than an
    error, so guessing a field name produces a silently empty answer.

    Without field_search the response lists every field, which is long. Pass
    field_search to list only the fields you need.

    Args:
        field_search: Case-insensitive text a field name must contain, e.g.
            "pathway", "go", "refseq", "uniprot". Leave empty to list all 366.

    Returns:
        The matching fields with their type and whether they are indexed, the
        species names MyGene accepts, the values of type_of_gene, and the
        query syntax.

    Example questions:
        "Which fields can I search genes on?"
        "Does MyGene have pathway annotation, and under which field?"
        "Which species names does MyGene accept?"
    """
    index = _field_index()
    needle = field_search.lower()
    fields = [
        {
            "name": name,
            "type": spec.get("type"),
            "indexed": spec.get("index", True),
            "facetable": (
                spec.get("type") in FACETABLE_TYPES and spec.get("index", True)
            ),
        }
        for name, spec in sorted(index.items())
        if needle in name.lower()
    ]
    aliases = _species_aliases()
    description = {
        "field_search": field_search,
        "fields_matched": len(fields),
        "fields_total": len(index),
        "fields": fields,
        "species_names": aliases,
        "species_note": (
            "Any species outside this table takes its NCBI taxid. MyGene holds "
            f"{len(aliases)} named species out of 53,939 in total."
        ),
        "type_of_gene_values": GENE_TYPES,
        "query_syntax": {
            "free text": "A bare term searches symbols, names, aliases and accessions, e.g. 'cyclin-dependent kinase'.",
            "field": "A field name with a value, e.g. 'symbol:CDK2' or 'type_of_gene:protein-coding'.",
            "wildcard": "A trailing star, e.g. 'symbol:CDK*'.",
            "range": "Square brackets on a numeric field, e.g. 'taxid:[9606 TO 10116]'.",
            "boolean": "AND, OR and NOT between clauses, with parentheses to group.",
            "existence": "'_exists_:pathway' for genes that carry a field, '_missing_:pathway' for those that do not.",
        },
        "notes": [
            "A field that is not indexed can be returned in fields but not "
            "searched or faceted.",
            "Only a field marked facetable can be counted with "
            "mygene_facet_counts. A text field cannot be, so an annotation "
            "often has to be counted by its id rather than its term, e.g. "
            "go.MF.id rather than go.MF.term.",
            "A gene without an Entrez id keeps its Ensembl id in _id, so _id is "
            "not always numeric. 21.6M of the 93.8M genes are Ensembl-only.",
        ],
    }
    _budget(description, "fields")
    return description


# ********** search genes **********

@mcp.tool()
def mygene_search_genes(
    q: str,
    species: str = "",
    type_of_gene: str = "",
    size: int = 10,
    offset: int = 0,
    fields: str = "",
    sort: str = "",
) -> dict:
    """Search genes by symbol, name, alias, accession, or free text.

    Leaving species empty searches all 53,939 species, so a bare symbol such
    as "CDK2" returns orthologs from many organisms mixed into one ranked
    list. The response reports the taxid breakdown either way, but pass
    species when the question is about one organism.

    Args:
        q: The query. A bare term searches symbols, names, aliases and
            accessions ("CDK2", "cyclin-dependent kinase"). Field syntax also
            works ("symbol:CDK2 AND type_of_gene:protein-coding"). Use
            "__all__" to match every gene.
        species: Comma-separated common names or NCBI taxids, e.g. "human",
            "human,mouse", "9606", "246437". Empty searches all species.
            Call mygene_describe_fields for the named species.
        type_of_gene: Restrict to one gene type, e.g. "protein-coding",
            "ncrna", "pseudo". Empty keeps every type.
        size: Hits to return, 0 to 1000. Use 0 for a count only.
        offset: Hits to skip. offset + size must stay at or under 10000.
        fields: Comma-separated fields to return. Empty returns a summary set
            of symbol, name, taxid, entrezgene, ensembl.gene and type_of_gene.
        sort: A field to sort on, e.g. "symbol" or "-taxid" for descending.
            Empty sorts by relevance.

    Returns:
        The hits with their ids and outbound links to NCBI Gene and Ensembl,
        the total number of matches, the taxid breakdown, and the API call.

    Example questions:
        "Find the human gene CDK2"
        "Which mouse genes are named interleukin?"
        "How many protein-coding genes does Mycobacterium tuberculosis have?"
    """
    if not q.strip():
        raise ToolError("q is required. Use '__all__' to match every gene.")

    requested = _split(fields) if fields else list(SUMMARY_FIELDS)
    _check_fields(requested)
    # Provenance needs the ids even when the caller did not ask for them.
    for needed in ("entrezgene", "ensembl.gene", "taxid"):
        if needed not in requested:
            requested.append(needed)

    query = q.strip()
    if type_of_gene.strip():
        if type_of_gene.strip() not in GENE_TYPES:
            raise ToolError(
                f"'{type_of_gene}' is not a gene type. "
                f"Gene types: {', '.join(GENE_TYPES)}."
            )
        query = f"({query}) AND type_of_gene:{type_of_gene.strip()}"

    clamped_size, clamped_offset, paging_note = _clamp_paging(size, offset)
    resolved_species = _resolve_species(species)
    params = {
        "q": query,
        "species": resolved_species,
        "size": clamped_size,
        "from": clamped_offset or None,
        "fields": requested,
        "sort": sort.strip() or None,
    }
    payload = _get("/query", params)

    hits = payload.get("hits", [])
    for hit in hits:
        hit.update(_provenance(hit))
    taxids, species_note = _species_breakdown(hits, resolved_species)

    notes = [n for n in (paging_note, species_note) if n]
    total = payload.get("total", 0)
    result = {
        "query": query,
        "species": resolved_species or "all species",
        "total": total,
        "hits_returned": len(hits),
        "hits": hits,
        "taxid_counts": taxids,
        "notes": notes,
        "api_call": {
            "url": f"{MYGENE_API}/query",
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
            f"holds {total} genes, so no hits came back. Lower offset."
        )
    elif total > clamped_offset + shown:
        notes.append(
            f"{total} genes match and {shown} are shown. Raise size, or "
            "page with offset, or narrow the query."
        )
    return result


# ********** get gene **********

@mcp.tool()
def mygene_get_gene(
    gene_id: str,
    sections: str = "core",
) -> dict:
    """Fetch the annotation for one gene, by section.

    A full gene document is large: human CDK2 is 100 KB, of which the
    literature section alone is 65 KB. So this tool returns only the sections
    you name, and never the whole document by default.

    Args:
        gene_id: An Entrez id ("1017"), an Ensembl gene id ("ENSG00000123374"),
            or any other id MyGene indexes. Use mygene_map_ids to turn a symbol
            into an id first, because a symbol alone is ambiguous across
            species.
        sections: Comma-separated sections to return. Available:
            "core" (symbol, name, ids, position, summary, aliases),
            "go" (Gene Ontology terms by category),
            "pathway" (KEGG, Reactome, WikiPathways and others),
            "protein" (UniProt, PDB, Pfam, InterPro, EC),
            "sequence" (RefSeq, GenBank accessions, exons),
            "homologs" (HomoloGene and Alliance of Genome Resources orthologs),
            "clinical" (OMIM, UMLS, PharmGKB, ExAC),
            "literature" (GeneRIF, Wikipedia).
            Pass "all" for every section, which can be very large.

    Returns:
        The requested sections, the outbound links, and the API call.

    Example questions:
        "What is the function of human CDK2?"
        "Which pathways is Entrez gene 1017 in?"
        "What are the mouse orthologs of BRCA1?"
    """
    if not gene_id.strip():
        raise ToolError("gene_id is required.")

    names = _split(sections) or ["core"]
    if "all" in names:
        chosen = sorted(GENE_SECTIONS)
    else:
        unknown = [n for n in names if n not in GENE_SECTIONS]
        if unknown:
            close = difflib.get_close_matches(unknown[0], list(GENE_SECTIONS), n=3)
            hint = f" Close matches: {', '.join(close)}." if close else ""
            raise ToolError(
                f"'{unknown[0]}' is not a section.{hint} "
                f"Sections: {', '.join(sorted(GENE_SECTIONS))}, or 'all'."
            )
        chosen = names

    requested: list[str] = []
    for name in chosen:
        for field in GENE_SECTIONS[name]:
            if field not in requested:
                requested.append(field)
    # Core ids travel with every response, so provenance always resolves.
    for needed in ("symbol", "taxid", "entrezgene", "ensembl.gene"):
        if needed not in requested:
            requested.append(needed)

    params = {"fields": requested}
    doc = _get(f"/gene/{gene_id.strip()}", params)

    notes = []
    generif_note = _trim_generif(doc)
    if generif_note:
        notes.append(generif_note)
    if "literature" in chosen or "all" in names:
        notes.append(
            "GeneRIF is curated literature annotation, not a complete "
            "bibliography for the gene."
        )
    missing = [
        name for name in chosen
        if not any(doc.get(f.split(".")[0]) for f in GENE_SECTIONS[name])
    ]
    if missing:
        notes.append(
            f"This gene carries no annotation for: {', '.join(missing)}. "
            "MyGene aggregates upstream sources, and coverage is uneven across "
            "species and gene types."
        )

    result = {
        "gene_id": gene_id.strip(),
        "sections": chosen,
        "gene": doc,
        "links": _provenance(doc),
        "notes": notes,
        "api_call": {
            "url": f"{MYGENE_API}/gene/{gene_id.strip()}",
            "method": "GET",
            "params": _clean_params(params),
        },
    }
    oversize = len(json.dumps(result, default=str))
    if oversize > MAX_RESPONSE_CHARS:
        result["truncated"] = (
            f"The response is {oversize} characters, past the "
            f"{MAX_RESPONSE_CHARS} budget. It is returned whole because a gene "
            f"document cannot be split safely. Ask for fewer of "
            f"{', '.join(chosen)} to shrink it."
        )
    return result


# ********** map ids **********

@mcp.tool()
def mygene_map_ids(
    ids: list[str],
    from_type: str = "symbol",
    species: str = "human",
    fields: str = "",
) -> dict:
    """Translate a list of gene identifiers into other identifiers, in one call.

    This is the tool for turning a gene list into ids: symbols to Entrez ids,
    RefSeq accessions to symbols, Ensembl ids to UniProt, and so on. It maps
    up to 1000 ids per call.

    Two results need reading, not just the matches. An id that matched nothing
    is listed under "not_found". An id that matched several genes is listed
    under "ambiguous", with every match. Leaving species empty makes ambiguity
    the normal case, because one symbol matches its orthologs in every species,
    so species defaults to human here.

    Args:
        ids: The identifiers to map, up to 1000.
        from_type: The field the ids are in. Common values: "symbol",
            "entrezgene", "ensembl.gene", "refseq", "uniprot", "alias",
            "name", "HGNC". Comma-separated values search several fields, e.g.
            "symbol,alias" to fall back to aliases for a retired symbol.
        species: Comma-separated common names or NCBI taxids. Defaults to
            "human". Pass "all" to search every species, which will make most
            symbols ambiguous.
        fields: Comma-separated fields to return for each match. Empty returns
            symbol, name, taxid, entrezgene, ensembl.gene and type_of_gene.

    Returns:
        The matches keyed by the id you passed, the ids that matched nothing,
        the ids that matched several genes, and the API call.

    Example questions:
        "Convert these 200 gene symbols to Entrez ids"
        "Which gene is NM_001798?"
        "Map these Ensembl ids to UniProt accessions"
    """
    notes = []
    submitted = [str(i).strip() for i in ids if str(i).strip()]
    if not submitted:
        raise ToolError("ids is required and must hold at least one identifier.")

    # A repeated id comes back once per copy, which would otherwise look like
    # an ambiguous match. Dedupe while keeping the caller's order.
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

    scopes = _split(from_type) or ["symbol"]
    _check_fields(scopes, must_be_indexed=True)
    requested = _split(fields) if fields else list(SUMMARY_FIELDS)
    _check_fields(requested)
    for needed in ("entrezgene", "ensembl.gene", "taxid"):
        if needed not in requested:
            requested.append(needed)

    resolved_species = _resolve_species(species)
    body = {
        "q": clean_ids,
        "scopes": scopes,
        "fields": requested,
        "species": resolved_species,
    }
    # q stays a JSON array: comma-joining it would split any id that holds a
    # comma, which a gene name can.
    rows = _post("/query", {**_clean_params(body), "q": clean_ids})
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
            f"'{','.join(scopes)}' for {resolved_species or 'all species'}. A "
            "retired or aliased symbol often resolves with from_type='symbol,alias', "
            "and a wrong species scope is the other common cause."
        )
    if ambiguous:
        notes.append(
            f"{len(ambiguous)} ids matched more than one gene. Read 'ambiguous' "
            "and pick by taxid or type_of_gene. Do not assume the first match."
        )

    result = {
        "from_type": ",".join(scopes),
        "species": resolved_species or "all species",
        "ids_submitted": len(clean_ids),
        "matched": len(unique) + len(ambiguous),
        "matches": unique,
        "ambiguous": ambiguous,
        "not_found": not_found,
        "notes": notes,
        "api_call": {
            "url": f"{MYGENE_API}/query",
            "method": "POST",
            "body": {**_clean_params(body), "q": f"<{len(clean_ids)} ids>"},
        },
    }
    # Trim the matches first and the ambiguous set only if that is not enough:
    # an id that matched several genes is a diagnostic the caller has to act on.
    kept, total = _fit(result, result, "matches")
    if kept < total:
        result["matched"] = kept + len(result["ambiguous"])
        result["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it carries "
            f"{kept} of {total} matches. Map fewer ids per call, or ask for "
            "fewer fields."
        )
    kept_ambiguous, total_ambiguous = _fit(result, result, "ambiguous")
    if kept_ambiguous < total_ambiguous:
        result["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it carries "
            f"{kept} of {total} matches and {kept_ambiguous} of "
            f"{total_ambiguous} ambiguous ids. Map fewer ids per call, or ask "
            "for fewer fields."
        )
    return result


# ********** facet counts **********

@mcp.tool()
def mygene_facet_counts(
    facet_by: str,
    q: str = "__all__",
    species: str = "",
    facet_size: int = 20,
) -> dict:
    """Count genes by the values of a field, for a query.

    Use this to answer "how many" and "which" questions, and to find the
    values a field actually holds before you filter on it. Faceting on taxid
    shows which species a query reaches; faceting on type_of_gene shows the
    mix of coding and non-coding genes.

    Args:
        facet_by: The field to count by, e.g. "taxid", "type_of_gene",
            "go.MF.id", "go.BP.evidence". Only a keyword, integer, long,
            boolean, byte or date field can be faceted, never a text field, so
            "go.MF.id" works where "go.MF.term" does not. Call
            mygene_describe_fields to see field types.
        q: The query to count within. Defaults to "__all__", every gene.
        species: Comma-separated common names or NCBI taxids. Empty counts
            across all species.
        facet_size: Values to return, 1 to 1000. Default 20.

    Returns:
        The values with their counts, the number of matching genes outside the
        returned values, and the API call.

    Example questions:
        "Which species have a gene called CDK2?"
        "How many human genes are non-coding?"
        "Which molecular functions are most annotated in human genes?"
    """
    field = facet_by.strip()
    if not field:
        raise ToolError("facet_by is required.")
    _check_facetable(field)

    clamped_facet_size = max(1, min(facet_size, MAX_FACET_SIZE))
    resolved_species = _resolve_species(species)
    params = {
        "q": q.strip() or "__all__",
        "species": resolved_species,
        "facets": field,
        "facet_size": clamped_facet_size,
        "size": 0,
    }
    payload = _get("/query", params)

    facet = payload.get("facets", {}).get(field, {})
    terms = [{"value": t.get("term"), "count": t.get("count")} for t in facet.get("terms", [])]
    notes = []
    if facet.get("missing"):
        notes.append(f"{facet['missing']} matching genes carry no value for '{field}'.")
    if not terms and payload.get("total"):
        notes.append(
            f"{payload['total']} genes match the query but '{field}' produced no "
            "values. Either no matching gene carries the field, or the field "
            "sits under a nested object, which the facet API cannot aggregate "
            "over. Check with mygene_search_genes, asking for the field."
        )
    if field == "taxid":
        notes.append(
            "A taxid is an NCBI taxonomy id. 9606 is human, 10090 mouse, "
            "10116 rat."
        )

    result = {
        "facet_by": field,
        "query": params["q"],
        "species": resolved_species or "all species",
        "total_matching_genes": payload.get("total", 0),
        "values_returned": len(terms),
        "terms": terms,
        "genes_outside_returned_values": facet.get("other", 0),
        "genes_missing_field": facet.get("missing", 0),
        "notes": notes,
        "api_call": {
            "url": f"{MYGENE_API}/query",
            "method": "GET",
            "params": _clean_params(params),
        },
    }
    # A client-side budget trim can shrink terms further than the server's
    # own facet_size cutoff did, so this note is built from what survives.
    _budget(result, "terms", "values_returned")
    if facet.get("other"):
        notes.append(
            f"{facet['other']} matching genes fall outside the "
            f"{result['values_returned']} values shown. Raise facet_size to "
            "see more."
        )
    return result


# ********** raw query **********

@mcp.tool()
def mygene_raw_query(
    q: str,
    fields: str = "symbol,name,taxid,entrezgene",
    size: int = 10,
    offset: int = 0,
    sort: str = "",
    species: str = "",
) -> dict:
    """Run a query against MyGene.info with no shaping of the response.

    An escape hatch for a query the other tools cannot express, such as one
    mixing existence checks with ranges and nested boolean groups. Prefer
    mygene_search_genes, which validates fields, reports the species spread,
    and adds provenance links. Field names are not validated here.

    Args:
        q: The query string, in Elasticsearch query_string syntax. Use
            "__all__" to match every gene.
        fields: Comma-separated fields to return, or "all" for the whole gene
            document, which is large.
        size: Hits to return, 0 to 1000.
        offset: Hits to skip. offset + size must stay at or under 10000.
        sort: A field to sort on, e.g. "-taxid". Empty sorts by relevance.
        species: Comma-separated common names or NCBI taxids. Empty searches
            all species.

    Returns:
        The API response as it came back, plus the API call.

    Example questions:
        "Find human genes with a KEGG pathway but no GO annotation"
        "Which genes on chromosome 7 lack an Ensembl id?"
    """
    if not q.strip():
        raise ToolError("q is required. Use '__all__' to match every gene.")

    clamped_size, clamped_offset, paging_note = _clamp_paging(size, offset)
    params = {
        "q": q.strip(),
        "fields": fields.strip() or None,
        "size": clamped_size,
        "from": clamped_offset or None,
        "sort": sort.strip() or None,
        "species": _resolve_species(species),
    }
    payload = _get("/query", params)

    result = {
        "response": payload,
        "notes": [n for n in (paging_note,) if n],
        "api_call": {
            "url": f"{MYGENE_API}/query",
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

    parser = argparse.ArgumentParser(description="MyGene.info MCP server")
    parser.add_argument("--stdio", action="store_true", help="run over stdio")
    parser.add_argument("--port", type=int, default=8002, help="HTTP port")
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.port = args.port
        print(f"MyGene MCP Server starting on http://localhost:{args.port}/mcp-mygene ...")
        mcp.run(transport="streamable-http")
