"""PubMed MCP Server

A remote MCP server exposing tools over PubMed, via NCBI's E-utilities:
literature search, article detail, batch summaries, and related-article
lookups (similar, citing, references, reviews) across 41M biomedical
citations.

Run over HTTP:  python mcp_servers/pubmed.py
Run over stdio: python mcp_servers/pubmed.py --stdio
"""

import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Literal

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP(
    name="PubMed MCP",
    dependencies=["mcp", "requests"],
    instructions=(
        "Search PubMed literature and fetch article detail, batch summaries, "
        "and related-article links (similar, citing, references, reviews) "
        "through NCBI's E-utilities"
    ),
    port=8006,
    streamable_http_path="/mcp-pubmed",
)


# PubMed  (NCBI E-utilities: esearch, esummary, efetch, elink, einfo)
#
# E-utilities is not a BioThings API: no field index to introspect for
# validity, and search returns a bare id list that a second call has to
# enrich. The tools here hide that two-step shape behind one search call.


# ********** shared by the PubMed tools **********

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI asks every automated client to identify itself with tool and email,
# and rewards it with a place to be contacted before being blocked instead of
# during. NCBI_EMAIL is optional; leaving it unset still works.
TOOL_NAME = "federated-data-ecosystem-assistant"
CONTACT_EMAIL = os.environ.get("NCBI_EMAIL", "")

# An API key raises the rate limit from 3 to 10 requests/second. Get one at
# https://www.ncbi.nlm.nih.gov/account/settings/ and set NCBI_API_KEY.
API_KEY = os.environ.get("NCBI_API_KEY", "")

HEADERS = {
    "Accept": "application/json",
    "User-Agent": f"{TOOL_NAME} (+https://github.com/NIAID-BRC-Codeathons/federated-data-ecosystem-assistant)",
}

# NCBI enforces this server-side: a burst of 4 requests in under a second
# reliably drew an HTTP 429 in testing, with no key. 0.4s keeps 3 requests to
# a second with margin; an API key allows 10/s. The throttle alone was not
# enough in testing -- a 429 still came through once, likely from rate-limit
# state NCBI keeps a little longer than one second -- so _request also
# retries a 429 with backoff rather than treating it as terminal.
_MIN_INTERVAL = 0.11 if API_KEY else 0.4
_rate_lock = threading.Lock()
_last_call = 0.0
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5

# The server accepts a query for more than this many records, but silently
# caps what it returns at 9999 regardless of the retmax asked for -- verified
# live: retmax=100000 came back with retmax echoed as "9999". retstart has
# its own, lower ceiling: 9999 is rejected outright ("'retstart' cannot be
# larger than 9998"), confirmed live, while 9998 succeeds. A result set past
# that needs NCBI's history server (WebEnv/QueryKey), which these tools do
# not implement; narrow the query instead.
MAX_RESULT_WINDOW = 9999
MAX_RETSTART = 9998

# A per-call cap well under the server's own ceiling, so one search or batch
# call cannot return more records than an agent can usefully read. NCBI's own
# guidance for a UID list without the history server is to keep it under a
# few hundred.
MAX_SEARCH_SIZE = 200
MAX_BATCH_IDS = 200

# A response above this many characters of JSON is cut short, so one call
# cannot exhaust the agent's context.
MAX_RESPONSE_CHARS = 60000

# The note explaining a trim is added after the rows have been measured, so
# trimming leaves room for it rather than overshooting the budget by its
# length. Halving is the floor, so a small budget still trims sensibly.
TRUNCATION_RESERVE = 400

# Sort values esearch actually accepts; a fourth guess ("date") returned an
# error body with no esearchresult key at all. Blank uses the server default,
# which returned the same top hit as "most_recent" in testing.
SORT_VALUES = {"", "relevance", "pub_date", "most_recent"}

