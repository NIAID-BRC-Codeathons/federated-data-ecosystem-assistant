"""NCBI E-utilities as MCP tools.

    THE INVARIANT: every tool function below is `async def`.

    FastMCP dispatches a synchronous tool onto a worker thread and an async one
    onto the event loop --- measured, not assumed; see tests/test_invariants.py,
    which fails if that ever stops being true. NCBI's rate limit is per source
    IP, so pacing has to be
    process-global --- and a sync tool would run alongside the asyncio limiter
    without ever entering it, quietly doubling the request rate. At a codeathon
    the whole room shares one IP, so the cost of that mistake is everyone's
    tools breaking at once, not just these.

    One event loop, one anyio.Lock, one httpx.AsyncClient. Do not add a sync
    tool here, and do not "simplify" a tool that does not appear to await
    anything --- it still has to await the limiter.

Built on `mcp.server.fastmcp`, the FastMCP vendored in the official `mcp` SDK
(1.x), which is what the rest of this repo uses --- see `mygene.py` and
`uniprot.py`. Note that jlowin's standalone `fastmcp` package is a *different
project* with the same class name; it requires `mcp>=2.0`, where this module is
a tombstone that raises on import. The two cannot share an environment, so this
server stays on the SDK's copy and installs from the root `pyproject.toml`.

The sibling servers use `requests` and sync tools. This one does not, and the
reason is the invariant above: NCBI rate-limits per IP, so the pacing has to be
shared across every in-flight call.

Parameter descriptions use Annotated[..., Field(description=...)], which is what
reaches the agent: a description written only in the docstring documents the
tool but not its arguments. These strings are the agent's entire basis for
choosing between seventeen similar-sounding tools; they are interface, not
comments.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from . import coverage, databases, parsing, pathogens
from .databases import DATABASES, RUNINFO_OPEN_ACCESS_EMPTY
from .envelope import NCBIError, envelope, provenance
from .eutils import MAX_ESUMMARY_UIDS, EUtilsClient, Timer, TransportError
from .pathogens import PathogensClient

# Each MCP server in this repo owns a port and a namespaced path, so they can
# all run at once behind one host: 8001 pdn, 8002 mygene, 8003 uniprot,
# 8004 myvariant.
DEFAULT_PORT = 8005
HTTP_PATH = "/mcp-ncbi"

server = FastMCP(
    name="NCBI MCP",
    dependencies=["mcp", "httpx"],
    port=DEFAULT_PORT,
    streamable_http_path=HTTP_PATH,
    instructions=(
        "Two NCBI services.\n\n"
        "E-utilities: sequencing runs (SRA), samples (BioSample), projects "
        "(BioProject), organisms (Taxonomy), genes, genome assemblies, and "
        "sequences.\n\n"
        "Pathogen Detection (`ncbi_pathogen_*`): a curated, deduplicated index "
        "of bacterial isolates with computed antimicrobial-resistance "
        "genotypes. **Prefer it for any question about resistance genes or "
        "resistant strains.** It answers those from curated AMR calls, whereas "
        "the E-utilities databases can only match free-text sample "
        "descriptions --- searching BioSample for 'MRSA' finds the isolates "
        "whose submitter happened to use that word, which undercounts "
        "severalfold. Use ncbi_pathogen_organisms and ncbi_pathogen_amr_genes "
        "to get exact filter values first: values are matched literally, and "
        "an unrecognized one returns 0 rather than an error, so a typo is "
        "indistinguishable from a real negative.\n\n"
        "Every result carries a `provenance` block. Read it before answering, "
        "and cite it:\n"
        "- `sources` --- which databases produced the data, and each one's "
        "share of the result.\n"
        "- `request_cost` --- how many NCBI requests this answer spent.\n"
        "- `coverage` --- the result count against a denominator, with `basis` "
        "naming what the denominator counts. Report the percentage and the "
        "basis together; a count without its denominator reads as a "
        "population. A `shortfall` means NCBI silently dropped records you "
        "asked for.\n"
        "- `query_translation` --- the query NCBI actually ran, which is often "
        "not the one that was sent. Report it when it differs from the "
        "request. Absent on Pathogen Detection record fetches, which cannot "
        "return it and rows together.\n"
        "- `notes` --- caveats that change how a number should be read. The "
        "Pathogen Detection tools use these to flag that raw row counts are "
        "about twice the isolate count.\n\n"
        "Results may be truncated. When `truncated` is true, `next` holds the "
        "parameters for the following page; do not describe a truncated "
        "result as complete."
    ),
)

client = EUtilsClient()

# A second service, deliberately sharing the first one's limiter. NCBI's rate
# limit is per IP address and applies across its services, so two independently
# paced clients in one process would each believe they had the whole 3/sec
# budget and together spend six.
pathogen_client = PathogensClient(client.limiter, client.email)

# Caps chosen from measured payload sizes, not guessed. Exceeding them produces
# a ToolError naming the smaller alternative rather than a 30 MB tool result.
MAX_RECORDS_DEFAULT = 100
MAX_SRA_FULL_RUNS = 50  # full SRA XML is ~9.3 KB/record
MAX_TAXONOMY_RECORDS = 50  # taxonomy efetch is ~7.7 KB/taxon
MAX_PATHOGEN_ROWS = 200  # isolate records are ~1 KB each before dedup


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _fail(message: str) -> ToolError:
    """ToolError text reaches the model; other exception types are replaced with
    a generic string it cannot act on. So every anticipated failure is raised as
    a ToolError whose message names a concrete next step."""
    return ToolError(message)


async def _run(coro_fn, tool_name: str):
    """Execute one tool body with timing, a fresh call log, and error translation.

    A tool body returns ``(summary, data, extra)``. Beyond the envelope fields,
    ``extra`` may carry the inputs for the coverage block:

    ``coverage_db`` + ``coverage_term`` + ``result_count``
        Search coverage: what share of the organism's records matched.
    ``coverage_db`` + ``coverage_requested``
        Fetch coverage: how many of the requested UIDs actually came back.
    ``records_by_database``
        Per-database record attribution, for the tools that touch more than one.
    """
    # Both services are drained, every time. A tool that used only one leaves
    # the other's log empty, which costs nothing --- but leaving a stale call
    # behind would credit the next tool's result with a request it never made.
    client.reset_call_log()
    pathogen_client.reset_call_log()
    try:
        with Timer() as timer:
            summary, data, extra = await coro_fn()
            cover = await _coverage(extra, data)
    except NCBIError as exc:
        raise _fail(f"NCBI rejected the request: {exc}") from exc
    except TransportError as exc:
        raise _fail(str(exc)) from exc

    prov = provenance(
        client.reset_call_log() + pathogen_client.reset_call_log(),
        elapsed_ms=timer.elapsed_ms,
        tool=tool_name,
        query_translation=extra.get("query_translation"),
        result_count=extra.get("result_count"),
        records_by_database=extra.get("records_by_database"),
        coverage=cover,
        notes=extra.get("notes"),
    )
    return envelope(
        summary,
        data,
        prov,
        truncated=extra.get("truncated", False),
        next_params=extra.get("next"),
    )


async def _coverage(extra: dict[str, Any], data: Any) -> dict[str, Any] | None:
    """Pick the coverage flavor a tool's ``extra`` asks for, if any.

    Runs inside the tool body's try-block on purpose: a coverage lookup is an
    ordinary NCBI request and can fail like any other, and it should fail the
    same way rather than escaping as an untranslated exception. ``coverage``
    itself already degrades to None on error, so this is belt and braces.
    """
    if "pathogen_coverage_organism" in extra:
        return await _pathogen_coverage(
            extra["pathogen_coverage_organism"],
            extra.get("result_count"),
            extra.get("pathogen_coverage_fq"),
        )

    db = extra.get("coverage_db")
    if not db:
        return None
    requested = extra.get("coverage_requested")
    if requested is not None:
        returned = len(data) if isinstance(data, (list, dict)) else 0
        return coverage.for_fetch(db, int(requested), returned)
    term = extra.get("coverage_term")
    if term and extra.get("result_count") is not None:
        return await coverage.for_search(client, db, term, int(extra["result_count"]))
    return None


async def _pathogen_coverage(
    organism: str | None, matched: int | None, primary_fq: str | None = None
) -> dict[str, Any] | None:
    """Coverage for a Pathogen Detection count: what share of the organism matched.

    Same shape and same purpose as the E-utilities search flavor --- an AMR
    count is meaningless without knowing how many isolates of that organism
    exist. The organism is the denominator; the gene filter is the selection
    being measured, so it is the clause that gets dropped.

    Degrades to None on any failure, like ``coverage.for_search``: this is
    commentary on a result that already succeeded.
    """
    if matched is None or not coverage.enabled():
        return None
    if not organism:
        return None
    fq = pathogens.build_fq({"taxgroup_name": [organism]})
    if primary_fq is not None and fq == primary_fq:
        # Dropping the gene clause left the query the caller already ran, so
        # the organism was the whole filter. Re-requesting it would spend a
        # second call from a 3/sec per-IP budget to rediscover `matched` and
        # report a tautological 100%.
        denominator: int | None = matched
    else:
        try:
            ngout = await pathogen_client.retrieve(
                pathogens.count_params(fq), purpose="coverage"
            )
            denominator = pathogens.distinct_count(ngout)
        except Exception:  # noqa: BLE001 -- never turn a good result into an error
            return None
    if denominator is None:
        return None
    return {
        "database": "pathogen detection isolates",
        "matched": matched,
        "denominator": denominator,
        "percent": round(matched / denominator * 100, 2) if denominator else None,
        "basis": f"all {organism} isolates in Pathogen Detection",
    }


def _split_ids(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(",", " ").split()]
    else:
        parts = [str(p).strip() for p in value]
    ids = [p for p in parts if p]
    if not ids:
        raise _fail("No identifiers were provided.")
    return ids


def _looks_like_uid(value: str) -> bool:
    return value.isdigit()


def _require_uids(ids: list[str], db: str, resolver_tool: str) -> None:
    """esummary needs numeric UIDs. Accessions produce an empty `uids` list and
    no per-uid key, so the failure surfaces as a KeyError far from the cause."""
    non_numeric = [i for i in ids if not _looks_like_uid(i)]
    if non_numeric:
        shown = ", ".join(non_numeric[:3])
        verb = "are" if len(non_numeric) > 1 else "is"
        raise _fail(
            f"esummary for db={db!r} needs numeric Entrez UIDs, but {shown} "
            f"{verb} not numeric (accessions and symbols are not UIDs). "
            f"Resolve them first with {resolver_tool}(db={db!r}, "
            f"accessions=...), which accepts accessions and names directly."
        )


async def _esearch(
    db: str,
    term: str,
    *,
    retmax: int,
    retstart: int = 0,
    use_history: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "term": term,
        "retmax": retmax,
        "retstart": retstart,
        "sort": "relevance",
    }
    if use_history:
        params["usehistory"] = "y"
    payload = await client.request_json("esearch", params, db=db)
    return payload.get("esearchresult", {})


async def _esummary_records(
    db: str, ids: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """esummary in chunks of 500 --- above that NCBI returns HTTP 200 with a
    top-level error rather than truncating.

    Returns ``(records, per_uid_errors)``. A nonexistent UID yields a record
    whose only content is an ``error`` field; those are separated out so they
    reach the agent as notes rather than as data.
    """
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for start in range(0, len(ids), MAX_ESUMMARY_UIDS):
        chunk = ids[start : start + MAX_ESUMMARY_UIDS]
        payload = await client.request_json(
            "esummary", {"id": ",".join(chunk), "version": "2.0"}, db=db
        )
        chunk_records, chunk_errors = parsing.parse_esummary_records(payload)
        records.extend(chunk_records)
        errors.extend(chunk_errors)
    return records, errors


def _uid_error_notes(errors: list[str]) -> list[str] | None:
    if not errors:
        return None
    return [
        "NCBI could not return a summary for these UIDs (they may not exist "
        "in this database): " + "; ".join(errors)
    ]


async def _sra_history_for_accessions(accessions: list[str]) -> dict[str, Any]:
    """Resolve SRA run accessions onto the History server.

    efetch will not take a run accession as ``id`` --- it answers HTTP 400,
    which is at least loud, unlike most of NCBI's failure modes. Accessions
    have to go through esearch first.

    One esearch for the whole batch (``ACC1 OR ACC2 OR ...``), with
    ``usehistory=y`` so the follow-up efetch replays the result set through a
    short URL instead of a multi-kilobyte id list.
    """
    term = " OR ".join(accessions)
    result = await _esearch("sra", term, retmax=len(accessions), use_history=True)
    count = int(result.get("count", 0))
    if count == 0:
        raise _fail(
            f"None of these SRA accessions were found: "
            f"{', '.join(accessions[:5])}. NCBI ran: "
            f"{result.get('querytranslation')}. Check the accessions, or "
            f"search by organism with ncbi_sra_search."
        )
    return {
        "count": count,
        # NCBI returns these keys lowercased but requires them capitalized
        # on the way back in.
        "WebEnv": result.get("webenv"),
        "query_key": result.get("querykey"),
        "idlist": result.get("idlist") or [],
        "query_translation": result.get("querytranslation"),
    }


def _history_params(history: dict[str, Any]) -> dict[str, Any]:
    if history.get("WebEnv") and history.get("query_key"):
        return {"WebEnv": history["WebEnv"], "query_key": history["query_key"]}
    return {"id": ",".join(history["idlist"])}


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


# Unregistered as a tool: ncbi_describe_database covers the same ground, and
# the curated list it returned is still reachable through databases.py. The
# function stays; unregistering is one decorator to put back.
async def ncbi_list_databases() -> dict[str, Any]:
    """List the NCBI Entrez databases this server has curated tools for.

    Start here when unsure which database answers a question. Each entry names
    the tool to use and flags databases where bulk record retrieval is unsafe.
    """

    async def body():
        data = [
            {
                "db": info.name,
                "description": info.description,
                "use_tool": info.tool,
                "bulk_fetch": info.efetch,
            }
            for info in DATABASES.values()
        ]
        return (
            (
                f"{len(data)} curated NCBI databases. "
                "Use ncbi_entrez_raw for any Entrez database not listed here."
            ),
            data,
            {"result_count": len(data)},
        )

    return await _run(body, "ncbi_list_databases")


@server.tool()
async def ncbi_describe_database(
    db: Annotated[
        str,
        Field(
            description=(
                "Entrez database name, e.g. 'sra', 'pubmed', 'biosample', "
                "'bioproject', 'taxonomy', 'gene', 'assembly', 'nuccore'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Describe one Entrez database: its searchable field tags and link targets.

    Use this before composing a search with field tags, to confirm a tag exists.
    NCBI silently drops unrecognized field names and runs the search anyway, so
    a typo returns confident but wrong results rather than an error.
    """

    async def body():
        payload = await client.request_json("einfo", {}, db=db)
        # einforesult.dbinfo is a LIST, even when describing a single database.
        infos = payload.get("einforesult", {}).get("dbinfo") or []
        if not infos:
            raise _fail(
                f"NCBI returned no description for db={db!r}. "
                f"Curated databases: {', '.join(sorted(DATABASES))}."
            )
        info = infos[0]
        fields = [
            {
                "tag": f.get("name"),
                "name": f.get("fullname"),
                "description": f.get("description"),
            }
            for f in info.get("fieldlist") or []
        ]
        links = [
            {"name": link.get("name"), "description": link.get("description")}
            for link in info.get("linklist") or []
        ]
        local = databases.describe(db)
        data = {
            "db": info.get("dbname"),
            "description": info.get("description"),
            "record_count": info.get("count"),
            "last_update": info.get("lastupdate"),
            "search_fields": fields,
            "link_targets": links,
        }
        if local:
            data["notes"] = local.efetch_note
            data["use_tool"] = local.tool
        return (
            (
                f"{info.get('dbname')}: {len(fields)} search fields, "
                f"{len(links)} link targets, {info.get('count')} records."
            ),
            data,
            {},
        )

    return await _run(body, "ncbi_describe_database")


