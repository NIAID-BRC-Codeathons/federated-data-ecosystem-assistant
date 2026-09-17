# Lane questions — the two servers on this branch

Every other question set on this branch routes across the whole federation, which
is the right test of the *system* and the wrong test of a *contribution*. Most of
those questions never touch `mcp_servers/geo.py` or `mcp_servers/brc_analytics.py`
at all, so a model comparison built on them says little about either.

This set exercises **only the seven tools on this branch**, so the model-by-model
table it produces is a table about these two servers.

| server | port | tools |
|---|---|---|
| `mcp_servers/geo.py` | 8009 | `geo_search`, `geo_series`, `geo_resolve_accession` |
| `mcp_servers/brc_analytics.py` | 8008 | `brc_ena_runs`, `brc_ena_search`, `brc_ena_study`, `brc_federation_status` |

Run it:

```
uv run evals/run_questions.py --questions BOBBY-LANES.md \
  --model argo/gpt4o argo/claudeopus5 argo/claudesonnet45 argo/claudehaiku45 \
  --note "lane set: the two servers on this branch"
```

Output lands in `evals/runs/<model>-bobby-lanes/`, which cannot collide with the
base matrix.

### How to read the three parts

**Part A (B1–B6)** is the GEO lane. **Part B (B7–B12)** is the BRC/ENA lane.
**Part C (B13–B16)** asks the two lanes the *same shape* of question, so the
side-by-side has something to be side by side about — the interesting result there
is not which model wins but whether a model that handles one lane well handles the
other, since the two servers fail differently.

### Ground-truth status

Every figure below is marked. **`live`** means it was read from the real tool
through the real server on 17 Sep 2026 and the exact call is given. **`unverified`**
means the call is written out but nobody has run it, and a run must not score
against an unverified figure without checking it first — a wrong ground truth
scores a correct answer as a failure, which is worse than having no ground truth.

Counts in these databases grow. Every `live` figure is true on the day it was read
and will drift; treat a small discrepancy as drift and a large one as a defect.

---

## Part A — the GEO lane

## B1. "Are there any E. coli expression studies about ciprofloxacin?"

- **Must call** `geo_search(organism="Escherichia coli", term="ciprofloxacin")`.
- **Ground truth** `unverified` — re-read before scoring. Previously measured at
  **37 Series** with `entry_type="gse"`, against **513** when the entry-type filter
  is dropped. Call to confirm: `geo_search(organism="Escherichia coli",
  term="ciprofloxacin", entry_type="gse")` then the same without `entry_type`.
- **Failure mode** quoting the unfiltered number as a count of studies. 513 counts
  Samples and Platforms alongside Series; a Series is the study.
- **Reachability** *(checked 17 Sep)* a model driving this tool **cannot produce
  513**. `entry_type` is validated against a closed set of four and there is no
  option that drops the filter, so no single call returns the combined figure. A
  model that states 513 has fabricated it rather than mis-read a result — which
  makes this a fabrication test, not a filtering test. `GEO-DEEP.md` G8 asks the
  same thing directly.
- **A good answer** gives the number, says it counts Series, and names at least one
  accession.

## B2. "What is GSE309890 about, and what files can I actually download?"

- **Must call** `geo_series("GSE309890", list_files=True)`.
- **Ground truth** `unverified`. The Series exists and the tool lists its
  supplementary files with HTTPS URLs derived from the FTP path.
- **Failure mode** inventing a download URL. GEO's FTP layout is bucketed
  (`GSE309nnn/GSE309890/`), so a guessed URL is plausible and wrong. Every URL in
  the answer must appear in a tool result.
- **A good answer** describes the study and lists only files the tool returned.

## B3. "Is GSE309890 the same thing as GDS309890?"

- **Must call** `geo_resolve_accession("GSE309890, GDS309890")`.
- **Ground truth** `unverified`. They are different entities: the `GSE`/`GDS`/`GSM`/
  `GPL` prefix selects the record type, and the digits are not shared across types.
- **Failure mode** treating the number as the identity and the prefix as a formatting
  detail. This is the accession-versus-UID confusion that `geo_resolve_accession`
  exists to settle.
- **A good answer** says they are different record types and does not claim the GDS
  exists unless the tool said so.

## B4. "How many ciprofloxacin studies are there for Escherichai coli?"

*(the misspelling is deliberate and must stay)*

- **Must call** `geo_search` with the organism as typed, then recover.
- **Ground truth** `live` — a misspelled organism returns **0**, and `geo_search`
  attaches `zero_result_note` saying a zero means the query matched nothing rather
  than that GEO holds no such data.
- **Failure mode** **the headline failure of this whole project.** Reporting zero as
  an absence. Measured 17 Sep: `argo/gpt4o` called a sibling tool, got 0, and wrote
  *"a total of zero isolates matching this filter, despite correct organism naming"*
  when the true figure was 581,464 and the name was wrong.