# elink linknames for one PMID's relationships to other PubMed records.
# pubmed_pubmed_five and pubmed_pubmed_reviews_five are NCBI's own curated
# top-5, cheaper to read than the full pubmed_pubmed set (126 links on a
# routine article in testing).
RELATIONSHIP_LINKNAMES = {
    "similar": "pubmed_pubmed_five",
    "citing": "pubmed_pubmed_citedin",
    "references": "pubmed_pubmed_refs",
    "reviews": "pubmed_pubmed_reviews_five",
}

PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
DOI_URL = "https://doi.org/{doi}"
PMC_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"

_field_cache: dict[str, Any] = {}


def _throttle() -> None:
    """Block until enough time has passed since the last call to stay under
    NCBI's rate limit. Shared across every tool, so a search followed by a
    detail fetch does not double up on the limit.
    """
    global _last_call
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _clean_params(params: dict) -> dict:
    """Drop empty values, and add the identifying params NCBI asks for."""
    out = {k: v for k, v in params.items() if v not in (None, "", [])}
    out["tool"] = TOOL_NAME
    if CONTACT_EMAIL:
        out["email"] = CONTACT_EMAIL
    if API_KEY:
        out["api_key"] = API_KEY
    return out


def _request(endpoint: str, params: dict, *, retmode: str = "json") -> Any:
    """Call one E-utilities endpoint. Rate-limited and error-normalized.

    Returns parsed JSON for retmode="json", or the raw XML text for
    retmode="xml". The pre-emptive throttle does not fully prevent a 429 by
    itself -- verified live, one still came through under it -- so a 429
    retries with backoff instead of failing the call outright.
    """
    url = f"{EUTILS_BASE}/{endpoint}.fcgi"
    last_error = None
    for attempt in range(_MAX_RETRIES):
        _throttle()
        try:
            resp = requests.get(
                url, params=_clean_params({**params, "retmode": retmode}),
                headers=HEADERS, timeout=30,
            )
        except requests.Timeout as exc:
            raise ToolError(f"Request to {endpoint} timed out after 30s.") from exc
        except requests.RequestException as exc:
            raise ToolError(f"Could not reach PubMed at {url}: {exc}") from exc

        if resp.status_code == 429:
            last_error = resp
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
            continue
        if resp.status_code >= 400:
            raise ToolError(f"PubMed returned HTTP {resp.status_code} from {endpoint}: {resp.text[:300]}")

        if retmode == "xml":
            return resp.text
        try:
            # strict=False tolerates a literal control character inside a
            # string value, which NCBI's own error bodies sometimes contain
            # unescaped -- resp.json() rejects that outright.
            return json.loads(resp.text, strict=False)
        except ValueError:
            raise ToolError(
                f"PubMed returned non-JSON content from {endpoint}: {resp.text[:300]}"
            ) from None

    raise ToolError(
        f"PubMed rate-limited this request (HTTP 429) {_MAX_RETRIES} times in a "
        "row, even after backoff. Wait a moment before retrying, or set "
        "NCBI_API_KEY for a higher limit."
    )


def _esearch(term: str, **params: Any) -> dict:
    payload = _request("esearch", {"db": "pubmed", "term": term, **params})
    result = payload.get("esearchresult")
    # A backend rejection nests its ERROR inside esearchresult, not at the
    # top level of the response -- verified live via a retstart past the
    # ceiling. Checked at the top level, this branch never fired, and the
    # ERROR body's own idlist-shaped absence would have read as a plain
    # zero-result search instead of a rejected query.
    if result is None or "ERROR" in result:
        raise ToolError(f"PubMed rejected the search: {result.get('ERROR') if result else payload}")
    return result


def _esummary(pmids: list[str]) -> dict[str, dict]:
    """Fetch lightweight metadata for a batch of PMIDs.

    A PMID with no record comes back with an "error" key rather than raising,
    so the caller reads that per id instead of getting an exception.
    """
    if not pmids:
        return {}
    payload = _request("esummary", {"db": "pubmed", "id": ",".join(pmids)})
    result = payload.get("result", {})
    return {uid: result[uid] for uid in result.get("uids", [])}