# --------------------------------------------------------------------------
# SRA
# --------------------------------------------------------------------------


@server.tool()
async def ncbi_sra_search(
    organism: Annotated[
        str | None,
        Field(
            description=(
                "Scientific or common organism name, e.g. 'Zaire ebolavirus' "
                "or 'Homo sapiens'. Mapped to the [ORGN] field."
            )
        ),
    ] = None,
    strategy: Annotated[
        str | None,
        Field(
            description=(
                "Sequencing strategy, e.g. 'WGS', 'RNA-Seq', 'AMPLICON', "
                "'ChIP-Seq', 'WXS'. Mapped to the [STRA] field."
            )
        ),
    ] = None,
    platform: Annotated[
        str | None,
        Field(
            description=(
                "Sequencing platform, e.g. 'illumina', 'oxford nanopore', "
                "'pacbio smrt'. Mapped to the [PLAT] field."
            )
        ),
    ] = None,
    layout: Annotated[
        str | None,
        Field(description="Library layout: 'paired' or 'single'. Mapped to [LAY]."),
    ] = None,
    extra_terms: Annotated[
        str | None,
        Field(
            description=(
                "Additional raw Entrez query text, ANDed with the other "
                "parameters. Use for anything the typed parameters do not "
                "cover, e.g. '2014:2015[PDAT]'."
            )
        ),
    ] = None,
    max_results: Annotated[
        int,
        Field(description="Maximum runs to return (1-500).", ge=1, le=500),
    ] = 20,
) -> dict[str, Any]:
    """Search the Sequence Read Archive for sequencing runs.

    Composes a correct Entrez query from typed parameters, so field-tag syntax
    is not needed. Returns run accessions plus summary metadata.

    To list every run in a known BioProject, use ncbi_sra_runs_for_project.
    """

    async def body():
        clauses = []
        if organism:
            clauses.append(f'"{organism}"[ORGN]')
        if strategy:
            clauses.append(f'"{strategy}"[STRA]')
        if platform:
            clauses.append(f'"{platform}"[PLAT]')
        if layout:
            clauses.append(f'"{layout}"[LAY]')
        if extra_terms:
            clauses.append(f"({extra_terms})")
        if not clauses:
            raise _fail(
                "Provide at least one of: organism, strategy, platform, "
                "layout, extra_terms."
            )
        term = " AND ".join(clauses)

        result = await _esearch("sra", term, retmax=max_results)
        count = int(result.get("count", 0))
        translation = result.get("querytranslation")
        uids = result.get("idlist") or []

        if not uids:
            return (
                f"No SRA runs matched. NCBI ran this query: {translation}",
                [],
                {"result_count": 0, "query_translation": translation},
            )

        records, uid_errors = await _esummary_records("sra", uids)
        data = [parsing.expand_sra_summary(r) for r in records]
        extra: dict[str, Any] = {
            "result_count": count,
            "query_translation": translation,
            "notes": _uid_error_notes(uid_errors),
            "coverage_db": "sra",
            "coverage_term": term,
        }
        if count > len(data):
            extra["truncated"] = True
            extra["next"] = {"retstart": len(data)}
        return (
            f"{count} SRA runs matched (showing {len(data)}).",
            data,
            extra,
        )

    return await _run(body, "ncbi_sra_search")


