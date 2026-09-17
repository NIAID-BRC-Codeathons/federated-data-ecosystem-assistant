"""Uniprot MCP Server

A remote MCP server exposing tools over UniProt: 
protein sequences, function, disease annotation
"""

import json
import re

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP(
    name="Uniprot MCP",
    dependencies=["mcp", "requests"],
    instructions=(
        "Query the UniProt database for protein information"
    ),
    streamable_http_path="/",
    # stateless_http=True,
)

UNIPROT_API  = "https://rest.uniprot.org"
HEADERS = {"Accept": "application/json"}

MAX_ACCESSIONS = 25
MAX_RESPONSE_CHARS = 60000

# The official UniProt accession pattern, plus an optional isoform suffix.
# uniprot_get_protein_info builds one query from every accession it receives,
# and UniProt rejects the whole query when one accession is malformed. So check
# each accession here and report it, rather than lose the call to a single typo.
ACCESSION_RE = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]"
    r"|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
    r"(?:-[0-9]+)?$"
)

# Every tool below names the fields it needs. UniProt returns the whole entry
# otherwise: 875 KB for TP53, against 4.6 KB for the summary fields.
SUMMARY_FIELDS = (
    "accession,protein_name,gene_names,organism_name,length,reviewed,cc_function"
)
ENTRY_FIELDS = (
    SUMMARY_FIELDS + ",cc_disease,cc_subcellular_location,cc_ptm,go"
)


def _comment_texts(entry: dict, comment_type: str) -> list[str]:
    """Collect every text that one comment type carries.

    An entry can hold several blocks of one type, one per chain. The SARS-CoV-2
    replicase (P0DTD1) holds 16 function blocks. Two thirds of the reviewed
    viral entries hold more than one, so reading only the first loses most of
    the annotation.
    """
    return [
        text.get("value", "")
        for comment in entry.get("comments", [])
        if comment.get("commentType") == comment_type
        for text in comment.get("texts", [])
    ]


# UNIPROT TOOLS