def _efetch_xml(pmids: list[str]) -> list[ET.Element]:
    """Fetch full records as XML. Returns the <PubmedArticle> elements found.

    An id efetch cannot resolve does not raise or come back empty in an
    obvious way -- it returns a near-blank text stub with no error, verified
    live against a made-up PMID. Parsed as XML, that id is simply missing
    from the elements returned, which the caller checks for explicitly.
    """
    if not pmids:
        return []
    xml_text = _request("efetch", {"db": "pubmed", "id": ",".join(pmids), "rettype": "xml"}, retmode="xml")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ToolError(f"PubMed returned unparseable XML: {exc}") from exc
    return root.findall(".//PubmedArticle")


def _text(el: ET.Element | None, path: str) -> str | None:
    found = el.find(path) if el is not None else None
    return found.text if found is not None and found.text else None


def _article_ids(article: ET.Element) -> dict[str, str]:
    """Read the article's own ids (pubmed, doi, pmc) from PubmedData.

    Scoped to PubmedData/ArticleIdList specifically: a broader search anywhere
    under the article also matches the ArticleId entries inside its
    ReferenceList, one set per cited reference, which is not this article's
    own identifiers.
    """
    ids = {}
    for id_el in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        id_type = id_el.attrib.get("IdType")
        if id_type and id_el.text:
            ids[id_type] = id_el.text
    return ids


def _links(pmid: str, ids: dict[str, str]) -> dict[str, str]:
    links = {"pubmed_url": PUBMED_URL.format(pmid=pmid)}
    if ids.get("doi"):
        links["doi_url"] = DOI_URL.format(doi=ids["doi"])
    if ids.get("pmc"):
        links["pmc_url"] = PMC_URL.format(pmcid=ids["pmc"])
    return links


def _parse_core(article: ET.Element, pmid: str) -> dict:
    citation = article.find("MedlineCitation")
    ids = _article_ids(article)
    authors = [
        " ".join(filter(None, (_text(a, "ForeName"), _text(a, "LastName")))) or _text(a, "CollectiveName")
        for a in citation.findall(".//AuthorList/Author")
    ]
    return {
        "pmid": pmid,
        "title": _text(citation, ".//ArticleTitle"),
        "journal": _text(citation, ".//Journal/ISOAbbreviation") or _text(citation, ".//Journal/Title"),
        "pub_date": _pub_date(citation),
        "authors": [a for a in authors if a],
        "doi": ids.get("doi"),
        "pmcid": ids.get("pmc"),
        # "Publisher" or "In-Process" means MEDLINE has not yet assigned MeSH
        # terms to this record; verified live on a same-week article, which
        # carried zero MeSH headings. A mesh_term filter will miss it.
        "indexing_status": citation.attrib.get("Status"),
        "links": _links(pmid, ids),
    }


def _pub_date(citation: ET.Element) -> str | None:
    date_el = citation.find(".//Article/Journal/JournalIssue/PubDate")
    if date_el is None:
        return None
    parts = [_text(date_el, tag) for tag in ("Year", "Month", "Day")]
    return " ".join(p for p in parts if p) or _text(date_el, "MedlineDate")


def _parse_abstract(article: ET.Element) -> dict:
    sections = []
    for at in article.findall(".//Abstract/AbstractText"):
        sections.append({"label": at.attrib.get("Label"), "text": at.text or ""})
    return {
        "abstract": " ".join(f"{s['label']}: {s['text']}" if s["label"] else s["text"] for s in sections) or None,
        "abstract_sections": sections if any(s["label"] for s in sections) else None,
    }


def _parse_mesh(article: ET.Element) -> dict:
    headings = []
    for mh in article.findall(".//MeshHeadingList/MeshHeading"):
        descriptor = mh.find("DescriptorName")
        if descriptor is None:
            continue
        headings.append({
            "term": descriptor.text,
            "major_topic": descriptor.attrib.get("MajorTopicYN") == "Y",
            "qualifiers": [q.text for q in mh.findall("QualifierName") if q.text],
        })
    return {
        "mesh_terms": headings,
        "publication_types": [
            pt.text for pt in article.findall(".//PublicationTypeList/PublicationType") if pt.text
        ],
    }