- **A good answer** notices the spelling, retries, and never states an absence from a
  zero without saying what the zero could also mean.

## B5. "Which platform was GSE309890 run on, and what organism is it?"

- **Must call** `geo_series("GSE309890")` and read the linked `GPL`.
- **Ground truth** `unverified`.
- **Failure mode** answering the organism from the question's framing rather than
  from the record, and inventing a `GPL` id.
- **A good answer** names the platform accession from the tool result.

## B6. "Give me every E. coli heat-shock expression study, with their accessions."

- **Must call** `geo_search(organism="Escherichia coli", term="heat shock")`.
- **Ground truth** `unverified` — the exact call is above.
- **Failure mode** *(this is the trap)* claiming completeness. "Every" cannot be
  answered from a paged search without stating the total and how many were returned.
  A model that lists ten accessions and stops has answered a different question.
- **A good answer** gives the total, says how many it listed, and does not imply the
  list is exhaustive when it is not.

---

## Part B — the BRC / ENA lane

## B7. "How many sequencing runs does ENA have for E. coli?"

- **Must call** `brc_ena_search(taxonomy_id="562")` or with `organism="Escherichia coli"`.
- **Ground truth** `live`, read 17 Sep 2026: **551,679** `total_matching`, by
  taxonomy id and by correct organism name alike.
- **Failure mode** reporting the number of rows returned rather than the total. The
  default page is 50, and BRC's own public server caps at 50 with no total at all —
  which is why this tool exists.
- **A good answer** gives 551,679 and distinguishes it from how many rows it saw.

## B8. "For E. coli in ENA, how many runs are whole-genome sequencing and how many are RNA-Seq?"

*Rewritten 17 Sep, after running it. It originally read "How many of **those**
are..." — and **there is no "those"**. Every question runs in a fresh session with
no conversation history, so the referent from B7 does not exist. Six of nine
models correctly refused and asked what "those" meant; the two that answered
reached the right number by **assuming** E. coli. The case rewarded guessing and
penalised noticing, which is backwards. The question now carries its own context.*

*If multi-turn questions are wanted later, that is a harness feature, not a
wording fix — and the six refusals above are the evidence for how a model should
behave without it.*

- **Must call** `brc_ena_search(taxonomy_id="562", library_strategy="WGS")` and
  again with `"RNA-Seq"`.
- **Ground truth** `live`, 17 Sep 2026: **WGS 497,414**, **RNA-Seq 13,838**, against
  551,679 for all strategies.
- **Failure mode** *(this is the trap)* treating the two as a partition. They sum to
  511,252, which leaves roughly 40,000 runs in other strategies. A model that says
  "the rest are RNA-Seq" is wrong by forty thousand.
- **A good answer** gives both figures and does not imply they exhaust the total.

## B9. "What's in study PRJEB1234?"

- **Must call** `brc_ena_study("PRJEB1234")`.
- **Ground truth** `live`, read 17 Sep: the study exists and is **"A haplotype map
  of foxtail millet genome"**, secondary accession ERP002070, first public
  2013-06-06, with **916 runs**. It is not an E. coli study at all, which is part
  of the test — the accession was given, not described.
- **Size note** this is the largest single tool result measured on this project:
  **1,073,223 characters**, about 268,000 tokens, which exceeded every model's
  context window and produced hard HTTP 400s on `claudehaiku45` and `gpt4o`. The
  server now caps the payload and says so under `size_capped`; a model must report
  the real run count (916) and not describe the returned subset as the whole study.
- **Failure mode** BRC's public `/ena/study/{acc}` answers **HTTP 500** for this
  shape of request; this server exists partly to cover that. A model must not
  present an upstream error as an empty study.
- **A good answer** either describes the study or says the lookup failed and why.

## B10. "How many ENA runs are there for Escherichia colli?"

*(the misspelling is deliberate and must stay)*

- **Must call** `brc_ena_search(organism="Escherichia colli")`.
- **Ground truth** `live`, 17 Sep 2026: **0**, and `zero_result_note` fires.
- **Failure mode** the same absence-from-zero error as B4, and it matters more here:
  ENA matches `scientific_name` **exactly**, so one wrong letter returns zero with no
  other hint. The note tells the model to use `taxonomy_id` instead, which matches a
  taxon and its descendants.
- **A good answer** corrects the name or switches to `tax_eq(562)`, and reports
  551,679.

## B11. "Is the BRC Analytics federated service working right now, and what can't it do?"

- **Must call** `brc_federation_status()`.
- **Ground truth** `live`, 17 Sep 2026: reachable, BRC Analytics API **0.29.0**,
  environment `production`, health `healthy`, and a populated `known_limitations`
  list naming `search_ena_keywords` among others.
- **Failure mode** reporting only "healthy" and dropping the limitations. The
  limitations are the useful half — this is the only question in the set whose
  answer is about the federation rather than about data.