@server.tool()
async def ncbi_sra_runs_for_project(
    accession: Annotated[
        str,
        Field(
            description=(
                "BioProject accession, e.g. 'PRJNA257197'. Also accepts an SRA "
                "study accession such as 'SRP045416'."
            )
        ),
    ],
    max_results: Annotated[
        int,
        Field(description="Maximum runs to return (1-1000).", ge=1, le=1000),
    ] = 100,
    start: Annotated[
        int,
        Field(
            description=(
                "Zero-based offset into the run list, for paging past "
                "max_results. Pass the value from the result's 'next' block."
            ),
            ge=0,
        ),
    ] = 0,
) -> dict[str, Any]:
    """List the sequencing runs belonging to a BioProject or SRA study.

    Use this whenever a paper or record cites a PRJNA/SRP accession. Returns
    one row per run with platform, library, sample, and download path.

    Large studies are paged: a truncated result carries 'next', and passing its
    'start' back returns the following page.
    """

    async def body():
        # Deliberately a direct esearch rather than an elink chain. elink costs
        # more requests AND has a silent-wrong-answer mode: PRJNA257197 maps to
        # two BioProject UIDs, and following the wrong one returns zero links,
        # which reads as "this project has no sequencing data" rather than as
        # an error.
        result = await _esearch(
            "sra", accession, retmax=max_results, retstart=start, use_history=True
        )
        count = int(result.get("count", 0))
        translation = result.get("querytranslation")
        if count == 0:
            raise _fail(
                f"No SRA runs found for {accession!r}. NCBI ran: {translation}. "
                "Check the accession, or search by organism with ncbi_sra_search."
            )
        if start >= count:
            # Caught before the efetch: paging off the end is a caller mistake,
            # and spending a second request to return zero rows would not tell
            # the agent anything this message does not.
            raise _fail(
                f"start={start} is past the end of {accession}, which has "
                f"{count} runs. Valid offsets are 0 to {count - 1}."
            )

        # The History server replays the whole result set through a short URL,
        # avoiding a multi-kilobyte id list. NCBI returns these keys lowercased
        # but requires them capitalized on the way back in.
        webenv = result.get("webenv")
        query_key = result.get("querykey")
        params: dict[str, Any] = {
            "rettype": "runinfo",
            "retmode": "text",
            "retmax": max_results,
        }
        if webenv and query_key:
            # Measured: retstart on the esearch windows the idlist it returns,
            # but the History set still holds all `count` UIDs. So the offset
            # has to be applied again here, or every page returns the first one.
            params["WebEnv"] = webenv
            params["query_key"] = query_key
            params["retstart"] = start
        else:
            # No History, so the windowed idlist above *is* the page. Applying
            # retstart again would offset within it and skip rows.
            params["id"] = ",".join(result.get("idlist") or [])

        text, _ = await client.request("efetch", params, db="sra")
        rows = parsing.parse_runinfo_csv(text, drop_columns=RUNINFO_OPEN_ACCESS_EMPTY)

        extra: dict[str, Any] = {
            "result_count": count,
            "query_translation": translation,
            "coverage_db": "sra",
            "coverage_term": accession,
        }
        shown_through = start + len(rows)
        if shown_through < count:
            extra["truncated"] = True
            # A cursor, not a bigger page. max_results caps at 1000, so on a
            # study larger than that the old "ask for more at once" hint could
            # not reach the tail however many times it was followed.
            extra["next"] = {"start": shown_through, "max_results": max_results}
        return (
            (
                f"{count} SRA runs in {accession} "
                f"(showing {start + 1}-{shown_through})."
            ),
            rows,
            extra,
        )

    return await _run(body, "ncbi_sra_runs_for_project")