def _fit(root: dict, container: dict, rows_key: str) -> tuple[int, int]:
    """Trim container[rows_key] until the JSON of root fits the budget.

    Works on a list of rows or a mapping of them, and measures the whole root
    payload rather than the rows alone, so the reported size is the size the
    caller receives. Returns how many rows were kept and how many there were.
    """
    rows = container.get(rows_key)
    if not isinstance(rows, (list, dict)):
        return 0, 0
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
    """Hold a response inside the budget by dropping rows off the end."""
    kept, total = _fit(payload, payload, rows_key)
    if kept < total:
        payload["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it carries "
            f"{kept} of {total} rows. Ask for fewer, or narrow the query."
        )
        if count_key:
            payload[count_key] = kept


def _clamp_size(size: int, ceiling: int) -> tuple[int, str | None]:
    clamped = max(0, min(size, ceiling))
    note = f"size was cut from {size} to {ceiling}." if size > ceiling else None
    return clamped, note


# ********** describe search **********

@mcp.tool()
def pubmed_describe_search(field_search: str = "") -> dict:
    """Describe the PubMed search field tags, id formats, and rate limit.

    Call this before writing a field-qualified query. PubMed has no field
    validation of its own: an unrecognized tag such as [Author] misspelled as
    [Authr] is read as free text and silently changes what matches, rather
    than erroring.

    Args:
        field_search: Case-insensitive text a field tag or its name must
            contain, e.g. "mesh", "author", "date". Leave empty to list all
            48 tags PubMed defines.
    """
    cached = _field_cache.get("fields")
    if cached is None:
        payload = _request("einfo", {"db": "pubmed"})
        cached = payload["einforesult"]["dbinfo"][0]["fieldlist"]
        _field_cache["fields"] = cached
    needle = field_search.lower()
    fields = [
        {"tag": f["name"], "name": f["fullname"], "description": f["description"]}
        for f in cached
        if needle in f["name"].lower() or needle in f["fullname"].lower()
    ]
    description = {
        "field_search": field_search,
        "fields_matched": len(fields),
        "fields_total": len(cached),
        "fields": fields,
        "query_syntax": {
            "field tag": 'A value followed by a bracketed tag, e.g. \'BRAF[Title/Abstract]\', \'Smith J[Author]\'.',
            "mesh": 'A controlled vocabulary term, e.g. \'"Melanoma"[MeSH Terms]\'. Use mesh_term on '
                    "pubmed_search_articles rather than writing this by hand.",
            "boolean": "AND, OR and NOT between clauses, with parentheses to group.",
            "date range": "Not a bracketed tag: pass date_from/date_to on pubmed_search_articles, "
                          "which map to esearch's own mindate/maxdate.",
        },
        "id_formats": {
            "pmid": "The PubMed id, e.g. 33668216. Used by every tool here.",
            "doi": "Not a PubMed id. Resolve a DOI to a PMID with "
                   'pubmed_search_articles(q=\'10.3390/v13030356[LID]\') first.',
        },
        "notes": [
            "An unrecognized field tag is not rejected -- it is read as a "
            "free-text term instead, so a typo changes the query rather "
            "than erroring. Check a tag against 'fields' here before "
            "trusting a zero-result search.",
            "Very recently published articles carry no MeSH terms yet: "
            'MEDLINE indexing lags publication, and a record\'s '
            'indexing_status of "Publisher" or "In-Process" (as opposed to '
            '"MEDLINE") means mesh_term will not match it even though the '
            "article exists.",
            f"PubMed itself caps how many hits one search returns at "
            f"{MAX_RESULT_WINDOW}, no matter how high size is set; these "
            f"tools cap further at {MAX_SEARCH_SIZE} per call. Narrow the "
            "query rather than trying to page through more.",
            f"Requests are throttled to stay under NCBI's rate limit "
            f"({'10' if API_KEY else '3'} requests/second, since "
            f"{'an' if API_KEY else 'no'} NCBI_API_KEY is set), shared "
            "across every tool in this server.",
        ],
    }
    return description


