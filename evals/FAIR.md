# FAIR mapping

FAIR — Findable, Accessible, Interoperable, Reusable — is the frame a NIAID program
officer will reach for first, so this page states what the system earns under each
letter, which pipeline and tool demonstrates it, the measured number with its date, and
the limit that number does not cover.

**This is not a score, and the headline is not "0/10 vs 10/10".** That table lives in
[`README.md`](README.md) and it explains there why it overstates: the same people wrote
the questions, the tools and the ground truths, and two of the ten cases call the
harness's own helper (`pathogen_count`, `run_eval.py:85`) rather than a shipped tool.

The claim that does hold is about three properties every answer carries.

**Provenance.** Each result returns the call that produced it — an `api_call` block in
`geo.py` and `brc_analytics.py`, a `request_cost` count in `ncbi_lib/envelope.py`, and
`query_translation` wherever the service rewrote the query before answering it.

**Honest nulls.** An empty result says which kind of empty it is. `geo.py:132` returns
`pubmed_note`: "No PubMed record is linked to this GEO record… It never means the study
has no data." A zero from a wrong Pathogen Detection group name is labelled a bad query,
not an absence.

**Premise repair.** When the question is malformed, the system returns the number that
proves it, then the nearest answerable questions — 2 of 581,464, then MRSA, ESBL and
fluoroquinolone resistance.

---

## F — Findable

| | |
|---|---|
| What earns it | Finding the right record, not a plausible neighbour. The `gds` index holds Series, Platforms, Samples and DataSets together, and a GEO accession is not an Entrez UID. |
| Pipeline · tool | P3 · `geo_search`, `geo_resolve_accession` · P4 · `ncbi_pathogen_organisms` |
| Measured, 17 Sep 2026 | `geo_search(organism="Escherichia coli", term="ciprofloxacin", entry_type="gse")` returns **37** Series. Unfiltered, the same search returns **513** mixed records. `geo_resolve_accession("GSM9284462")` returns **309284462** in **0** requests, where `esearch` ranks the parent Series first instead. `ncbi_pathogen_organisms(contains="coli")` gives the curated group name **`E.coli and Shigella`**; the taxonomy name `Escherichia coli` returns **0**. |
| Honest limit | The discovery layer is not wired in. NDE's tools (`nde_search_datasets`, `nde_facet_counts`, `nde_get_record`) exist in `NIAID-Data-Ecosystem/src/nde_mcp/server.py`, but the package is stdio-only and absent from `chatbot.py`'s `MCP_SERVERS`, so **P1 cannot run in the demo at all**. GEO counts are text matches, not curation: the same query shape for "antimicrobial resistance" expands through MeSH and returns 234 records that are not 234 AMR experiments. |

## A — Accessible

| | |
|---|---|
| What earns it | The numbers reach the user, not just the metadata about them. No source here needs a key. |
| Pipeline · tool | P3 · `geo_series` · P2 · `brc_ena_runs` |
| Measured, 17 Sep 2026 | `geo_series("GSE309890")` returns **1 file**, `GSE309890_FPKMs_allSamples.csv.gz`, with a real download URL. The `esummary` field `suppfile` returns `'CSV'` — a format, never a filename — because the values live only in the GEO FTP tree, a different host with no API. `brc_ena_runs(taxonomy_id="562")` returns **551,679** runs in ENA; the federated `search_ena` returns `count=50, has_more=true`, a page size four orders of magnitude off. A byte-range GET against the first FASTQ URL it hands back answers 200 or 206 (`tests/test_bobby_servers_live.py`). |
| Honest limit | The supplementary file is whatever the submitter uploaded — no schema, no units contract, no guarantee of a matrix, and nothing in this repo parses it. Accessible here means the URL resolves, not that the file is machine-readable. |

## I — Interoperable