@server.tool()
async def ncbi_sra_run_metadata(
    accessions: Annotated[
        str | list[str],
        Field(
            description=(
                "SRA run accessions, e.g. 'SRR1972976' or "
                "['SRR1972976', 'SRR1972977']. Comma- or space-separated "
                "string also accepted."
            )
        ),
    ],
    detail: Annotated[
        str,
        Field(
            description=(
                "'summary' (default) returns the 47-column runinfo table: "
                "platform, library, sample, sizes, download path. "
                "'full' additionally returns sample attributes, study "
                "abstract, and per-file download URLs, at roughly 20x the "
                "size --- use it only for a handful of runs."
            )
        ),
    ] = "summary",
) -> dict[str, Any]:
    """Get metadata for specific SRA runs by accession.

    'summary' is the right default; reach for 'full' only when sample
    attributes or the study abstract are actually needed.
    """

    async def body():
        ids = _split_ids(accessions)
        if detail not in ("summary", "full"):
            raise _fail("detail must be 'summary' or 'full'.")

        if detail == "full" and len(ids) > MAX_SRA_FULL_RUNS:
            raise _fail(
                f"{len(ids)} runs exceeds the {MAX_SRA_FULL_RUNS}-run cap "
                f"for detail='full' (~9.3 KB of XML per run). Use "
                f"detail='summary', or request fewer runs at a time."
            )

        history = await _sra_history_for_accessions(ids)
        fetch_params = _history_params(history)
        fetch_params["retmax"] = len(ids)

        if detail == "full":
            text, _ = await client.request(
                "efetch", {**fetch_params, "retmode": "xml"}, db="sra"
            )
            data = parsing.xml_to_dict(parsing.parse_xml_fragment(text))
            return (
                f"Full XML metadata for {history['count']} SRA run(s).",
                data,
                {
                    "result_count": history["count"],
                    "query_translation": history["query_translation"],
                },
            )

        text, _ = await client.request(
            "efetch",
            {**fetch_params, "rettype": "runinfo", "retmode": "text"},
            db="sra",
        )
        rows = parsing.parse_runinfo_csv(text, drop_columns=RUNINFO_OPEN_ACCESS_EMPTY)
        # runinfo row order does not follow the requested order.
        rows = parsing.order_runinfo(rows, ids)
        missing = parsing.missing_accessions(rows, ids)

        extra: dict[str, Any] = {
            "result_count": len(rows),
            "query_translation": history["query_translation"],
        }
        if missing:
            extra["notes"] = [
                (
                    f"NCBI returned no row for: {', '.join(missing)}. "
                    "These accessions may be invalid, suppressed, or "
                    "dbGaP-controlled."
                )
            ]
        return (
            f"Metadata for {len(rows)} of {len(ids)} requested SRA run(s).",
            rows,
            extra,
        )

    return await _run(body, "ncbi_sra_run_metadata")


# --------------------------------------------------------------------------
# literature
# --------------------------------------------------------------------------
# Unregistered as tools: the standalone pubmed server covers PubMed, and two
# routes to the same database only gave the model a choice it got wrong. The
# functions stay because databases.py and the error hints below still name
# them, and because unregistering is one decorator to put back.


async def ncbi_pubmed_search(
    query: Annotated[
        str,
        Field(
            description=(
                "PubMed search query. Entrez field tags work, e.g. "
                "'ebola[TITLE] AND 2015[PDAT]'."
            )
        ),
    ],
    max_results: Annotated[
        int, Field(description="Maximum citations to return (1-200).", ge=1, le=200)
    ] = 20,
) -> dict[str, Any]:
    """Search PubMed for biomedical literature citations.

    Returns titles, authors, journals, dates, and PMIDs. Abstract text is NOT
    included --- PubMed's summary records have no abstract field. Pass the PMIDs
    to ncbi_pubmed_abstracts to get abstracts.
    """

    async def body():
        result = await _esearch("pubmed", query, retmax=max_results)
        count = int(result.get("count", 0))
        translation = result.get("querytranslation")
        uids = result.get("idlist") or []
        if not uids:
            return (
                f"No PubMed citations matched. NCBI ran: {translation}",
                [],
                {"result_count": 0, "query_translation": translation},
            )
        records, uid_errors = await _esummary_records("pubmed", uids)
        data = [
            {
                "pmid": r.get("uid"),
                "title": r.get("title"),
                "journal": r.get("fulljournalname") or r.get("source"),
                "pubdate": r.get("pubdate"),
                "authors": [a.get("name") for a in r.get("authors") or []],
                "doi": r.get("elocationid"),
            }
            for r in records
        ]
        extra: dict[str, Any] = {
            "result_count": count,
            "query_translation": translation,
            "notes": _uid_error_notes(uid_errors),
            "coverage_db": "pubmed",
            "coverage_term": query,
        }
        if count > len(data):
            extra["truncated"] = True
            extra["next"] = {"max_results": min(count, 200)}
        return (
            (
                f"{count} PubMed citations matched (showing {len(data)}). "
                "Abstracts require ncbi_pubmed_abstracts."
            ),
            data,
            extra,
        )

    return await _run(body, "ncbi_pubmed_search")


