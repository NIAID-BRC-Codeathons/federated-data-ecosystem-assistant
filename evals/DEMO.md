# DEMO — what to show on Friday, in what order, what to say

Bobby has two MCP servers in the federation. This page is the script for the five
minutes that prove it. Every model the team could reach was measured.

**Open:** `evals/demo/index.html`. **Rebuild:** `python evals/build_demo.py`.

## Do this first, at the podium

```
python evals/build_demo.py
```

Every number below carries the time it was read. The run is still landing. Runner
added two models in the 13 seconds between two of my rebuilds. Read the numbers off
the page, not off this file.

The two servers, one name each, used the same way on every slide:

| name here | file | port | tools |
|---|---|---|---|
| the GEO server | `mcp_servers/geo.py` | 8009 | `geo_search`, `geo_series`, `geo_resolve_accession` |
| the BRC server | `mcp_servers/brc_analytics.py` | 8008 | `brc_ena_runs`, `brc_ena_search`, `brc_ena_study`, `brc_federation_status` |

Two question sets. `BOBBY-LANES.md` holds 16 questions aimed at those seven tools.
`QUESTIONS.md` holds 15 for the whole federation.

---

## Beat 1 — both servers, every tool, every model

Read at 16:35:39 on 17 Sep. 18 models have BOBBY-LANES transcripts. 12 finished all
16 questions; 6 were still in flight. 234 records, 232 answered, 2 transport faults.

**343 calls landed on the seven tools. All seven were used.**

| tool | calls | server |
|---|---|---|
| `geo_search` | 92 | GEO |
| `geo_series` | 77 | GEO |
| `brc_ena_runs` | 64 | BRC |
| `brc_ena_search` | 59 | BRC |
| `geo_resolve_accession` | 18 | GEO |
| `brc_ena_study` | 17 | BRC |
| `brc_federation_status` | 16 | BRC |

GEO server 187 calls. BRC server 156 calls. Neither is a side path that one model
touched once.

**Say:** both servers carry real traffic from every model, not incidental traffic.

---

## Beat 2 — the trap the BRC server defused

B12 asks what fraction of E. coli ENA runs mention carbapenem.

BRC's public `search_ena_keywords` returns an ENA HTTP 400 **as tool text** instead
of an error. A model reads that as "zero matches". It divides that by a real denominator
of 551,679 and reports a confident **0%**. The arithmetic is correct and
the numerator is fiction.

**14 of 14 models with a B12 record called `brc_ena_search` instead. No exceptions.**

**Say:** a broken upstream tool turns into a percentage with a decimal point. The BRC
server gave every model a path that does not do that.

---

## Beat 3 — the trap the models defused, which is a result about method

B4 asks how many ciprofloxacin studies exist for "Escherichai coli". The misspelling
is deliberate. The question expects a zero, and expects `geo_search` to attach
`zero_result_note` so the zero cannot read as absence.

That never happened.

**17 of 17 models sent `organism="Escherichia coli"`, spelled correctly. Zero sent
it as typed.** Every one got 37 GEO Series. Only 2 of 16 mentioned the spelling
anywhere in the answer.

```
argo_gpt5  b04.jsonl
  CALL geo_search {"organism": "Escherichia coli", "term": "ciprofloxacin",
                   "entry_type": "gse", "max_results": 10}
  ANSWER "...with organism set to 'Escherichia coli'... a total_count of 37"
```

**Say:** you cannot test a server's zero handling through a model. The model repairs
the input before the tool sees it. The trap was written for the tool and defused one
layer upstream. State it as a finding, not as a failure.

---

## Beat 4 — the zero read as absence, where it is actually proven

This is the headline failure of the project. It is real, and B4 is not the evidence.

```
evals/runs/_archive-pre-matrix/argo_gpt4o/q02.jsonl
  CALL ncbi_pathogen_isolate_count {"organism": "Escherichia coli"}
  TOOL -> {"isolates": 0, "index_rows": 0}
  ANSWER "...a total of zero isolates matching this filter, despite correct
          organism naming."
```

The true figure is 581,464. NCBI keeps no group called `Escherichia coli`; the group
is `E.coli and Shigella`. The same model, same tool, same day, with the right filter
value, returned 581,464.

Both of Bobby's servers already fix this class in code. `mcp_servers/geo.py:136`,
attached at `:603`, and `mcp_servers/brc_analytics.py:71`, attached at `:321`, add a
`zero_result_note` to every zero.

**Say:** the fix is in the code and it is readable. The proof is the archived
transcript, not B4.

---

## The honest column — say this before anyone asks

**234 BOBBY-LANES records are not scored.** Judge discovers all of them and then
refuses them: question set B has no rubric in `judge.py`. Measured 16:34 — 222
`no-rubric` skips, 0 B rows scored. Judge's own refusal text calls it "a gap in
`judge.py`, not a model failure".

The page prints **not asked** and **not scored** as two separate columns. They are
opposite facts. "Not asked" means the model was never reached. "Not scored" means the
model answered and nobody has graded it. An empty score column here means the second
one.

`argo_claudesonnet45` on QUESTIONS reads 15 **not asked**. Those transcripts were
overwritten at 16:07 and are gone. That model cannot be ranked on QUESTIONS.

## What was never tested

- **Nobody opened the page in a browser.** The markup has a guard that was watched to
  fail first. The markdown twin at `evals/demo/model-matrix.md` was read.
- **No live calls.** Not to NCBI, not to ENA, not to any running server.
- **The full chatbot has never run end to end here.**
- **Every dollar on the page is unverified.** Token counts are measured. The prices
  came from memory, not from a price page. The page says so wherever a dollar appears.