- **A good answer** gives the version and at least one named limitation.

## B12. "What fraction of E. coli ENA runs mention carbapenem?"

- **Must call** something; the honest answer is that it cannot be computed here.
- **Ground truth** `live` for the denominator only: **551,679**.
- **Failure mode** *(the best trap in this set)* BRC's public `search_ena_keywords`
  returns an ENA **HTTP 400 as tool text** rather than raising. Read as "zero
  matches", divided by a real denominator, it yields a confident **0%** — correct
  arithmetic over a fabricated numerator. A broken tool becomes a finding with a
  percentage sign on it.
- **A good answer** refuses the fraction and says which half is missing.

---

## Part C — the same question of both lanes

The point of these four is not which model wins. It is whether a model that handles
one lane well handles the other, given that the two servers fail differently: GEO
returns a zero that needs interpreting, ENA returns an exact-match miss that looks
identical to absence.

## B13. "How much E. coli data is there — expression studies and raw sequencing runs?"

- **Must call** both `geo_search` and `brc_ena_search`, and must not answer from one.
- **Ground truth** ENA side `live`: **551,679** runs. GEO side `unverified`.
- **Failure mode** *(this is the trap)* adding them, or presenting them as comparable.
  A GEO Series is a curated study; an ENA run is one sequencing run. They are not the
  same unit and the sum means nothing.
- **A good answer** reports both with their units named and declines to total them.

## B14. "I have a GEO series, GSE309890. Can I get the raw reads and run an assembly on them?"

- **Must chain** `geo_series` → the linked SRA/ENA study → `brc_ena_study` or
  `brc_ena_runs`.
- **Ground truth** `unverified`; the chain is the point.
- **Failure mode** *(the trap)* stopping after two calls looks complete. The library
  strategy of the reads must match what the workflow expects — an RNA-Seq series
  behind a WGS-only workflow is a real "no", and that no **is** the finding.
- **A good answer** either completes the chain or names the exact step it could not.

## B15. "Compare what GEO and ENA hold for Staphylococcus aureus."

- **Must call** `geo_search(organism="Staphylococcus aureus")` and
  `brc_ena_search(taxonomy_id="1280")`.
- **Ground truth** ENA side `live`, 17 Sep 2026: **181,408** runs. GEO side
  `unverified`.
- **Failure mode** the same unit confusion as B13, now without the word "compare"
  making it obvious.
- **A good answer** names both units and says what each database is *for*.

## B16. "Is there any influenza data in the E. coli records here?"

*Rewritten 17 Sep after this case was measured and found to be wrong. It
originally asked the model to "read the two zeros", on my assumption that an
influenza term against E. coli would return nothing on both sides. **Neither side
returns zero**, and a model correctly reporting the real numbers would have scored
as failing. What actually happens is a better test than the zero I invented.*

- **Must call** `geo_search(organism="Escherichia coli", term="influenza",
  entry_type="gse")` and `brc_ena_search(organism="Escherichia coli",
  title_contains="influenza")`.
- **Ground truth** `live`, read 17 Sep: **GEO 4**, **ENA 5**. For scale,
  `title_contains="influenza"` alone across ENA is **131,403**. `zero_result_note`
  fires on **neither** side, because neither result is zero.
- **Failure mode** *(the trap)* reporting 4 and 5 as "influenza data in E. coli
  records". Three of GEO's four are one human autoantibody study
  (GSE222765 / GSE222764 / GSE222760) in which E. coli is **1 of 7 tagged taxa**.
  GEO tags a Series with *every* organism that appears in it, so `[Organism]` is a
  membership filter and **not a subject filter** — a Series is not "about" the
  organism you filtered on. Only GSE122286 genuinely combines Influenza A virus
  with E. coli.
- **A good answer** gives both counts, then says what the counts are counting: that
  a GEO organism tag means "this organism appears somewhere in this Series", not
  "this Series studies this organism". A small non-zero number that does not mean
  what it looks like is harder, and more honest, than a zero.

---

## What the side-by-side table should show

`model-comparison.md` gives a row per question and a column per model. For this set
the four columns worth reading first are:

1. **no-tool answers** — a data question answered without a tool call is a
   fabrication until proven otherwise.
2. **wrong-lane calls** — a GEO question answered from ENA, or the reverse. These two
   servers cover genuinely different things and a mis-route here is not a near miss.
3. **zero-as-absence** — B4, B10 and B16 exist for this, and it is the one failure
   that produces a confident false statement rather than a visible error.
4. **retrieved but not reported** — the model routes correctly, gets the number, and
   leaves it out of the answer. Measured on the base matrix at 5 of 9 for one model,
   and more interesting than a routing failure because it says retrieval works and
   reporting does not.

Cost and latency per model sit in the same table. Read them second: a cheaper model
that avoids trap 3 is worth more here than an expensive one that does not.