async def ncbi_pubmed_abstracts(
    pmids: Annotated[
        str | list[str],
        Field(description="PubMed IDs, e.g. '25814066' or ['25814066', '26060301']."),
    ],
) -> dict[str, Any]:
    """Fetch full abstract text for PubMed citations by PMID.

    A separate call from ncbi_pubmed_search because PubMed summary records do
    not carry abstracts.
    """

    async def body():
        ids = _split_ids(pmids)
        if len(ids) > MAX_RECORDS_DEFAULT:
            raise _fail(
                f"{len(ids)} PMIDs exceeds the {MAX_RECORDS_DEFAULT}-record "
                "cap. Request them in smaller batches."
            )
        text, _ = await client.request(
            "efetch",
            {"id": ",".join(ids), "rettype": "abstract", "retmode": "text"},
            db="pubmed",
        )
        return (
            f"Abstracts for {len(ids)} PubMed citation(s).",
            text,
            {"result_count": len(ids)},
        )

    return await _run(body, "ncbi_pubmed_abstracts")


# --------------------------------------------------------------------------
# samples, projects, organisms
# --------------------------------------------------------------------------


@server.tool()
async def ncbi_biosample_metadata(
    ids: Annotated[
        str | list[str],
        Field(
            description=(
                "BioSample numeric UIDs, e.g. '2604091'. Accessions like "
                "'SAMN02604091' are NOT accepted here --- pass them to "
                "ncbi_find_uids first."
            )
        ),
    ],
) -> dict[str, Any]:
    """Get the biological source metadata for BioSample records.

    Returns host, isolation source, collection date, geographic location, and
    other submitter-supplied sample attributes.
    """

    async def body():
        uids = _split_ids(ids)
        _require_uids(uids, "biosample", "ncbi_find_uids")
        records, uid_errors = await _esummary_records("biosample", uids)
        # The sampledata field is an XML blob and holds most of the payload.
        data = [parsing.expand_xml_field(r, "sampledata") for r in records]
        return (
            f"BioSample metadata for {len(data)} record(s).",
            data,
            {
                "result_count": len(data),
                "notes": _uid_error_notes(uid_errors),
                "coverage_db": "biosample",
                "coverage_requested": len(uids),
            },
        )

    return await _run(body, "ncbi_biosample_metadata")


@server.tool()
async def ncbi_bioproject_summary(
    ids: Annotated[
        str | list[str],
        Field(
            description=(
                "BioProject numeric UIDs, e.g. '257197'. Accessions like "
                "'PRJNA257197' are NOT accepted --- pass them to "
                "ncbi_find_uids first."
            )
        ),
    ],
) -> dict[str, Any]:
    """Get project-level descriptions for BioProject records.

    Returns title, description, organism, submitting organization, and data
    types. To list the sequencing runs in a project, use
    ncbi_sra_runs_for_project, which takes the PRJNA accession directly.
    """

    async def body():
        uids = _split_ids(ids)
        _require_uids(uids, "bioproject", "ncbi_find_uids")
        # esummary only: efetch for this database is unbounded, returning
        # 20 KB or 840 KB for the same call shape with no size control.
        records, uid_errors = await _esummary_records("bioproject", uids)
        return (
            f"BioProject summaries for {len(records)} record(s).",
            records,
            {
                "result_count": len(records),
                "notes": _uid_error_notes(uid_errors),
                "coverage_db": "bioproject",
                "coverage_requested": len(uids),
            },
        )

    return await _run(body, "ncbi_bioproject_summary")


