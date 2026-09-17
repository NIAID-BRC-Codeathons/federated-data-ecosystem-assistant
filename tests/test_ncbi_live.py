"""End-to-end calls against the real NCBI API.

Deselected by default (``addopts = -m 'not live'``). Run with::

    pytest -m live

Deliberately few. At 3 requests/second a broad live suite is slow, and at a
codeathon it spends an allowance the whole room shares.

These assert on structure and invariants, not on exact counts --- NCBI's data
changes, and a test that breaks when a study gains a run is a test people learn
to ignore.
"""

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from ncbi_lib.server import (
    client,
    ncbi_find_uids,
    ncbi_linked_records,
    ncbi_sra_run_metadata,
    ncbi_sra_runs_for_project,
    ncbi_taxonomy_lookup,
)

pytestmark = pytest.mark.live

EBOLA_PROJECT = "PRJNA257197"
EBOLA_RUN = "SRR1972976"


def unwrap(tool, **kwargs):
    """Call a tool directly.

    FastMCP's @tool() decorator returns the original function rather than a
    wrapper object (measured on mcp 1.30.0), so the tools are ordinary
    coroutines here. (list_tools() still yields Tool objects, which is what
    test_ncbi_invariants inspects.)
    """
    return tool(**kwargs)


async def test_runs_for_project():
    result = await unwrap(ncbi_sra_runs_for_project, accession=EBOLA_PROJECT)
    assert result["provenance"]["result_count"] > 500
    assert result["data"], "no runs returned"
    assert result["data"][0]["Run"].startswith("SRR")
    assert result["provenance"]["urls"]


async def test_run_metadata_has_the_cross_references_that_make_it_useful():
    result = await unwrap(ncbi_sra_run_metadata, accessions=[EBOLA_RUN])
    row = result["data"][0]
    assert row["Run"] == EBOLA_RUN
    for field in ("BioProject", "BioSample", "download_path", "Platform"):
        assert row.get(field), f"{field} is empty"


async def test_runinfo_rows_are_matched_to_the_right_accession():
    """The join-on-Run guarantee, live.

    The accessions are read out of a real project rather than hardcoded --- a
    guessed accession that does not exist makes this test fail for a reason
    that has nothing to do with ordering.
    """
    project = await unwrap(ncbi_sra_runs_for_project, accession=EBOLA_PROJECT)
    available = [row["Run"] for row in project["data"][:3]]
    assert len(available) == 3, "need three runs to test ordering"

    requested = list(reversed(available))
    result = await unwrap(ncbi_sra_run_metadata, accessions=requested)
    assert [r["Run"] for r in result["data"]] == requested


async def test_no_links_is_an_empty_result_not_a_crash():
    result = await unwrap(
        ncbi_linked_records, from_db="sra", to_dbs="pubmed", ids="1063523"
    )
    assert result["provenance"]["result_count"] >= 0


async def test_accession_resolves_to_uids():
    result = await unwrap(ncbi_find_uids, db="bioproject", accessions=EBOLA_PROJECT)
    assert result["data"][0]["uids"]


async def test_query_translation_is_reported():
    """NCBI rewrites terms silently; the echo is the only way to see it."""
    result = await unwrap(ncbi_taxonomy_lookup, query="Zaire ebolavirus")
    assert result["provenance"]["query_translation"]
    assert result["data"][0]["taxid"]


async def test_paging_walks_forward_instead_of_re_fetching():
    """`next` is a cursor, not a bigger page.

    It used to say ``max_results``, which is capped at 1000 --- so on a study
    larger than that, following the hint returned the same rows forever and the
    tail was unreachable. Two small pages are enough to pin the contract; the
    full 891-run walk is not worth the requests at 3/sec.
    """
    first = await unwrap(
        ncbi_sra_runs_for_project, accession=EBOLA_PROJECT, max_results=5
    )
    assert first["truncated"] is True
    cursor = first["next"]["start"]
    assert cursor == 5

    second = await unwrap(
        ncbi_sra_runs_for_project,
        accession=EBOLA_PROJECT,
        max_results=5,
        start=cursor,
    )
    page_one = [row["Run"] for row in first["data"]]
    page_two = [row["Run"] for row in second["data"]]
    assert page_two, "cursor returned an empty page"
    assert set(page_one).isdisjoint(page_two), "the cursor re-fetched page one"


async def test_paging_past_the_end_says_so():
    with pytest.raises(ToolError) as exc:
        await unwrap(ncbi_sra_runs_for_project, accession=EBOLA_PROJECT, start=99_999)
    assert "past the end" in str(exc.value)


async def test_bad_accession_fails_with_an_actionable_message():
    with pytest.raises(ToolError) as exc:
        await unwrap(ncbi_sra_runs_for_project, accession="PRJNA000000000")
    assert "ncbi_sra_search" in str(exc.value)


@pytest.fixture(autouse=True)
async def _fresh_client_per_test():
    """Close the shared client after each test.

    The client is created lazily and binds to whatever event loop is running at
    the time. pytest-asyncio gives each test its own loop, so a client left open
    across tests would be closed on a loop that no longer exists
    ("RuntimeError: Event loop is closed").

    This is a test-harness concern only: in production the server runs on a
    single loop for its whole lifetime, which is exactly what the shared client
    is designed for.
    """
    yield
    await client.aclose()