@mcp.tool()
def uniprot_search(
    query: str,
    organism: str = "Homo sapiens",
    reviewed_only: bool = True,
    max_results: int = 5,
) -> dict:
    """Search UniProt for proteins matching a query (gene name, function, keyword…).

    Args:
        query: Free-text or field query. Examples:
            "TP53", "kinase AND cancer", "insulin receptor"
        organism: Organism name filter (default "Homo sapiens").
            Use "" to search all organisms.
        reviewed_only: If True, restrict to Swiss-Prot reviewed entries.
        max_results: Number of results to return (max 25).

    Returns:
        List of protein entries with accession, gene, protein name, organism,
        length (aa), and Swiss-Prot review status.

    Example questions:
        "Find all reviewed human kinases involved in DNA repair"
        "Search for mouse insulin proteins in UniProt"
    """
    max_results = min(max_results, 25)
    full_query = query
    if organism:
        full_query += f" AND organism_name:{organism}"
    if reviewed_only:
        full_query += " AND reviewed:true"

    params = {
        "query": full_query,
        "format": "json",
        "size": str(max_results),
        "fields": "accession,gene_names,protein_name,organism_name,length,reviewed",
    }
    resp = requests.get(f"{UNIPROT_API}/uniprotkb/search", params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for e in data.get("results", []):
        gene = e.get("genes", [{}])
        gene_name = gene[0].get("geneName", {}).get("value", "N/A") if gene else "N/A"
        results.append({
            "accession": e["primaryAccession"],
            "gene": gene_name,
            "protein_name": e.get("proteinDescription", {})
                .get("recommendedName", {})
                .get("fullName", {})
                .get("value", "N/A"),
            "organism": e.get("organism", {}).get("scientificName", "N/A"),
            "length_aa": e.get("sequence", {}).get("length"),
            "reviewed": e.get("entryType") == "UniProtKB reviewed (Swiss-Prot)",
        })
    return {
        "query": full_query,
        "total_found": data.get("totalResults", len(results)),
        "results": results,
    }


@mcp.tool()
def uniprot_get_entry(accession: str) -> dict:
    """Fetch the curated annotation for one protein by its accession.

    This tool covers one protein per call, and it costs about twice what
    uniprot_get_protein_info costs. Call it when you need the diseases, the
    subcellular locations, the PTMs, or the GO terms. Call
    uniprot_get_protein_info instead when the name, the organism, and the
    function answer the question, or when you hold several accessions.

    Args:
        accession: UniProt accession (e.g. "P04637" for human TP53,
            "P01308" for human insulin, "P38398" for BRCA1).

    Returns:
        Dict with the accession, the gene, the protein name, the organism, the
        length, the review status, the function texts, the subcellular
        locations, the diseases, the PTM texts, and up to 10 GO terms.

    Example questions:
        "What diseases is BRCA1 (P38398) involved in?"
        "Where is human insulin localized in the cell?"
    """
    resp = requests.get(
        f"{UNIPROT_API}/uniprotkb/{accession}",
        params={"format": "json", "fields": ENTRY_FIELDS},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    e = resp.json()
    comments = e.get("comments", [])
    # Diseases
    diseases = []
    for c in comments:
        if c.get("commentType") == "DISEASE":
            d = c.get("disease", {})
            diseases.append({
                "name": d.get("diseaseName"),
                "id": d.get("diseaseId"),
                "description": d.get("description"),
            })

    # Subcellular locations
    locations = []
    for c in comments:
        if c.get("commentType") == "SUBCELLULAR LOCATION":
            for loc in c.get("subcellularLocations", []):
                loc_val = loc.get("location", {}).get("value")
                if loc_val:
                    locations.append(loc_val)

    # GO terms (top 10)
    go_terms = []
    for ref in e.get("uniProtKBCrossReferences", []):
        if ref.get("database") == "GO":
            props = {p["key"]: p["value"] for p in ref.get("properties", [])}
            go_terms.append({
                "id": ref.get("id"),
                "term": props.get("GoTerm"),
                "evidence": props.get("GoEvidenceType"),
            })

    gene = e.get("genes", [{}])
    gene_name = gene[0].get("geneName", {}).get("value", "N/A") if gene else "N/A"
    return {
        "accession": e["primaryAccession"],
        "gene": gene_name,
        "protein_name": (
            e.get("proteinDescription", {})
             .get("recommendedName", {})
             .get("fullName", {})
             .get("value", "N/A")
        ),
        "organism": e.get("organism", {}).get("scientificName"),
        "length_aa": e.get("sequence", {}).get("length"),
        "reviewed": e.get("entryType") == "UniProtKB reviewed (Swiss-Prot)",
        "function": _comment_texts(e, "FUNCTION"),
        "subcellular_locations": list(set(locations)),
        "diseases": diseases,
        "ptm_processing": _comment_texts(e, "PTM"),
        "go_terms": go_terms[:10],
    }


def _clean_accession(value: str) -> str:
    """Normalize an accession. The caller matches it against ACCESSION_RE."""
    return value.strip().upper()


def _summarize(entry: dict, include_sequence: bool) -> dict:
    """Reduce a UniProt entry to the summary fields."""
    genes = entry.get("genes") or [{}]
    functions = _comment_texts(entry, "FUNCTION")
    sequence = entry.get("sequence", {})
    summary = {
        "accession": entry.get("primaryAccession"),
        "gene": genes[0].get("geneName", {}).get("value", "N/A"),
        "protein_name": (
            entry.get("proteinDescription", {})
                 .get("recommendedName", {})
                 .get("fullName", {})
                 .get("value", "N/A")
        ),
        "organism": entry.get("organism", {}).get("scientificName", "N/A"),
        "length_aa": sequence.get("length"),
        "reviewed": entry.get("entryType") == "UniProtKB reviewed (Swiss-Prot)",
        "function": functions,
    }
    if include_sequence:
        summary["sequence"] = sequence.get("value", "")
    return summary


def _fetch_one(accession: str, fields: str) -> dict | None:
    """Fetch one entry by accession. Returns None when UniProt holds no entry."""
    resp = requests.get(
        f"{UNIPROT_API}/uniprotkb/{accession}",
        params={"format": "json", "fields": fields},
        headers=HEADERS,
        timeout=20,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _fetch_many(accessions: list[str], fields: str) -> dict[str, dict]:
    """Fetch canonical entries with one search request, indexed by accession."""
    resp = requests.get(
        f"{UNIPROT_API}/uniprotkb/search",
        params={
            "query": " OR ".join(f"accession:{a}" for a in accessions),
            "format": "json",
            "fields": fields,
            "size": str(len(accessions)),
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return {e.get("primaryAccession"): e for e in resp.json().get("results", [])}


@mcp.tool()
def uniprot_get_protein_info(
    accessions: list[str],
    include_sequence: bool = False,
) -> dict:
    """Fetch the name, the organism, and the function of up to 25 proteins.

    This tool returns a summary, and it takes one accession or many. It sends
    one request to UniProt for the whole list, so prefer one call with many
    accessions over many calls with one. The response is about half the size of
    the uniprot_get_entry response.

    Use this tool when you need the identity and the function of a protein. Use
    uniprot_get_entry when you need diseases, subcellular locations, PTMs, or
    GO terms, and accept that it covers one protein per call.

    Args:
        accessions: UniProt accessions, at most 25. Pass a list even for one
            protein. The tool drops a duplicate and keeps the first copy.
            An isoform accession such as "P04637-2" works, and costs one extra
            request, because UniProt indexes no isoform in its search.
        include_sequence: Add the amino acid sequence to every result.
            The default is False. Sequences dominate the response size, so
            leave this off unless you need the residues. Titin (Q8WZ42) alone
            adds 34,350 characters.

    Returns:
        Dict with "results" in the order you asked for. Each result holds the
        accession, the gene, the protein name, the organism, the length, the
        review status, and the function texts. Reports "invalid" for an
        accession with the wrong shape, and "not_found" for one that UniProt
        does not hold. Reports "truncated" when the response hits the size
        budget, and "note" when the list was cut to 25.

    Example questions:
        "What does P04637 do?"
        "Summarize P04637, P38398, and P01308"
    """
    if not accessions:
        raise ToolError(
            "Pass at least one accession. To find one from a gene name, call "
            "uniprot_search first."
        )

    # Keep the caller's order, drop duplicates, then split off the malformed.
    # UniProt rejects a whole search query when one accession is malformed, so
    # a typo must never reach the request.
    seen: dict[str, None] = {}
    invalid: list[str] = []
    for raw in accessions:
        cleaned = _clean_accession(raw)
        if not ACCESSION_RE.match(cleaned):
            if cleaned not in invalid:
                invalid.append(cleaned)
            continue
        seen.setdefault(cleaned, None)

    wanted = list(seen)
    note = None
    if len(wanted) > MAX_ACCESSIONS:
        note = (
            f"The list held {len(wanted)} accessions, so it was cut to the first "
            f"{MAX_ACCESSIONS}. Call the tool again for the rest."
        )
        wanted = wanted[:MAX_ACCESSIONS]

    payload: dict = {"requested": len(wanted), "results": []}
    if wanted:
        fields = SUMMARY_FIELDS + (",sequence" if include_sequence else "")
        # The search endpoint carries the canonical accessions in one request.
        # It indexes no isoform, so each isoform needs the entry endpoint.
        canonical = [a for a in wanted if "-" not in a]
        isoforms = [a for a in wanted if "-" in a]
        found: dict[str, dict] = {}
        if canonical:
            found.update(_fetch_many(canonical, fields))
        for isoform in isoforms:
            entry = _fetch_one(isoform, fields)
            if entry is not None:
                found[isoform] = entry
        # UniProt returns the matches in its own order, so rebuild the order
        # the caller asked for.
        payload["results"] = [
            _summarize(found[a], include_sequence) for a in wanted if a in found
        ]
        not_found = [a for a in wanted if a not in found]
        if not_found:
            payload["not_found"] = not_found

    if invalid:
        payload["invalid"] = invalid
    if note:
        payload["note"] = note

    # Drop results off the end until the whole payload fits the budget, so the
    # caller never receives more characters than the budget allows.
    total = len(payload["results"])
    kept = total
    while kept > 1 and len(json.dumps(payload, default=str)) > MAX_RESPONSE_CHARS:
        kept -= max(1, kept // 10)
        payload["results"] = payload["results"][:kept]
    if kept < total:
        payload["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it carries "
            f"{kept} of {total} results. Ask for fewer accessions, or set "
            f"include_sequence to False."
        )
    payload["returned"] = len(payload["results"])
    return payload


@mcp.tool()
def uniprot_get_sequence(accession: str) -> dict:
    """Fetch the raw amino acid sequence for a UniProt accession in FASTA format.

    Args:
        accession: UniProt accession (e.g. "P04637", "P01308").

    Returns:
        Dict with FASTA header, full amino acid sequence string, and length.

    Example questions:
        "Give me the amino acid sequence of human TP53"
        "What is the sequence of insulin (P01308)?"
    """
    resp = requests.get(
        f"{UNIPROT_API}/uniprotkb/{accession}.fasta",
        timeout=20,
    )
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    sequence = "".join(lines[1:])
    return {
        "accession": accession,
        "fasta_header": lines[0],
        "sequence": sequence,
        "length_aa": len(sequence),
    }

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.port = args.port
        print(f"Uniprot MCP Server starting on http://localhost:{args.port}/mcp ...")
        mcp.run(transport="streamable-http")