@server.tool()
async def ncbi_taxonomy_lookup(
    query: Annotated[
        str,
        Field(
            description=(
                "Organism name or NCBI taxonomy ID, e.g. 'Zaire ebolavirus' "
                "or '186538'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Resolve an organism name to an NCBI taxonomy ID, rank, and full lineage.

    Use this to confirm the exact organism name before an SRA or sequence
    search, since Entrez organism matching is name-sensitive.
    """

    async def body():
        uids = _split_ids(query) if query.strip().isdigit() else None
        translation = None
        if uids is None:
            result = await _esearch("taxonomy", query, retmax=MAX_TAXONOMY_RECORDS)
            translation = result.get("querytranslation")
            uids = result.get("idlist") or []
            if not uids:
                return (
                    f"No taxonomy record matched {query!r}.",
                    [],
                    {"result_count": 0, "query_translation": translation},
                )

        # efetch, not esummary. The taxonomy esummary record has no lineage and
        # no genetic code --- it carries an empty `commonname` and nothing else
        # of the sort --- so the two fields this tool advertises can only come
        # from efetch. Same one request, ~7.7 KB/taxon instead of ~343 bytes.
        if len(uids) > MAX_TAXONOMY_RECORDS:
            raise _fail(
                f"{len(uids)} taxa exceeds the {MAX_TAXONOMY_RECORDS}-record "
                f"cap for this tool (full records are ~7.7 KB each). Narrow "
                f"the query, or fetch the taxids in smaller batches."
            )
        blob, _ = await client.request(
            "efetch", {"id": ",".join(uids), "retmode": "xml"}, db="taxonomy"
        )
        data = parsing.parse_taxonomy_records(blob)

        # efetch drops taxids it cannot resolve without comment, the same way
        # esummary does --- and unlike esummary there is no per-record error to
        # surface, so the only signal is the count.
        returned = {r.get("taxid") for r in data}
        missing = [uid for uid in uids if uid not in returned]
        notes = (
            ["NCBI returned no taxonomy record for these IDs: " + ", ".join(missing)]
            if missing
            else None
        )
        return (
            f"{len(data)} taxonomy record(s) for {query!r}.",
            data,
            {
                "result_count": len(data),
                "query_translation": translation,
                "coverage_db": "taxonomy",
                "coverage_requested": len(uids),
                "notes": notes,
            },
        )

    return await _run(body, "ncbi_taxonomy_lookup")


@server.tool()
async def ncbi_gene_info(
    ids: Annotated[
        str | list[str],
        Field(
            description=(
                "Gene numeric UIDs, e.g. '7157' for TP53. Gene symbols are NOT "
                "accepted --- resolve them with ncbi_find_uids first."
            )
        ),
    ],
) -> dict[str, Any]:
    """Get gene records: symbol, aliases, description, genomic location, summary."""

    async def body():
        uids = _split_ids(ids)
        _require_uids(uids, "gene", "ncbi_find_uids")
        # esummary only. efetch retmode=xml returned 34.6 MB for TP53 alone.
        records, uid_errors = await _esummary_records("gene", uids)
        return (
            f"Gene records for {len(records)} UID(s).",
            records,
            {
                "result_count": len(records),
                "notes": _uid_error_notes(uid_errors),
                "coverage_db": "gene",
                "coverage_requested": len(uids),
            },
        )

    return await _run(body, "ncbi_gene_info")


@server.tool()
async def ncbi_assembly_info(
    ids: Annotated[
        str | list[str],
        Field(
            description=(
                "Assembly numeric UIDs. Accessions like 'GCF_000001405.40' "
                "are NOT accepted --- resolve them with ncbi_find_uids first."
            )
        ),
    ],
) -> dict[str, Any]:
    """Get genome assembly records: accession, level, submitter, and FTP paths.

    The FTP paths in the result are where the actual sequence files live; this
    server does not download them.
    """

    async def body():
        uids = _split_ids(ids)
        _require_uids(uids, "assembly", "ncbi_find_uids")
        # esummary only. efetch is not implemented for assembly and does not
        # say so: it returns HTTP 200 and a 192-byte <IdList> echoing the UID.
        records, uid_errors = await _esummary_records("assembly", uids)
        data = [parsing.expand_xml_field(r, "meta") for r in records]
        return (
            f"Assembly records for {len(data)} UID(s).",
            data,
            {
                "result_count": len(data),
                "notes": _uid_error_notes(uid_errors),
                "coverage_db": "assembly",
                "coverage_requested": len(uids),
            },
        )

    return await _run(body, "ncbi_assembly_info")


@server.tool()
async def ncbi_sequence_fetch(
    ids: Annotated[
        str | list[str],
        Field(
            description=(
                "Sequence accessions or UIDs, e.g. 'NM_000546' or 'KM034562'. "
                "Accessions are accepted directly here."
            )
        ),
    ],
    db: Annotated[
        str,
        Field(description="'nuccore' for nucleotide (default) or 'protein'."),
    ] = "nuccore",
    format: Annotated[
        str,
        Field(
            description=(
                "'fasta' (default, compact) or 'genbank' (full flatfile with "
                "feature annotations --- roughly 15x larger)."
            )
        ),
    ] = "fasta",
) -> dict[str, Any]:
    """Fetch nucleotide or protein sequences by accession.

    FASTA by default. Request 'genbank' only when feature annotations are
    needed; a 2.5 kb mRNA is 37 KB as GenBank versus about 2.5 KB as FASTA.
    """

    async def body():
        seq_ids = _split_ids(ids)
        if db not in ("nuccore", "protein"):
            raise _fail("db must be 'nuccore' or 'protein'.")
        if format not in ("fasta", "genbank"):
            raise _fail("format must be 'fasta' or 'genbank'.")
        if len(seq_ids) > MAX_RECORDS_DEFAULT:
            raise _fail(
                f"{len(seq_ids)} sequences exceeds the {MAX_RECORDS_DEFAULT}-"
                "record cap. Request them in smaller batches."
            )
        rettype = "fasta" if format == "fasta" else "gb"
        text, _ = await client.request(
            "efetch",
            {"id": ",".join(seq_ids), "rettype": rettype, "retmode": "text"},
            db=db,
        )
        if not text.strip():
            raise _fail(
                f"NCBI returned nothing for {', '.join(seq_ids[:3])}. "
                f"Check that the accessions exist in db={db!r}."
            )
        return (
            f"{format} for {len(seq_ids)} record(s) from {db}.",
            text,
            {"result_count": len(seq_ids)},
        )

    return await _run(body, "ncbi_sequence_fetch")


# --------------------------------------------------------------------------
# cross-database navigation
# --------------------------------------------------------------------------


@server.tool()
async def ncbi_find_uids(
    db: Annotated[
        str,
        Field(
            description=(
                "Entrez database to search, e.g. 'biosample', 'bioproject', "
                "'gene', 'assembly'."
            )
        ),
    ],
    accessions: Annotated[
        str | list[str],
        Field(
            description=(
                "Accessions or names to resolve, e.g. 'SAMN02604091' or 'PRJNA257197'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Resolve accessions or names to the numeric Entrez UIDs other tools need.

    Most NCBI tools here require numeric UIDs, but papers and records cite
    accessions. This is the bridge. One search per accession.
    """

    async def body():
        wanted = _split_ids(accessions)
        resolved: list[dict[str, Any]] = []
        for accession in wanted:
            result = await _esearch(db, accession, retmax=10)
            uids = result.get("idlist") or []
            resolved.append(
                {
                    "query": accession,
                    "uids": uids,
                    "count": int(result.get("count", 0)),
                    "query_translation": result.get("querytranslation"),
                }
            )
        found = sum(1 for r in resolved if r["uids"])
        notes = None
        multi = [r["query"] for r in resolved if len(r["uids"]) > 1]
        if multi:
            # Real and load-bearing: PRJNA257197 resolves to two BioProject
            # UIDs. Picking one arbitrarily can yield an empty downstream
            # result that looks like a legitimate "no data".
            notes = [
                (
                    f"Ambiguous --- more than one UID matched: {', '.join(multi)}. "
                    "Inspect each UID rather than assuming the first is correct."
                )
            ]
        return (
            f"Resolved {found} of {len(wanted)} identifier(s) in db={db!r}.",
            resolved,
            {"result_count": found, "notes": notes},
        )

    return await _run(body, "ncbi_find_uids")


@server.tool()
async def ncbi_linked_records(
    from_db: Annotated[
        str, Field(description="Source Entrez database, e.g. 'bioproject'.")
    ],
    to_dbs: Annotated[
        str,
        Field(
            description=(
                "One or more target databases, comma-separated, e.g. "
                "'sra,biosample,pubmed'. Multiple targets cost one request."
            )
        ),
    ],
    ids: Annotated[
        str | list[str],
        Field(description="Numeric UIDs in the source database."),
    ],
) -> dict[str, Any]:
    """Find records in other databases linked to the given records.

    Results are grouped by target database only. If several source UIDs are
    given, NCBI merges their links and does not report which source produced
    which target, so do not attribute a linked record to a specific input.
    """

    async def body():
        uids = _split_ids(ids)
        targets = ",".join(t.strip() for t in to_dbs.split(",") if t.strip())
        if not targets:
            raise _fail("to_dbs must name at least one target database.")
        payload = await client.request_json(
            "elink",
            {"dbfrom": from_db, "id": ",".join(uids), "db": targets},
        )
        links = parsing.parse_linksets(payload)
        total = sum(len(v) for v in links.values())
        notes = None
        if len(uids) > 1:
            notes = [
                (
                    "Links from multiple source UIDs are merged by NCBI; "
                    "per-source attribution is not available."
                )
            ]
        if not links:
            return (
                f"No links found from {from_db} to {targets}.",
                {},
                {"result_count": 0, "notes": notes},
            )
        return (
            f"{total} linked record(s) across {len(links)} database(s).",
            links,
            {"result_count": total, "notes": notes},
        )

    return await _run(body, "ncbi_linked_records")


@server.tool()
async def ncbi_entrez_raw(
    utility: Annotated[
        str,
        Field(
            description=(
                "E-utility name: 'esearch', 'esummary', 'efetch', 'elink', "
                "'einfo', or 'espell'."
            )
        ),
    ],
    db: Annotated[
        str,
        Field(
            description=(
                "Entrez database. Required. Omitting it makes esearch silently "
                "search PubMed instead of erroring."
            )
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Extra E-utilities parameters, e.g. "
                "{'term': 'ebola', 'retmax': 5, 'rettype': 'fasta'}. "
                "tool, email, and api_key are added automatically."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Escape hatch: call any E-utility directly for cases the other tools miss.

    Prefer the curated tools --- they encode database-specific hazards this one
    does not. Use this for databases or parameters they do not cover.
    """

    async def body():
        allowed = {"esearch", "esummary", "efetch", "elink", "einfo", "espell"}
        if utility not in allowed:
            raise _fail(f"utility must be one of: {', '.join(sorted(allowed))}.")

        extra = dict(params or {})
        if utility == "efetch":
            guidance = databases.efetch_guidance(db)
            if guidance:
                raise _fail(guidance)

        text, _ = await client.request(utility, extra, db=db)
        if len(text) > 200_000:
            return (
                (
                    f"{utility} on db={db!r} returned {len(text)} bytes; "
                    "truncated to 200,000. Narrow the request."
                ),
                text[:200_000],
                {"truncated": True},
            )
        return (f"{utility} on db={db!r}: {len(text)} bytes.", text, {})

    return await _run(body, "ncbi_entrez_raw")


# --------------------------------------------------------------------------
# NCBI Pathogen Detection --- a separate service. See pathogens.py.
# --------------------------------------------------------------------------


def _pathogen_filter(
    organism: str | None,
    amr_genes: str | list[str] | None,
    host: str | None,
    isolation_source: str | None,
    epi_type: str | None,
) -> tuple[str | None, dict[str, list[str]]]:
    clauses: dict[str, list[str]] = {}
    if organism:
        clauses["taxgroup_name"] = [organism]
    if amr_genes:
        clauses["AMR_genotypes"] = _split_ids(amr_genes)
    if host:
        clauses["host"] = [host]
    if isolation_source:
        clauses["isolation_source"] = [isolation_source]
    if epi_type:
        clauses["epi_type"] = [epi_type]
    return pathogens.build_fq(clauses), clauses


@server.tool()
async def ncbi_pathogen_isolate_count(
    organism: Annotated[
        str | None,
        Field(
            description=(
                "Pathogen Detection organism group, e.g. 'Staphylococcus "
                "aureus'. These are curated groups, not taxonomy names --- "
                "'E.coli and Shigella' is one group. List them with "
                "ncbi_pathogen_organisms."
            )
        ),
    ] = None,
    amr_genes: Annotated[
        str | list[str] | None,
        Field(
            description=(
                "AMR gene symbols, e.g. 'mecA,mecC'. Multiple genes are OR-ed, "
                "so this counts isolates carrying ANY of them. List the "
                "symbols available for an organism with ncbi_pathogen_amr_genes."
            )
        ),
    ] = None,
    host: Annotated[
        str | None, Field(description="Host organism, e.g. 'Homo sapiens'.")
    ] = None,
    isolation_source: Annotated[
        str | None, Field(description="Isolation source, e.g. 'blood'.")
    ] = None,
    epi_type: Annotated[
        str | None,
        Field(description="Epidemiological type: 'clinical' or 'environmental/other'."),
    ] = None,
) -> dict[str, Any]:
    """Count DISTINCT pathogen isolates matching a filter, without fetching them.

    Use this for "how many isolates ..." questions. One request. Returns the
    deduplicated isolate count, which is NOT the row count the service reports
    --- see the note in the result.
    """

    async def body():
        fq, clauses = _pathogen_filter(
            organism, amr_genes, host, isolation_source, epi_type
        )
        ngout = await pathogen_client.retrieve(pathogens.count_params(fq))

        isolates = pathogens.distinct_count(ngout)
        rows = pathogens.raw_row_count(ngout)
        if isolates is None:
            raise _fail(
                "Pathogen Detection did not return the target_acc facet, so "
                "the distinct isolate count could not be determined. The row "
                f"count was {rows}, which is roughly twice the isolate count "
                "and must not be reported as one."
            )

        described = ", ".join(f"{k}={v}" for k, v in clauses.items()) or "no filter"
        notes = [
            (
                f"'isolates' ({isolates:,}) is the distinct count from the "
                f"target_acc facet. The service's own totalCount for this "
                f"query is {rows:,} --- it indexes each isolate twice, so "
                f"that figure is not an isolate count. Report 'isolates'."
            )
        ]
        if isolates == 0:
            # Every filter matches literally, and an unrecognized VALUE is not
            # an error --- only an unrecognized FIELD is. So a misspelled
            # organism or gene returns a confident zero indistinguishable from a
            # real negative. The coverage denominator settles it (zero there
            # means the organism itself matched nothing), but say so here rather
            # than relying on the agent to cross-reference two blocks.
            notes.append(
                "Zero matched. Filter values are matched literally and an "
                "unrecognized value returns 0 rather than an error, so this may "
                "be a misspelling rather than a real absence. Confirm spellings "
                "with ncbi_pathogen_organisms and ncbi_pathogen_amr_genes; if "
                "the coverage denominator is also 0, the organism name is wrong."
            )
        return (
            f"{isolates:,} distinct isolates ({described}).",
            {
                "isolates": isolates,
                "index_rows": rows,
                "filter": clauses or None,
            },
            {
                "result_count": isolates,
                "query_translation": pathogens.solr_query(ngout),
                "pathogen_coverage_organism": organism,
                # So coverage can tell whether its denominator query is the one
                # already run here and skip a duplicate request.
                "pathogen_coverage_fq": fq,
                "notes": notes,
            },
        )

    return await _run(body, "ncbi_pathogen_isolate_count")


@server.tool()
async def ncbi_pathogen_isolates(
    organism: Annotated[
        str | None,
        Field(
            description="Pathogen Detection organism group, e.g. 'Staphylococcus aureus'."
        ),
    ] = None,
    amr_genes: Annotated[
        str | list[str] | None,
        Field(description="AMR gene symbols to require, e.g. 'mecA,mecC' (OR-ed)."),
    ] = None,
    host: Annotated[str | None, Field(description="Host organism.")] = None,
    isolation_source: Annotated[
        str | None, Field(description="Isolation source, e.g. 'blood'.")
    ] = None,
    epi_type: Annotated[
        str | None,
        Field(description="Epidemiological type: 'clinical' or 'environmental/other'."),
    ] = None,
    limit: Annotated[
        int,
        Field(
            description=(
                f"Isolates to return, 1-{MAX_PATHOGEN_ROWS}. Use "
                "ncbi_pathogen_isolate_count for totals rather than a large limit."
            ),
            ge=1,
            le=MAX_PATHOGEN_ROWS,
        ),
    ] = 20,
    start: Annotated[int, Field(description="Row offset for paging.", ge=0)] = 0,
) -> dict[str, Any]:
    """Fetch pathogen isolate records: AMR genotypes, AST phenotypes, and linked
    BioSample/SRA/assembly accessions.

    Each isolate carries its resistance genotype (`AMR_genotypes`), any
    measured susceptibility phenotypes (`AST_phenotypes`), and the accessions
    needed to pull the underlying data from the other NCBI tools here.
    """

    async def body():
        fq, clauses = _pathogen_filter(
            organism, amr_genes, host, isolation_source, epi_type
        )
        # record_params over-fetches 2x (the index stores every isolate twice)
        # and refuses to carry a facet, which would empty `content` outright.
        ngout = await pathogen_client.retrieve(
            pathogens.record_params(
                fq,
                limit=min(limit, MAX_PATHOGEN_ROWS),
                start=start,
                fl="target_acc,biosample_acc,asm_acc,Run,bioproject_acc,scientific_name,taxgroup_name,epi_type,collection_date,geo_loc_name,isolation_source,host,number_amr_genes,AMR_genotypes,AMR_genotypes_core,AST_phenotypes",
            )
        )

        rows = pathogens.records(ngout)[:limit]
        total_rows = pathogens.raw_row_count(ngout)
        described = ", ".join(f"{k}={v}" for k, v in clauses.items()) or "no filter"
        truncated = total_rows > start + limit * 2

        return (
            f"{len(rows)} isolate(s) ({described}).",
            rows,
            {
                "result_count": len(rows),
                # No query_translation here, deliberately: this service echoes
                # the translated query only when facets are requested, and a
                # facet would empty this result. See pathogens.py.
                "truncated": truncated,
                "next": {"start": start + limit * 2} if truncated else None,
                "notes": [
                    (
                        "Records are deduplicated on target_acc: the index stores "
                        "each isolate twice. Use ncbi_pathogen_isolate_count for a "
                        "total; do not infer one from this page."
                    ),
                    (
                        "The translated Solr query is not available alongside "
                        "records --- requesting it would return zero records. "
                        "ncbi_pathogen_isolate_count reports it for the same "
                        "filter."
                    ),
                ],
            },
        )

    return await _run(body, "ncbi_pathogen_isolates")


@server.tool()
async def ncbi_pathogen_amr_genes(
    organism: Annotated[
        str | None,
        Field(
            description=(
                "Restrict the vocabulary to one organism group, e.g. "
                "'Staphylococcus aureus'. Strongly recommended --- the "
                "unfiltered list spans every organism."
            )
        ),
    ] = None,
    contains: Annotated[
        str | None,
        Field(description="Case-insensitive substring filter, e.g. 'mec' or 'bla'."),
    ] = None,
    limit: Annotated[
        int, Field(description="Maximum gene symbols to return.", ge=1, le=2000)
    ] = 100,
) -> dict[str, Any]:
    """List the AMR gene symbols that actually exist in Pathogen Detection, with
    how many index rows carry each.

    Call this before filtering by gene. The field is matched literally, and a
    symbol that does not exist returns zero isolates rather than an error, so a
    typo is indistinguishable from a real negative.
    """

    async def body():
        fq, _ = _pathogen_filter(organism, None, None, None, None)
        ngout = await pathogen_client.retrieve(
            {"limit": 0, "facets": "AMR_genotypes[||1|20000]", "fq": fq}
        )
        buckets = pathogens.facet_buckets(ngout, "AMR_genotypes")

        # The field is indexed with both the bare symbol ("mecA") and the
        # symbol=STATUS token ("mecA=COMPLETE"). Bare symbols are what a caller
        # filters on; the status variants are kept out of the default view
        # because they triple the list without adding genes.
        bare = [b for b in buckets if "=" not in str(b.get("val", ""))]
        if contains:
            needle = contains.lower()
            bare = [b for b in bare if needle in str(b.get("val", "")).lower()]

        shown = bare[:limit]
        data = [{"gene": b.get("val"), "index_rows": b.get("count")} for b in shown]
        scope = f" in {organism}" if organism else ""
        return (
            f"{len(data)} AMR gene symbol(s){scope}"
            + (f" matching {contains!r}" if contains else "")
            + f" (of {len(bare)} total).",
            data,
            {
                "result_count": len(data),
                "query_translation": pathogens.solr_query(ngout),
                "truncated": len(bare) > limit,
                "notes": [
                    (
                        "index_rows is a row count, about twice the isolate count "
                        "(each isolate is indexed twice). For isolate counts call "
                        "ncbi_pathogen_isolate_count with the gene symbol."
                    ),
                    (
                        "Each gene is also indexed as 'gene=STATUS' (COMPLETE, "
                        "PARTIAL, PARTIAL_END_OF_CONTIG, MISTRANSLATION, HMM, "
                        "POINT); those variants are omitted here. Filtering on the "
                        "bare symbol counts all statuses."
                    ),
                ],
            },
        )

    return await _run(body, "ncbi_pathogen_amr_genes")


@server.tool()
async def ncbi_pathogen_organisms(
    contains: Annotated[
        str | None, Field(description="Case-insensitive substring filter, e.g. 'coli'.")
    ] = None,
    limit: Annotated[
        int, Field(description="Maximum organism groups to return.", ge=1, le=500)
    ] = 50,
) -> dict[str, Any]:
    """List the organism groups Pathogen Detection covers, largest first.

    These are curated groups, not taxonomy names: 'E.coli and Shigella' is a
    single group. Filters match them literally, so confirm the exact spelling
    here before using ncbi_pathogen_isolate_count or ncbi_pathogen_isolates.
    """

    async def body():
        ngout = await pathogen_client.retrieve(
            {"limit": 0, "facets": "taxgroup_name[||1|500]"}
        )
        buckets = pathogens.facet_buckets(ngout, "taxgroup_name")
        if contains:
            needle = contains.lower()
            buckets = [b for b in buckets if needle in str(b.get("val", "")).lower()]

        shown = buckets[:limit]
        data = [
            {
                "organism": b.get("val"),
                # Halved, not raw: this is the one place a per-organism isolate
                # count is available without a request each, and the 2x
                # duplication is uniform. Flagged as approximate because it is
                # derived rather than measured per organism.
                "approx_isolates": int(b["count"]) // 2 if b.get("count") else None,
            }
            for b in shown
        ]
        return (
            f"{len(data)} organism group(s)"
            + (f" matching {contains!r}" if contains else "")
            + ".",
            data,
            {
                "result_count": len(data),
                "query_translation": pathogens.solr_query(ngout),
                "truncated": len(buckets) > limit,
                "notes": [
                    (
                        "approx_isolates halves the service's row count, which "
                        "double-indexes every isolate. For an exact figure call "
                        "ncbi_pathogen_isolate_count with the organism."
                    )
                ],
            },
        )

    return await _run(body, "ncbi_pathogen_organisms")


# Running the server lives in mcp_servers/ncbi.py, next to the other servers.
