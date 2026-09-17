# Friday demo — the two servers, every model, side by side

Bobby built two data servers and measured them against three models on 15 questions.
The measurement found a fault. That fault made one model state a confident false
number. Both of his servers already fix it in code. That is the demo. It runs about
five minutes and needs no live network.

Open `evals/demo/index.html` in a browser.

## What to show, in order

### 1. The two servers (30 seconds)

Say what they are before naming them.

| What it does | Name | Port | Tools |
|---|---|---|---|
| Finds gene-expression studies in NCBI GEO | `mcp_servers/geo.py` | 8009 | `geo_search`, `geo_series`, `geo_resolve_accession` |
| Finds sequencing runs in ENA, with real totals | `mcp_servers/brc_analytics.py` | 8008 | `brc_ena_runs`, `brc_ena_search`, `brc_ena_study`, `brc_federation_status` |

They fail in different ways, and that is why both are here. A GEO search returns a zero
that someone has to interpret. An ENA search matches the organism name letter for
letter. One wrong letter returns nothing, and that looks exactly like an empty database.

### 2. The zero that was not a zero (2 minutes — this is the demo)

Three beats.

**A tool reported "no match" as a quantity.** The model `argo/gpt4o` asked another team's
tool for a count:

```
ncbi_pathogen_isolate_count(organism="Escherichia coli")
  -> {"summary": "0 distinct isolates", "isolates": 0, "index_rows": 0}
```

It then wrote: *"a total of zero isolates matching this filter, despite correct organism
naming."* The model was not inventing anything. It reported what the tool told it. NCBI
does not keep a group called `Escherichia coli`; the group is called `E.coli and Shigella`.
The filter matched nothing, and the tool published that as the number zero.

**The same model asked again and got it right.** Later the same day, same tool, one
changed word:

```
ncbi_pathogen_isolate_count(organism="E.coli and Shigella")
  -> {"summary": "581,464 distinct isolates", "isolates": 581464}
```

Say plainly that the older transcript carries no `code_sha`, so nobody knows which
version of that server produced the zero. The two runs are not scored against each other.
The filter value is visible in both files, and that is the whole finding.

**One model saw the trap coming.** On question 13, `argo/claudeopus5` listed the
controlled vocabulary before it filtered on that vocabulary. It said why: *"that
vocabulary is matched literally — a misspelled symbol returns zero isolates rather than
an error."*

**Then land it on Bobby's servers.** Both of them attach a note to every zero they return,
so no model has to be clever:

```python
# mcp_servers/brc_analytics.py:71
"This returned 0 matches, which means the query matched nothing -- NOT that ENA
 holds no such data. ENA matches scientific_name EXACTLY, so a misspelling or a
 strain-level name returns zero. Check the spelling, or use taxonomy_id instead..."
```

Same guard in the GEO server at `mcp_servers/geo.py:136`, attached at line 603.

### 3. The side-by-side (1 minute)

Question down the page, model across. Scroll it; do not read it aloud. Point at two rows.

**Row Q2 — all three models correct.** This is the 581,464 question. Every model that
ran it got it right once the filter value was right.

**Row Q13 — one row, two different failures.** The question asks how many methicillin-
resistant E. coli strains there are.

- `argo/claudeopus5` is marked **critical**. It dodged the vocabulary trap and got the
  581,464 denominator right. It still gave the wrong answer. **94,336** counts isolates
  with `mecA` **or** `mecC`. The question asks for `mecA` alone, which is **93,260**.
- `argo/claudesonnet45` is marked **retrieved, not reported**. It called the right tool
  and got the number. It left the number out of its answer. Retrieval works; reporting
  does not.

That row is the honest note. Avoiding the trap is not the same as being right.

**Then scroll to the per-model table and say this.** All seven tools were called, 26
times in total. The lane column in the matrix marks only 3 of the 15 questions as GEO
and none as ENA. Those two facts do not conflict. The lane says what a question is
*about*. The question set fixes it, so a question cannot drift. The per-model
table says what the models actually *did*. His servers were reached far more often than
the lane column alone suggests.

The verdicts above are the ones on the page as generated at the stamp in its header.
Runner is still landing models and judge is still rescoring, so rebuild before Friday
and re-check these two rows. `argo/gpt4o` has 2 of 15 questions so far; its empty cells
read "not run", which is not a score.

### 4. What the numbers cannot say (45 seconds)

Say this out loud rather than letting the table imply otherwise.

- **`argo/claudesonnet45` cannot be ranked.** It lost 6 of its 15 questions to an empty
  reply. Each one shows 37,571 input tokens, which is the tool list alone. One round
  trip, no error, no denial.
  It never got past the first turn. `argo/claudeopus5` lost 0 of 15 in the same run.
  That is the gateway, not the model, so those 6 are held out rather than scored zero.
- **Every dollar is an estimate nobody checked.** Token counts are measured. The prices
  were written from memory, never read off a price page. Argo billed none of it.
- **The full chatbot has never run end to end here.** These are per-question runs against
  the servers. Nobody has driven the whole system from one plain-language request.
- **Question 11 was run without BV-BRC's 18 tools.** It measures a source being down, not
  whether the system can use that source.
- **The 16 questions written for these two servers have not been run yet.** That set is
  `evals/BOBBY-LANES.md`. Everything on the page comes from `evals/QUESTIONS.md`, which
  asks about the whole federation. When the other set runs, rebuild and the page will
  grow a second section on its own.

## What not to claim

Do not add GEO studies to ENA runs. A GEO Series is a curated study; an ENA run is one
sequencing run. Any total across the two is meaningless, and the generator raises an
error rather than print one.

## Regenerate

```
python evals/build_demo.py              # rewrites evals/demo/index.html and model-matrix.md
python evals/build_demo.py --self-test  # 12 guards, each observed failing before it was trusted
```

The page rebuilds from whatever transcripts are in `evals/runs/` at the time.
