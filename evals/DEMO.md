# DEMO — what to show on Friday, in what order, what to say

Bobby has two MCP servers in the federation. This page is the script for the five
minutes that prove it. Every model the team could reach was measured.

**Open:** `evals/demo/index.html`. **Rebuild:** `python evals/build_demo.py`.

## Do this first, at the podium

```
python evals/build_demo.py
```

No model runs today, so the transcripts are final. Judge is still under repair this
morning, and each fix changes the scores on the page. Rebuild after the last fix. Read
the scores off the page, not off this file.

The two servers, one name each, used the same way on every slide:

| name here | file | port | tools |
|---|---|---|---|
| the GEO server | `mcp_servers/geo.py` | 8009 | `geo_search`, `geo_series`, `geo_resolve_accession` |
| the BRC server | `mcp_servers/brc_analytics.py` | 8008 | `brc_ena_runs`, `brc_ena_search`, `brc_ena_study`, `brc_federation_status` |

Two question sets. `BOBBY-LANES.md` holds 16 questions aimed at those seven tools.
`QUESTIONS.md` holds 15 for the whole federation.

---

## Beat 1 — both servers, every tool, every model

Read at 08:33 on 18 Sep. 34 models have BOBBY-LANES transcripts. 30 finished all 16
questions. The `anthropic/*` rows stopped at 9 when the network dropped. 522 records.

**687 calls landed on the seven tools. All seven were used.**

| tool | calls | server |
|---|---|---|
| `geo_search` | 180 | GEO |
| `geo_series` | 150 | GEO |
| `brc_ena_search` | 134 | BRC |
| `brc_ena_runs` | 120 | BRC |
| `brc_ena_study` | 37 | BRC |
| `geo_resolve_accession` | 34 | GEO |
| `brc_federation_status` | 32 | BRC |

GEO server 364 calls. BRC server 323 calls.

**Say:** both servers carry real traffic from every model, not incidental traffic.

---

## Beat 2 — the trap the BRC server defused

B12 asks what fraction of E. coli ENA runs mention carbapenem.

BRC's public `search_ena_keywords` returns an ENA HTTP 400 **as tool text** instead
of an error. A model reads that as "zero matches". It divides that by a real
denominator of 551,679 and reports a confident **0%**.

**27 models called `brc_ena_search` instead. None fell into the keyword tool.** Of the
other 4 records, 3 are transport faults. The fourth, `argo/gpto3mini`, made no tool
call and gave no figure.

**Say:** a broken upstream tool turns into a percentage with a decimal point. The BRC
server gave every model a path that does not do that.

---

## Beat 3 — the trap the models defused, which is a result about method

B4 asks how many ciprofloxacin studies exist for "Escherichai coli". The misspelling
is deliberate. The question expected a zero, and `zero_result_note` beside it.

That never happened.

**25 of 25 models that called `geo_search` sent `organism="Escherichia coli"`,
spelled correctly. None sent it as typed.** They got 37 GEO Series, not 0.

```
argo_gpt5  b04.jsonl
  CALL geo_search {"organism": "Escherichia coli", "term": "ciprofloxacin",
                   "entry_type": "gse", "max_results": 10}
  ANSWER "...with organism set to 'Escherichia coli'... a total_count of 37"
```

**Say:** you cannot test a server's zero handling through a model. The model repairs
the input before the tool sees it. State it as a finding, not as a failure.

---

## Beat 4 — the zero read as absence, where it is actually proven

This is the headline failure of the project. B4 is not the evidence. Two transcripts
are.

```
evals/runs/_archive-pre-matrix/argo_gpt4o/q02.jsonl
  CALL ncbi_pathogen_isolate_count {"organism": "Escherichia coli"}
  TOOL -> {"isolates": 0, "index_rows": 0}
  ANSWER "...a total of zero isolates matching this filter, despite correct
          organism naming."
```

The true figure is 581,464. NCBI has no group called `Escherichia coli`. The group is
`E.coli and Shigella`.

The second is from this run. `argo/gpt41nano` on B1 called only NDE's
`nde_search_datasets`, got 0, and answered *"No, there are no datasets specifically
labeled as E. coli expression studies about ciprofloxacin"*. The GEO server
returns 37 GEO Series for E. coli and ciprofloxacin, as in beat 3. That model
never called Bobby's server.

Both of Bobby's servers fix this class in code. `mcp_servers/geo.py:136`, attached at
`:603`, and `mcp_servers/brc_analytics.py:71`, attached at `:321`, add a
`zero_result_note` to every zero.

**Say:** the one real case in this run came from a model that skipped the GEO server.

---

## Read the red tile carefully

The first tile reads **Confident false statements**. On the 08:31 build it held 53.
It splits them: 1 wrong figure, 23 zero read as absence, 29 fabrication flags.

A wrong figure is checked against a pinned answer. The other two are judge's pattern
checks, and they over-fire. Every flagged cell was read by hand:

| flag | flagged | real | what the false ones were |
|---|---|---|---|
| wrong figure | 1 | 1 | — |
| fabrication | 29 | 1 | 13 taxonomy IDs, 5 correct figures split by the parser, then percentages, HTTP codes and roundings |
| zero read as absence | 23 | 1 | 15 answers named the tool that gave 0; 7 matched an unrelated phrase such as "no linked PubMed record" |

The three real ones:

- `argo/claudeopus5` Q13 said 94,336 where the answer is 93,260.
- `argo/gpto1` B13 made no tool call and stated *"2735 GEO Series ... 258939 SRA runs"*.
- `argo/gpt41nano` B1 read an NDE zero as absence, as in beat 4.

Each fabrication cell now shows the numbers judge could not trace. `fabricated: 511145`
is the taxonomy ID of E. coli K-12, not an invented figure.

**Say:** 3 real, not 53. The other 50 are reported to the owner of `judge.py`.

---

## The honest column — say this before anyone asks

The page prints **not asked** and **not scored** as two columns. They are opposite
facts. "Not asked" means the model was never reached. "Not scored" means the model
answered and judge did not grade it.

BOBBY-LANES on the 08:31 build: 522 records, 61 not asked, 29 not scored.

The not-scored records are our own bug. B8 as first written asked *"how many of
those"* with no antecedent. Judge refuses those records by name. `BOBBY-LANES.md` records
that six of the first nine models asked what "those" meant. B8 is now rewritten.

`argo_claudesonnet45` on QUESTIONS reads 15 **not asked**. Those transcripts were
overwritten on 17 Sep and are gone. That model cannot be ranked on QUESTIONS.

## What was never tested

- **Nobody opened the page in a browser.** The markup has a guard that was watched to
  fail first. The markdown twin at `evals/demo/model-matrix.md` was read.
- **No live calls.** The network is gone. The ENA figure for E. coli has since grown
  to 551,870; 551,679 is the 17 Sep reading.
- **The full chatbot has never run end to end here.**
- **Every dollar on the page is unverified.** Token counts are measured. The prices
  came from memory, not from a price page.
