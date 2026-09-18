# DEMO — what to show on Friday, in what order, what to say

Bobby has two MCP servers in the federation. This page is the script for the five
minutes that prove it. Every model the team could reach was measured.

Bobby presents from a separate page built from direct measurements:
https://claude.ai/artifact/VL6hvVMzgbn8wwmUuMePGE. The page below is the drill-down
behind it.

**Open:** `evals/demo/index.html`. **Rebuild:** `python evals/build_demo.py`.

## Do this first, at the podium

```
python evals/build_demo.py
```

The transcripts are final. Judge's owner may still change a check this morning.
Rebuild just before you present.
Read the scores off the page, not off this file.

One name per server, used on every slide:

| name here | file | port | tools |
|---|---|---|---|
| the GEO server | `mcp_servers/geo.py` | 8009 | `geo_search`, `geo_series`, `geo_resolve_accession` |
| the BRC server | `mcp_servers/brc_analytics.py` | 8008 | `brc_ena_runs`, `brc_ena_search`, `brc_ena_study`, `brc_federation_status` |

Two question sets. `BOBBY-LANES.md` holds 16 questions aimed at those seven tools.
`QUESTIONS.md` holds 15 for the whole federation.

---

## Beat 1 — both servers, every tool

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

**Say:** 29 of 34 models used both servers. The other 5 hit a transport fault or
the network drop first.

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
server gave the models a path that does not do that.

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

The first tile reads **Confident false statements**. On the 08:44 build it held 24:
1 wrong figure and 23 zero read as absence.

| check | in the tile | flagged | real, read by hand |
|---|---|---|---|
| wrong figure | yes | 1 | 1 |
| zero read as absence | yes | 23 | 1 |
| fabrication | no, reads "check by hand" | 30 | 1 |

A wrong figure is checked against a pinned answer. The other two are judge's pattern
checks. I read all 23 zero cells. In 15, the answer named the tool that returned 0. In
7, judge matched an unrelated phrase such as "no linked PubMed record".

Runner read all 30 fabrication flags. Most were taxonomy IDs or correct figures the
number parser split. Those cells read **check by hand**, with the
numbers judge could not trace. `check by hand: 511145` is the taxonomy ID of E. coli
K-12. Where the answer's figure is right, the cell reads **figure correct** first.

The three real ones:

- `argo/claudeopus5` Q13 said 94,336 where the answer is 93,260.
- `argo/gpto1` B13 made no tool call and stated *"2735 GEO Series ... 258939 SRA runs"*.
  Its cell reads `no tool call, untraced: 2735, 258939`.
- `argo/gpt41nano` B1 read an NDE zero as absence, as in beat 4.

**Say:** 3 real, not 24. The zero check is reported to the owner of `judge.py`.

---

## The honest column — say this before anyone asks

The page prints **not asked** and **not scored** as two columns. They are opposite
facts. "Not asked" means the model was never reached. "Not scored" means the model
answered and judge did not grade it.

BOBBY-LANES on the 08:31 build: 522 records, 61 not asked, 29 not scored.

The not-scored records are our own bug. B8 as first written asked *"how many of
those"* with no antecedent. Judge refuses those records by name. `BOBBY-LANES.md` records
that six of the first nine models asked what "those" meant. B8 is now rewritten.

`argo_claudesonnet45` cannot be ranked on QUESTIONS. Its 15 transcripts were
overwritten on 17 Sep.

## What was never tested

- **Nobody opened the page in a browser.** The markup has a guard that was watched to
  fail first.
- **No live calls.** The network is gone. The ENA figure for E. coli has since grown
  to 551,870; 551,679 is the 17 Sep reading.
- **The full chatbot has never run end to end here.**
- **Every dollar on the page is unverified.** Token counts are measured. The prices
  came from memory, not from a price page.