# ********** search articles **********

@mcp.tool()
def pubmed_search_articles(
    q: str = "",
    author: str = "",
    journal: str = "",
    mesh_term: str = "",
    publication_type: str = "",
    date_from: str = "",
    date_to: str = "",
    size: int = 10,
    offset: int = 0,
    sort: Literal["", "relevance", "pub_date", "most_recent"] = "",
) -> dict:
    """Search PubMed and return titles, authors, journal, and links for each hit.

    At least one of q, author, journal, or mesh_term is required -- unlike a
    BioThings search, there is no sentinel that matches the whole 41M-record
    index, since that is never a sensible target here.

    Args:
        q: Free text, or a field-qualified clause, e.g.
            '"cyclin-dependent kinase" AND cancer', 'BRAF[Title/Abstract]'.
        author: An author name, e.g. "Smith J". Matched as free text against
            the author field, not required to be exact.
        journal: A journal name or its standard abbreviation, e.g. "Nature",
            "N Engl J Med".
        mesh_term: A MeSH heading, e.g. "Melanoma", "Drug Resistance,
            Neoplasm". Misses very recent articles; see pubmed_describe_search.
        publication_type: e.g. "Review", "Randomized Controlled Trial",
            "Meta-Analysis", "Systematic Review", "Case Reports". Matched as
            free text, not validated against a fixed list.
        date_from: Earliest publication date, "YYYY", "YYYY/MM", or
            "YYYY/MM/DD".
        date_to: Latest publication date, same formats. Both are needed for
            a bounded range; either alone is open-ended on the other side.
        size: Hits to return, 0 to 200.
        offset: Hits to skip, for paging. offset + size must stay at or
            under 9999, PubMed's own ceiling for a plain search.
        sort: "" (server default, newest-first in testing), "relevance",
            "pub_date", or "most_recent".

    Example questions:
        "Find reviews on BRAF inhibitor resistance in melanoma"
        "What has Jennifer Doudna published on CRISPR since 2023?"
    """
    clauses = [q.strip()] if q.strip() else []
    if author.strip():
        clauses.append(f'{author.strip()}[Author]')
    if journal.strip():
        clauses.append(f'"{journal.strip()}"[Journal]')
    if mesh_term.strip():
        clauses.append(f'"{mesh_term.strip()}"[MeSH Terms]')
    if publication_type.strip():
        clauses.append(f'"{publication_type.strip()}"[Publication Type]')
    if not clauses:
        raise ToolError("At least one of q, author, journal, or mesh_term is required.")
    term = " AND ".join(clauses)

    if sort not in SORT_VALUES:
        raise ToolError(f"'{sort}' is not a sort value. Use one of: {', '.join(sorted(SORT_VALUES - {''}))}, or empty.")

    clamped_size, size_note = _clamp_size(size, MAX_SEARCH_SIZE)
    clamped_offset = max(0, min(offset, MAX_RETSTART))
    window_note = None
    if clamped_offset + clamped_size > MAX_RESULT_WINDOW:
        clamped_size = max(0, MAX_RESULT_WINDOW - clamped_offset)
        window_note = f"offset + size must stay at or under {MAX_RESULT_WINDOW}; size was cut to {clamped_size}."

    params: dict[str, Any] = {
        "retmax": clamped_size, "retstart": clamped_offset, "sort": sort or None,
    }
    if date_from or date_to:
        params.update(datetype="pdat", mindate=date_from or "1800", maxdate=date_to or "3000")
    search = _esearch(term, **params)

    ids = search.get("idlist", [])
    summaries = _esummary(ids)
    hits = []
    for pmid in ids:
        summary = summaries.get(pmid, {})
        if "error" in summary:
            continue
        article_ids = {a["idtype"]: a["value"] for a in summary.get("articleids", [])}
        hits.append({
            "pmid": pmid,
            "title": summary.get("title"),
            "journal": summary.get("source"),
            "pub_date": summary.get("pubdate"),
            "authors": [a["name"] for a in summary.get("authors", [])],
            "doi": article_ids.get("doi"),
            "pmcid": article_ids.get("pmc"),
            "links": _links(pmid, {"doi": article_ids.get("doi"), "pmc": article_ids.get("pmc")}),
        })

    total = int(search.get("count", 0))
    warnings = search.get("warninglist", {}).get("outputmessages", [])
    notes = [n for n in (size_note, window_note) if n]
    if warnings:
        # Confirmed live to cover more than one cause: an unbalanced
        # parenthesis in the term produces one of these, and so does
        # requesting a page past what retstart allows -- so this states what
        # PubMed said rather than guessing which one applies.
        notes.append(f"PubMed adjusted or ignored part of this request: {warnings}")

    result = {
        "term": term,
        "query_translation": search.get("querytranslation"),
        "total": total,
        "hits_returned": len(hits),
        "hits": hits,
        "notes": notes,
        "api_call": {
            "url": f"{EUTILS_BASE}/esearch.fcgi",
            "params": _clean_params({"db": "pubmed", "term": term, **params}),
        },
    }
    _budget(result, "hits", "hits_returned")
    shown = result["hits_returned"]
    if shown == 0 and total and clamped_offset >= total:
        notes.append(f"offset {clamped_offset} is past the end of this result set, which holds {total} articles.")
    elif total > clamped_offset + shown:
        notes.append(f"{total} articles match and {shown} are shown. Raise size, page with offset, or narrow the query.")
    return result


