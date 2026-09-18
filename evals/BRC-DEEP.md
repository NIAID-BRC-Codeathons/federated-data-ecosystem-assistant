# BRC deep set — the parts of the BRC Analytics server the lane set does not reach

`BOBBY-LANES.md` routes to BRC six times and checks whether the model reads the
result correctly. That is the right test of the system. It is not coverage of the
server, and one of the gaps is the server's whole reason for existing.

| gap | why it matters |
|---|---|
| **`offset` is used zero times** | `brc_ena_runs` exists *because* BRC's public `search_ena` stops at 50 with no total. Paging past 50 is the justification for this server and no question tests it. |
| species vs strain taxid | `562` and `511145` differ by **26×**, and the docstring warns about it |
| `brc_ena_runs` vs `brc_ena_search` | two tools over the same data; nothing tests whether a model picks the right one |
| `limit` at its bounds | accepts 1–1000; never varied |
| `title_contains` | a substring filter on the title, not a subject filter |

`tests/test_brc_analytics_tools.py` (26 tests) covers this at the function level.
What is missing is whether a **model** chooses correctly.

Run it:

```
uv run evals/run_questions.py --questions BRC-DEEP.md \
  --parallel 6 --excerpt 4000 --model <models> --note "BRC deep: paging, taxid, routing"
```

ENA is a different host from NCBI, so this set does **not** spend the shared 3
req/sec venue budget.

---

## C1. "Get me 200 E. coli sequencing runs with their download links."

- **Must call** `brc_ena_runs(taxonomy_id="562", limit=200)`.
- **Ground truth** `live`, read 17 Sep before the payload cap: returns **200** runs,
  `total_in_ena` **551,679**. **Since the cap landed, the server trims any result
  over 40,000 characters and says so under `size_capped`**, so a correct call now
  returns fewer than 200 rows plus that note. Measured from transcripts: about 934
  characters per run, so roughly 42 rows fit.
- **Failure mode** *(the trap, and the one that justifies this server)* routing to
  the federated `search_ena`, which **stops at 50**, reports only `has_more: true`,
  and never gives a total. A model that returns 50 and says "here are the runs" has
  under-delivered by 75% and cannot tell you it did.
- **A good answer** asks for 200, reports that the response was capped, gives how
  many rows came back and the real total of 551,679, and does not describe the
  returned rows as the 200 it asked for. That is a sharper test than counting to
  200: the `size_capped` note literally says the records "are NOT the whole answer
  and must not be described as though they were".

## C2. "Show me runs 51 to 100 for E. coli — I already have the first 50."

- **Must call** `brc_ena_runs(taxonomy_id="562", limit=50, offset=50)`.
- **Ground truth** `live`: `offset=0` begins `ERR10016945, ERR10016947,
  ERR10016958…`; `offset=50` begins `ERR10017217, ERR10017220, ERR10017222…`. The
  two pages are **disjoint**.
- **Failure mode** ignoring `offset` and re-returning the first page. The user
  cannot tell — the records are real, they are simply the ones already held. This is
  the only question in the whole corpus where the *right* answer and the *wrong*
  answer are both well-formed lists of genuine accessions.
- **A good answer** returns a page that does not overlap the first.

## C3. "How many sequencing runs are there for E. coli K-12 MG1655?"

- **Must call** `brc_ena_search(taxonomy_id="511145")` — the **strain**, not the
  species.
- **Ground truth** `live`, 17 Sep: **20,925** for `511145`, against **551,679** for
  `562`. A factor of **26**.
- **Failure mode** *(the trap)* answering with the species figure. K-12 MG1655 is a
  specific laboratory strain; E. coli is the species. Both are "E. coli" in
  conversation and they are different taxids, and the tool's own docstring warns
  about exactly this.
- **A good answer** uses the strain taxid and says which one it counted.

## C4. "How many E. coli studies mention carbapenem in the title?"

- **Must call** `brc_ena_search(taxonomy_id="562", title_contains="carbapenem")`.
- **Ground truth** `live`, 17 Sep: **9,759** — and those are **runs, not studies**.
  For comparison, `title_contains="resistance"` is 48,421 and `"plasmid"` is 4,421.
- **Failure mode** *(two traps, and the second is subtler)* First, reporting runs as
  studies: the unit is `read_run`, and the same ENA query counted as studies is
  roughly 500× smaller. Second, `title_contains` is a **substring match on the
  title**, not a subject filter — a study titled "…without carbapenem resistance"
  matches.
- **A good answer** gives the number, names the unit as runs, and says the filter is
  a title substring.

## C5. "Which should I use to count E. coli runs — `brc_ena_runs` or `brc_ena_search`?"

- **Must call** either, and explain the difference.
- **Ground truth** `live`: both report **551,679**. `brc_ena_runs` needs a
  `taxonomy_id` and returns `total_in_ena` with full run records including download
  links; `brc_ena_search` accepts `organism` **or** `taxonomy_id` plus
  `library_strategy` and `title_contains`, and returns `total_matching`.
- **Failure mode** asserting a difference in the numbers where there is none, or
  claiming one is "more accurate". They agree; they differ in **what you can filter
  by** and **what comes back**.
- **A good answer** says they agree on the count and distinguishes them by filters
  and payload, not by accuracy.

## C6. "Give me one E. coli run. Just one."

- **Must call** `brc_ena_runs(taxonomy_id="562", limit=1)`.
- **Ground truth** `live`: returns exactly 1, with `total_in_ena` still 551,679.
- **Failure mode** returning the default 50 because `limit` was never set, or
  reporting `total_in_ena` as though it were the number returned. `returned` and
  `total_in_ena` are different fields and this question separates them.
- **A good answer** returns one run and does not confuse the two numbers.

## C7. "Is BRC Analytics reliable right now? I'm about to depend on it."

- **Must call** `brc_federation_status()`.
- **Ground truth** `live`, 17 Sep: reachable, **BRC Analytics API 0.29.0**,
  environment `production`, health `healthy`, and a populated `known_limitations`
  list naming `search_ena_keywords` among others.
- **Failure mode** *(the trap)* answering "yes, it's healthy" and stopping.
  `healthy` and `reliable` are different claims: the service is up **and** three of
  its tools are known-broken, which is precisely what someone about to depend on it
  needs told.
- **A good answer** reports healthy *and* enumerates the known limitations.

## C8. "How many E. coli RNA-Seq runs are there, as a share of all E. coli runs?"

- **Must call** `brc_ena_search(taxonomy_id="562", library_strategy="RNA-Seq")` and
  again with no strategy.
- **Ground truth** `live`, 17 Sep: RNA-Seq **13,838** of **551,679** — about
  **2.5%**. WGS is 497,414, so WGS + RNA-Seq is 511,252 and the two **do not
  partition** the total: roughly 40,000 runs are in neither.
- **Failure mode** computing the share against WGS instead of against the total, or
  stating that the remainder is WGS.
- **A good answer** gives 2.5% against the real denominator and does not imply the
  rest is WGS.
