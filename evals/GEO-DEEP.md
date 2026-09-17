# GEO deep set — the parts of the GEO server the lane set does not reach

`BOBBY-LANES.md` tests GEO the way a user meets it: six questions that route to
`geo_search`, `geo_series` and `geo_resolve_accession` and check whether the model
interprets what comes back. That is the right test of the *system*.

It is not full coverage of the *server*. Four things `mcp_servers/geo.py` does are
never exercised by a model in that set, and each has a trap in it:

| gap | why it matters |
|---|---|
| `entry_type` is never varied | it takes four values and the counts differ by orders of magnitude |
| `max_results` is never varied | "every study" is asked in B6 but nothing tests what paging does to the answer |
| `geo_series(list_files=False)` | the cheap path, never chosen by a model |
| `geo_resolve_accession` on GSM / GPL | B3 only ever sees GSE and GDS |

The offline suite (`tests/test_geo_tools.py`, 52 tests) covers all of this at the
*function* level. What is missing is whether a **model** picks the right option,
which is a different question and the one this file asks.

Run it:

```
uv run evals/run_questions.py --questions GEO-DEEP.md \
  --parallel 6 --model <models> --note "GEO deep: entry_type, paging, resolution"
```

Output lands in `evals/runs/<model>-geo-deep/`.

---

## G1. "How many E. coli Samples are in GEO, as opposed to Series?"

- **Must call** `geo_search(organism="Escherichia coli", entry_type="gsm")` and
  compare with `entry_type="gse"`.
- **Ground truth** `unverified` — run both before scoring.
- **Failure mode** *(the trap)* answering with the Series count because that is the
  default. `entry_type` defaults to `"gse"`, so a model that never sets it gets
  Series and may not notice it was asked about something else. A Sample is one
  assayed specimen; a Series is a study containing many.
- **A good answer** gives both numbers and names which unit each counts.

## G2. "Give me the curated DataSets for E. coli."

- **Must call** `geo_search(organism="Escherichia coli", entry_type="gds")`.
- **Ground truth** `unverified`.
- **Failure mode** *(the trap)* treating `gds` as a synonym for `gse`. GEO's curated
  DataSet tier is a **small, frozen subset** — most Series never get one, and
  curation largely stopped years ago. A model that reports a low count as "there is
  little E. coli data in GEO" has drawn a conclusion about the database from an
  artefact of a curation programme.
- **A good answer** gives the count and says what the `gds` tier is, rather than
  treating a small number as evidence of absence.

## G3. "What platform types has E. coli expression been measured on?"

- **Must call** `geo_search(organism="Escherichia coli", entry_type="gpl")`.
- **Ground truth** `unverified`.
- **Failure mode** answering from background knowledge — microarray, RNA-seq —
  without calling anything. A platform question is answerable from the record and a
  model that recites the general history of expression assays has not used the tool.
- **A good answer** is grounded in returned `GPL` records.

## G4. "Give me all 500 E. coli heat-shock studies."

*The premise is deliberately wrong; B6 measured 50.*

- **Must call** `geo_search(organism="Escherichia coli", term="heat shock")` and
  contradict the number in the question.
- **Ground truth** `live`, read 17 Sep: **50** Series.
- **Failure mode** *(the trap)* accepting the premise. `max_results` defaults to 10,
  so a model can return ten records and describe them in a way consistent with there
  being 500. Nothing in the answer will look wrong.
- **A good answer** says the real total is 50, not 500, and does not imply its ten
  returned rows are all of them.

## G5. "Just tell me what GSE309890 is — I don't need the files."

- **Must call** `geo_series("GSE309890", list_files=False)`.
- **Ground truth** `live`: the Series exists and has exactly **1** supplementary
  file (~510 KB), so the difference is visible but small.
- **Failure mode** ignoring the explicit instruction and listing files anyway. The
  parameter exists because file listing is a second network call; a model that never
  varies it is spending a request the user declined.
- **A good answer** describes the study and does not enumerate files.

## G6. "Is GSM9284462 a study or a sample, and what is it part of?"

- **Must call** `geo_resolve_accession("GSM9284462")`.
- **Ground truth** `unverified` — the prefix says Sample.
- **Failure mode** *(the trap)* treating the digits as the identity. The server maps
  `GSE`→UID prefix 2, `GSM`→3, `GPL`→1, so the same digits under different prefixes
  are **different records**, and a model that strips the prefix resolves the wrong
  one. This is the accession-versus-UID confusion the tool exists to settle.
- **A good answer** identifies it as a Sample from the prefix, not from the number.

## G7. "Resolve GPL24659 and GSE309890 together — how do they relate?"

- **Must call** `geo_resolve_accession("GPL24659, GSE309890")`.
- **Ground truth** `live`: `GPL24659` is the platform for `GSE309890` (read via
  `geo_series` on 17 Sep).
- **Failure mode** resolving one and inferring the other, or asserting a
  relationship the resolver did not return. A platform and a series that resolve
  successfully are not thereby linked.
- **A good answer** resolves both and states the relationship only if a tool result
  carries it.

## G8. "GEO says 513 E. coli ciprofloxacin records but I only see 37. Which is right?"

- **Must call** `geo_search` across entry types and explain the discrepancy.
- **Ground truth** `live`: **37** Series; **513** is the figure with the entry-type
  filter dropped, i.e. Series + Samples + Platforms + DataSets together.
- **Failure mode** *(the trap, and it is a subtle one)* **the tool cannot reproduce
  513.** `entry_type` is validated against a closed set of four and there is no
  option that drops the filter, so no single call a model can make returns 513. A
  model that "confirms" 513 has fabricated it; a model that says the tool cannot
  produce that number is right.
- **A good answer** gives 37 for Series, explains that 513 counts several record
  types together, and says the tool cannot return the combined figure directly.