# ********** get article **********

@mcp.tool()
def pubmed_get_article(pmid: str, sections: str = "core,abstract") -> dict:
    """Fetch the full detail for one article, by section.

    Args:
        pmid: A PubMed id, e.g. "33668216", from pubmed_search_articles or
            pubmed_get_summaries.
        sections: Comma-separated sections to return. Available: "core"
            (title, journal, authors, dates, ids, links, indexing status),
            "abstract" (the abstract text, with structured labels when the
            article uses them, e.g. Background/Methods/Results/Conclusions),
            "mesh" (MeSH headings with major-topic flags, and publication
            types). Defaults to core and abstract together, since the
            abstract is almost always why an agent fetches one article.

    Example questions:
        "What does this paper's abstract say about the study's methods?"
        "Is this a randomized controlled trial or a review?"
    """
    pmid = pmid.strip()
    if not pmid:
        raise ToolError("pmid is required.")

    chosen = [s.strip() for s in sections.split(",") if s.strip()] or ["core", "abstract"]
    unknown = [s for s in chosen if s not in ("core", "abstract", "mesh")]
    if unknown:
        raise ToolError(f"'{unknown[0]}' is not a section. Sections: core, abstract, mesh.")

    articles = _efetch_xml([pmid])
    if not articles:
        # Verified live: efetch on an id it cannot resolve returns a
        # near-blank stub with HTTP 200, not an error, so this is the only
        # place that failure surfaces.
        raise ToolError(f"No PubMed record found for pmid '{pmid}'. Check the id with pubmed_get_summaries first.")
    article = articles[0]

    result: dict[str, Any] = {"pmid": pmid}
    if "core" in chosen:
        result.update(_parse_core(article, pmid))
    if "abstract" in chosen:
        result.update(_parse_abstract(article))
    if "mesh" in chosen:
        result.update(_parse_mesh(article))

    notes = []
    if "abstract" in chosen and not result.get("abstract"):
        notes.append("This record carries no abstract; some article types (letters, corrections) have none.")
    if "mesh" in chosen and not result.get("mesh_terms"):
        notes.append(
            "This record carries no MeSH terms. If its indexing_status is "
            '"Publisher" or "In-Process" rather than "MEDLINE", that is '
            "because it has not been indexed yet, not because none apply."
        )
    result["sections"] = chosen
    result["notes"] = notes
    return result


# ********** get summaries **********