| | |
|---|---|
| What earns it | The joins between sources are exact and are checked, rather than assumed from a label. |
| Pipeline · tool | P2 · `brc_ena_study`, `ncbi_sra_runs_for_project` · P3 · `geo_series` |
| Measured, 17 Sep 2026 | `brc_ena_study("PRJNA715470")` returns **369 runs across 13 distinct organisms** in a project labelled *E. coli*. BRC's own study endpoint returns HTTP 500 for the same accession. `geo_series("GSE309890")` carries `bioproject = PRJNA1363958`, which `ncbi_sra_runs_for_project` turns into **6** SRA runs — a GEO to SRA to ENA crosswalk with no name matching anywhere in it. The BRC K-12 assembly `GCF_000005845.2` carries `ncbiTaxonomyId` **511145** when the question asked about **562**, so the join to a GEO study on K-12 MG1655 is strain-exact. |
| Honest limit | ENA reports 369 runs and NCBI 382 experiments for the same project — different units, different indexes, and an answer has to say which one it is quoting. BV-BRC, first on the whiteboard and first in the charter's resource list, has no server here and is not connected. |

## R — Reusable

| | |
|---|---|
| What earns it | Someone else can re-derive the number, add a source, or find out that an upstream change broke it. |
| Pipeline · tool | All · the `api_call` / `request_cost` / `query_translation` envelope · P8 · the refusal |
| Measured, 17 Sep 2026 | `uv run pytest -q` collects **566** offline tests (589 total, 23 live deselected) in under a second — **52** for GEO, **26** for BRC. Every server follows the one-file-per-resource pattern set out in [`SERVERS.md`](../SERVERS.md), and BRC's own 12-tool public server was federated unchanged rather than reimplemented. The P8 refusal is reusable as text: **2** of **581,464** *E. coli* and Shigella isolates carry `mecA`, against **93,260** of **171,412** *S. aureus*, with `blaCTX-M-15` at **75,487** and `gyrA_S83L` at **170,726** as the reframings. |
| Honest limit | There is no citation export — the provenance is in the result, not in a form anyone can paste into a manuscript. Offline tests will not notice an upstream schema change; only the live suite and `run_eval.py` will, which is how BRC's catalogue change between 16 and 17 Sep was caught mid-build. Two ENA scorers in `run_eval.py` accept any integer above 1,000, so 1,001 passes where 551,679 is true. |

---

## What a reviewer will ask

| Question | The answer, out loud |
|---|---|
| Can another BRC reuse this? | One flat file per resource in the pattern in `SERVERS.md`, and BRC's own 12-tool server was federated unchanged — adding a source is three registrations, not a rewrite. |
| Is the provenance good enough to cite in a publication? | Every number comes back with its call URL and the query as the service rewrote it, so it can be re-derived; there is no citation export yet, so a person still copies it across by hand. |
| What does a query cost? | The NCBI request count is reported per answer as `request_cost`; LLM token cost is not measured anywhere in this repo. |
| What happens when an upstream schema changes? | The offline tests will not catch it — the live suite and `run_eval.py` will, which is how BRC's catalogue change on 16–17 Sep was noticed while we were building against it. |
| Who maintains this after Friday? | Each server has a named owner in `SERVERS.md`, and there is no commitment past the event; say that rather than implying one. |

## What this page does not claim

Four of the charter's five question types are demonstrated. Launching an analysis is the
fifth, and it stops by design at the resolved workflow inputs: BRC's MCP server is
read-only, execution needs a Galaxy key and a human, and read and execute deliberately
live in different servers.

Measured susceptibility is the sharpest remaining gap. `AST_phenotypes` is returned per
isolate by `ncbi_pathogen_isolates` (`ncbi_lib/server.py:1545`), but it is not one of the
five fields `_pathogen_filter` builds a query from, so it cannot be filtered or counted
through any tool here. `PIPELINES.md:118` calls it "not reachable", which overstates it:
readable, not filterable, is the accurate limit. The source holds 1,548 measured
ciprofloxacin-resistant *E. coli* isolates, and this system cannot return that number.