@mcp.tool()
def pubmed_get_summaries(pmids: list[str]) -> dict:
    """Fetch lightweight metadata for a batch of PMIDs in one call.

    For turning a list of PubMed ids into readable titles, authors, and
    dates without the cost of a full record per id. Maps up to 200 ids per
    call.

    Args:
        pmids: The PubMed ids to summarize, up to 200.

    Example questions:
        "Give me the titles and journals for these 40 PMIDs"
        "Which of these PMIDs are actually valid?"
    """
    notes = []
    submitted = [str(p).strip() for p in pmids if str(p).strip()]
    if not submitted:
        raise ToolError("pmids is required and must hold at least one identifier.")

    clean_ids = list(dict.fromkeys(submitted))
    if len(clean_ids) < len(submitted):
        notes.append(f"{len(submitted) - len(clean_ids)} of the {len(submitted)} ids passed were repeats.")

    if len(clean_ids) > MAX_BATCH_IDS:
        notes.append(f"{len(clean_ids)} ids were passed and {MAX_BATCH_IDS} are mapped per call. Call again with the rest.")
        clean_ids = clean_ids[:MAX_BATCH_IDS]

    summaries = _esummary(clean_ids)
    found: dict[str, dict] = {}
    not_found = []
    for pmid in clean_ids:
        summary = summaries.get(pmid)
        if summary is None or "error" in summary:
            not_found.append(pmid)
            continue
        article_ids = {a["idtype"]: a["value"] for a in summary.get("articleids", [])}
        found[pmid] = {
            "title": summary.get("title"),
            "journal": summary.get("source"),
            "pub_date": summary.get("pubdate"),
            "authors": [a["name"] for a in summary.get("authors", [])],
            "doi": article_ids.get("doi"),
            "pmcid": article_ids.get("pmc"),
            "links": _links(pmid, {"doi": article_ids.get("doi"), "pmc": article_ids.get("pmc")}),
        }

    if not_found:
        notes.append(f"{len(not_found)} of {len(clean_ids)} ids did not resolve to a PubMed record.")

    result = {
        "ids_submitted": len(clean_ids),
        "found": len(found),
        "summaries": found,
        "not_found": not_found,
        "notes": notes,
        "api_call": {
            "url": f"{EUTILS_BASE}/esummary.fcgi",
            "params": {"db": "pubmed", "id": f"<{len(clean_ids)} ids>"},
        },
    }
    kept, total = _fit(result, result, "summaries")
    if kept < total:
        result["found"] = kept
        result["truncated"] = (
            f"The response passed {MAX_RESPONSE_CHARS} characters, so it carries "
            f"{kept} of {total} summaries. Summarize fewer ids per call."
        )
    return result


# ********** related articles **********

@mcp.tool()
def pubmed_related_articles(
    pmid: str,
    relationship: Literal["similar", "citing", "references", "reviews"] = "similar",
    size: int = 20,
) -> dict:
    """List PMIDs related to one article, by relationship.

    Args:
        pmid: A PubMed id, e.g. "33668216".
        relationship: "similar" -- PubMed's own topic-similarity ranking, its
            curated top 5. "citing" -- articles that cite this one, drawn
            only from PMC's own full-text citation index, not a
            comprehensive citation count like Scopus or Web of Science.
            "references" -- this article's own bibliography, where indexed.
            "reviews" -- review articles judged similar to this one, top 5.
        size: Results to return, 0 to 200. "similar" and "reviews" already
            return at most 5 by design.

    Example questions:
        "What cites this paper, at least within PMC?"
        "What did this study cite?"
    """
    pmid = pmid.strip()
    if not pmid:
        raise ToolError("pmid is required.")
    linkname = RELATIONSHIP_LINKNAMES[relationship]

    payload = _request("elink", {"dbfrom": "pubmed", "db": "pubmed", "id": pmid, "linkname": linkname})
    linksets = payload.get("linksets", [])
    if not linksets or "linksetdbs" not in linksets[0]:
        return {
            "pmid": pmid, "relationship": relationship, "related_pmids": [],
            "notes": [f"No '{relationship}' links found for this article. It may not exist, or genuinely have none."],
        }
    links = linksets[0]["linksetdbs"][0].get("links", [])

    clamped_size, size_note = _clamp_size(size, MAX_SEARCH_SIZE)
    truncated = len(links) > clamped_size
    links = links[:clamped_size]
    summaries = _esummary(links)

    hits = []
    for related_pmid in links:
        summary = summaries.get(related_pmid, {})
        hits.append({
            "pmid": related_pmid,
            "title": summary.get("title"),
            "journal": summary.get("source"),
            "pub_date": summary.get("pubdate"),
            "links": {"pubmed_url": PUBMED_URL.format(pmid=related_pmid)},
        })

    notes = [n for n in (size_note,) if n]
    if relationship == "citing":
        notes.append(
            "Coverage is limited to citations found in PMC's own full-text "
            "index, so this count understates true citation impact."
        )
    if truncated:
        notes.append(f"More than {clamped_size} '{relationship}' links exist; raise size to see more.")

    result = {
        "pmid": pmid,
        "relationship": relationship,
        "related_count": len(hits),
        "related_articles": hits,
        "notes": notes,
        "api_call": {
            "url": f"{EUTILS_BASE}/elink.fcgi",
            "params": {"dbfrom": "pubmed", "db": "pubmed", "id": pmid, "linkname": linkname},
        },
    }
    _budget(result, "related_articles", "related_count")
    return result


# ********** raw search **********

@mcp.tool()
def pubmed_raw_search(
    term: str,
    retmax: int = 20,
    retstart: int = 0,
    sort: str = "",
    datetype: str = "",
    mindate: str = "",
    maxdate: str = "",
) -> dict:
    """Run an esearch query against PubMed with no shaping of the response.

    An escape hatch for a query the structured tool cannot express, such as
    one combining several field tags pubmed_search_articles has no argument
    for. Prefer pubmed_search_articles, which enriches hits with titles and
    authors; this returns bare PMIDs.

    Args:
        term: The full esearch term, in PubMed query syntax, e.g.
            '(cancer[MeSH Terms]) AND 2024[PDAT]'.
        retmax: PMIDs to return, 0 to 9999 (PubMed's own ceiling).
        retstart: PMIDs to skip, for paging.
        sort: "", "relevance", "pub_date", or "most_recent".
        datetype: "pdat" (publication date) or "edat" (entry date), paired
            with mindate/maxdate.
        mindate: Earliest date, "YYYY", "YYYY/MM", or "YYYY/MM/DD".
        maxdate: Latest date, same formats.

    Example questions:
        "Search PubMed with this exact query string I already wrote"
    """
    if not term.strip():
        raise ToolError("term is required.")
    if sort not in SORT_VALUES:
        raise ToolError(f"'{sort}' is not a sort value. Use one of: {', '.join(sorted(SORT_VALUES - {''}))}, or empty.")

    clamped_retmax = max(0, min(retmax, MAX_RESULT_WINDOW))
    clamped_retstart = max(0, min(retstart, MAX_RETSTART))
    params = {
        "retmax": clamped_retmax, "retstart": clamped_retstart, "sort": sort or None,
        "datetype": datetype or None, "mindate": mindate or None, "maxdate": maxdate or None,
    }
    search = _esearch(term.strip(), **params)

    result = {
        "response": search,
        "notes": [],
        "api_call": {
            "url": f"{EUTILS_BASE}/esearch.fcgi",
            "params": _clean_params({"db": "pubmed", "term": term.strip(), **params}),
        },
    }
    if retmax > MAX_RESULT_WINDOW:
        result["notes"].append(f"retmax was cut from {retmax} to {MAX_RESULT_WINDOW}, PubMed's own ceiling.")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PubMed MCP server")
    parser.add_argument("--stdio", action="store_true", help="run over stdio")
    parser.add_argument("--port", type=int, default=8006, help="HTTP port")
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.port = args.port
        print(f"PubMed MCP Server starting on http://localhost:{args.port}/mcp-pubmed ...")
        mcp.run(transport="streamable-http")
