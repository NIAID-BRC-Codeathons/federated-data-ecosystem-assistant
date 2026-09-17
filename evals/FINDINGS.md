---
title: Findings — what the model matrix actually says
date: 2026-09-17
type: analysis
project: codeathon
owner: analyst
status: Part 1 pre-registered 14:30; Part 2 six findings on the pre-matrix archive, awaiting a re-run on 39bb6ad
---

# Findings

Two documents already say the tools are right (`README.md` — 13/14 against the
documented call) and whether each transcript routed, fabricated or refused
(`judge-report.md`). Neither says what **differs between models and why**. That is
this file.

It is in two parts and the order is the point. **Part 1 is the scoring design, written
before any answer text was read**, so the analysis cannot be fitted to the results.
**Part 2 is the results**, added as models finish, with contradictions given their own
headings rather than buried.

---

## What I had seen when Part 1 was written

Stated so the pre-registration can be checked rather than believed.

At 14:20 on 17 Sep, 13 transcripts existed under `evals/runs/` across three models
(`argo_claudeopus5`, `argo_claudesonnet45`, `argo_gpt4o`). Before writing Part 1 I read:

- the **structure** of `evals/runs/argo_claudeopus5/q01.jsonl` — the line shapes, to learn
  the schema;
- the **summary metrics only** for all 13 — calls, round trips, tokens, TTFT, cost,
  answer length — printed as a table by a throwaway one-liner;
- `QUESTIONS.md`, `PIPELINES.md`, `README.md`, `judge.py`, `run_questions.py`, and the
  header of `judge-report.md`.

I did **not** read the answer text of any transcript. Three numbers from the metrics
table are quoted in Part 1 as measured anchors, each labelled as such.

---

# Part 1 · The scoring design

## The rules that govern every number below

**No composite score.** Nine dimensions, reported separately. Any weighting that
collapses them is a judgement about what this system is *for*, and that judgement
belongs to the team, not to a script. A reader who wants a ranking picks the dimension
that matches their use and reads that column.

**Fabrication vetoes.** A model with a confirmed fabrication — Judge's `fabricated`,
not its weaker `unmatched` — is not recommended for the demo whatever else it scores.
Nothing trades against this.

**Nothing is a real difference until it exceeds repeat spread.** Queue item 4 is
`--repeat 3` on five models. Until it lands, every cross-model gap in Part 2 is labelled
*unreplicated*. A two-call difference between models means nothing if one model varies
by three calls against itself. This commitment is the one most likely to delete a
finding I would otherwise enjoy, which is why it is made in advance.

**Every number carries its question, its model and its transcript path.** A number
without those three is not written down.

## The dimensions

### D0 · Completion — did the row produce an answer at all

**Computed from** `answer_chars`, `denied`, `error`, `tool_call_count`.

This gates everything else. An empty answer cannot be scored for routing or refusal
quality, and averaging it in as a zero would punish or flatter a model for the wrong
reason. It gets its own column and is never folded into the rest.

**Hypothesis:** completion is a transport and harness property, not a model property.
Empty answers and denials cluster by *provider route*, not by model tier.

**Measured anchor, already in hand** — `evals/runs/argo_claudesonnet45/q01.jsonl`:
`tool_call_count: 0`, `output_tokens: 0`, `answer_chars: 0`, `ttft_s: null`,
`elapsed_s: 2.7`, `error: null`, `denied: false`, `list_cost_usd: 0.0991`. A silent
empty reply that spent 33,028 input tokens and raised nothing. If this recurs on Claude
rows and not GPT rows, D0's hypothesis holds and the finding is about Argo's Claude
streaming path, not about Sonnet. **This is the one dimension where a bad score may not
be the model's fault at all**, and Part 2 must not let it be read that way.

### D1 · Routing — did it reach a source that can answer the question

**Computed from** `tools_in_order` against the chains in `QUESTIONS.md`, encoded in
`analyze.py`. Three measures, because a single "routed" flag hides the interesting split:

| measure | definition |
|---|---|
| `first_ok` | the **first** tool called is in the expected set for that question |
| `reached` | **any** expected tool was called — this is Judge's `routed` |
| `off_source` | a tool from a server that cannot answer this question was called at all |

`analyze.py` computes `reached` independently of `judge.py`, and the two are compared.
**A disagreement is reported, not resolved by preference.** Two implementations of one
definition diverging means the definition is loose, and that is a finding about the
rubric rather than about a model.

**Hypothesis:** routing accuracy is a step, not a gradient. On single-source questions
(Q1–Q6) every model that answers at all reaches the right source, because the question
names its own domain. The split appears on cross-source questions (Q7–Q12), where the
second hop can only be chosen after reading the first tool's result.

**Falsifiable prediction:** `reached` ≥ 5/6 on Q1–Q6 for every model with D0 complete,
and the between-model spread on Q7–Q12 at least 3× the spread on Q1–Q6.

**What would refute it:** a small model reaching the right source on the hard
cross-source questions as often as on the easy ones. That would mean the *tool
descriptions*, not the model's reasoning, are doing the routing — a better result for
this project than the hypothesis, and Part 2 would say so plainly.

### D2 · Chain depth — and whether depth is the same thing as quality

**Computed from** `tool_call_count`, `llm_round_trips`, distinct servers touched, and
`depth_ratio` = calls ÷ the documented chain length for that question in `QUESTIONS.md`.

Raw depth is the wrong measure alone. `QUESTIONS.md` gives Q13 a two-call chain; a model
spending fourteen calls on it is thrashing, not reasoning. So depth is reported against
the documented length, and both overshoot and undershoot are named.

**Hypothesis (from the brief):** reasoning tiers go deeper; small tiers skip.

**Falsifiable prediction:** median `depth_ratio` ≥ 0.8 for reasoning tiers and ≤ 0.5 for
nano/mini tiers on Q7–Q12.

**Competing hypothesis, tested at the same time:** `depth_ratio` > 1.5 goes with *worse*
answers, not better — over-calling is a model failing to notice it already has the
answer. If both hold, the honest statement is a band rather than a direction, and Part 2
reports it as a band.

**Measured anchor** — `evals/runs/argo_claudesonnet45/q04.jsonl`: 14 tool calls, 7 round
trips, 224,806 input tokens, and a 480-character answer. The documented Q4 chain is three
calls. Whether those fourteen bought anything is exactly what D2 must decide.

### D3 · Fabrication — Judge owns it, I consume it

**Computed by** `judge.py`, read out of `judge-report.md`. `analyze.py` does **not**
re-implement the check. Two fabrication rules that disagree are worse than one, and the
rule is Judge's under the fleet plan.

What I add is the cross-cut Judge does not: flag rate by **question class** — answerable
(Q1–Q9), unwired (Q10–Q11), gap (Q13–Q15) — against **model tier**.

**Hypothesis:** small tiers fabricate more on the cannot-questions than on the answerable
ones, because a gap question returns no tool result to anchor on and the model fills the
vacuum.

**Falsifiable prediction:** flag rate on Q13–Q15 at least 2× the rate on Q1–Q6, within
every tier showing any flags at all.

**What would refute it:** flags concentrated on Q2, Q6 and Q9 — the counting questions —
instead. That would mean fabrication here is arithmetic drift on real numbers rather than
invention from nothing, and the fix is a prompt rule about not computing, not a model
change.

**Judge's stated limit is inherited whole.** `run_questions.py` truncates each tool result
to 600 characters, so an unmatched number may have sat in the part of the result the
transcript does not hold. `unmatched` gets its own column and is never counted as
fabrication. **If the unmatched column is large, the honest Part 2 sentence is "this
dimension is not yet decidable" and that is what will be written**, not a rate with a
caveat under it.

### D4 · Refusal quality — the four parts of a good no

**Computed on** Q10, Q11, Q13, Q14, Q15. `PIPELINES.md` P8 defines a good refusal as four
things, and each is separately detectable:

| part | detected by |
|---|---|
| the number that proves it | an expected proof number appears in the answer — Q13: `2` and `581,464`; Q14: the 1.6% sparsity figure; Q10: the funder denominator |
| why the question is malformed | a refusal marker **plus** a reason clause, not a bare "I cannot" |
| the nearest answerable question | a reframing marker — "instead", "did you mean", "the question you probably want" |
| the source that could answer it | a named alternative — BV-BRC, CARD, RCSB PDB, AlphaFold |

Scored 0–4 and reported as the four components, not only the total. A 2/4 carrying the
proof number and the alternative source is a different failure from a 2/4 carrying the
reframing and the reason.

**Hypothesis:** refusal quality is what the SYSTEM_PROMPT buys most, and therefore the
dimension that moves most under the `--prompt paper minimal none` ablation.

**Falsifiable prediction:** mean refusal score drops ≥ 1.0 point from `paper` to `none` on
the same model, while routing (D1) drops less than half as much proportionally.

**What would refute it:** refusal quality tracking model tier and ignoring the prompt.
That would mean the 57-line research-paper prompt is not earning the tokens it costs —
a finding the team needs before Friday either way.

### D5 · Trap avoidance — and the split I expect inside it

**Computed from** tool arguments in the step lines and numbers in the answer:

| trap | detected by |
|---|---|
| `search_ena_keywords` called | the tool name in `tools_in_order` |
| `geo_search` without `entry_type` | that call's `args` in the step line |
| 513 quoted as the Series count | `513` in the answer on Q3 or Q7 |
| 50 quoted as an ENA total | `50` in the answer beside an ENA claim |
| wrong Pathogen Detection group | an `organism` argument outside the curated list |

**Hypothesis, and the sharpest one here:** these traps fall into two classes with
different cures.

- **Argument traps** (`entry_type`, the group name) are avoided by the *prompt*, because
  the model has to be told the argument exists. Prediction: tier explains little,
  ablation explains a lot.
- **Credulity traps** (quoting 513, quoting 50 as a total) are avoided by *model quality*,
  because the model must disbelieve a number a tool just handed it. Prediction: tier
  explains a lot, ablation explains little.

**Falsifiable prediction:** tier correlates more strongly with credulity-trap rate than
with argument-trap rate; the ablation shows the reverse. **If the two classes behave
identically the split is wrong**, and Part 2 will retract it in those words.

### D6 · Cost — where the money actually goes

**Computed from** `input_tokens`, `output_tokens`, `llm_round_trips`, `ttft_s`,
`model_seconds`, `tool_seconds`, `list_cost_usd`.

**Hypothesis:** cost on this board is tool-schema overhead multiplied by round trips, not
question difficulty. Roughly 90 tool schemas plus a 57-line system prompt are re-sent on
every round trip, so input tokens should be near-linear in round trips with a large
intercept and a small slope.

**Measured anchors for the intercept.** `evals/runs/argo_claudesonnet45/q01.jsonl` — one
round trip, zero tool calls, zero output: **33,028 input tokens**. That is close to the
pure floor: system prompt + tool schemas + one short question, no tool results, no
history. And `laptop_codeathon` reports an `argo/gpt4o` question at 55,033 input tokens
across 3 round trips for a 926-token answer, which is the same ~33k shape seen from the
other end. Two observations, treated as two.

**Falsifiable prediction:** regress `input_tokens` on `llm_round_trips` per model; the
intercept lands within ±15% of 33k with R² > 0.8. Schema share of input tokens is then
33k × round trips ÷ input tokens, reported per model. My expectation: over half for most
rows.

**What would refute it:** a poor fit, meaning tool *results* dominate instead — in which
case the lever is result truncation, not tool count. Different fix, so the distinction
earns its place.

**Two defects in the cost column, found reading the driver, reported not patched.**
`LIST_PRICE_PER_M` in `run_questions.py` has no key for `claudeopus5`, `gpt5` or several
other catalogue aliases, so those rows carry a null cost — confirmed in
`evals/runs/argo_claudeopus5/q01.jsonl`, `"list_cost_usd": null`. And the prices that are
there are documented in that same file as "from public price pages **as remembered** on
17 Sep 2026". Remembered, not checked. **Every list-price figure in Part 2 is therefore
labelled unverified, and no cost-per-quality claim rests on the dollar column alone.**
Tokens are measured; dollars are a conversion through a table nobody has audited.

### D7 · Transport — `argo/` vs `anthropic/`, same weights

**Computed on** the three Claude models run both ways. Compared: first tool per question,
`tool_call_count`, `answer_chars`, `ttft_s`, `elapsed_s`, `input_tokens`.

**Hypothesis:** nothing differs except TTFT and wall clock. Same weights, same prompt,
same tools; behaviour should be indistinguishable.

**Falsifiable prediction:** identical first tool on ≥ 13/15 questions; median
`tool_call_count` differing by ≤ 1; median `answer_chars` within 30%.

**What would refute it, and why it matters more than anything else here:** a behavioural
difference means the two routes are not sending the same request — different max-tokens,
different sampling, a dropped system prompt, a different tool-schema serialisation. Every
other number in this file is measured over Argo. **If Argo is not neutral, the matrix
measures Argo as much as it measures the models**, and that limit belongs on the Friday
slide rather than in a footnote. This dimension exists to find out.

### D8 · Plateau — does more model buy anything here

Bobby's framing, and `laptop_codeathon`'s hypothesis, on record so it can be falsified:
*these questions need tool selection, not reasoning, so capability plateaus early while
cost does not.*

**Computed from** D1, D2, D4 and D5 plotted against `list_cost_usd` and against
`input_tokens + output_tokens`, per model, per question class.

**Falsifiable prediction:** `claudehaiku45` scores within 10% of `claudeopus5` on routing
(D1) and trap avoidance (D5) at under a fifth of the token cost — and the gap that does
exist sits in refusal quality (D4) on Q13–Q15, not in routing.

**What would refute it:** a monotone capability curve, where each tier up buys a
proportional gain across all four quality dimensions. That is the boring result and it is
equally reportable.

**The trap in this dimension, named in advance.** A plateau in *my* measures is not a
plateau in quality — D1–D5 are behavioural proxies, and a cheap model can route correctly
and still write a worse answer. **If the plateau appears, the claim is "capability
plateaus on the dimensions we can measure mechanically", never "capability plateaus".**
The stronger claim needs a human reading answers against `PIPELINES.md`, and if nobody
does that before Friday, the slide says the weaker thing.

## What this design cannot see

Stated now rather than when the results make it convenient.

1. **Correctness.** A correctly routed, fully evidenced, well-refused answer can still
   misread its own tool result. Nothing here catches that; it needs a human against
   `PIPELINES.md`. D0–D8 measure behaviour, and behaviour is not truth.
2. **The 600-character truncation** bounds every evidence-based check (D3, D4, D5).
3. **Argo only, one venue, one day.** Nothing here generalises past that.
4. **n = 1 per cell** until `--repeat 3` lands. Every unreplicated gap is labelled.
5. **The full chatbot has never run end to end here.** The repo says so and this file does
   not pretend otherwise. This measures the agent through the driver, which uses
   `init_agent()` and the same servers — but it is not a user at the Chainlit UI.
6. **Tier labels are mine.** "Reasoning tier", "small tier" are my groupings of the Argo
   catalogue, listed in `analyze.py` where they can be argued with.

---

# Part 2 · Results

*Tables generated by `evals/analyze.py`, pasted here as models finish, each number
carrying its question, model and transcript path. Where a result contradicts a hypothesis
above, the contradiction gets its own subsection.*

**How much has landed: 17 transcripts on 3 of 36 model rows, all on `argo/`.** Eleven of
the seventeen are one model (`argo_gpt4o`), and two of those eleven are a second run of
Q2 and Q3. No small-tier model, no gap question (Q13–Q15), no `anthropic/` row, no
deliberate repeat. So D4, D7 and D8 are not yet decidable, and every gap named below is a
gap of one run against one run. What follows is written now because the findings that
*are* available change what the rest of the matrix should measure.

**Where these transcripts are, and what produced them.** At 14:39 on 17 Sep `runner`
moved all of them to `evals/runs/_archive-pre-matrix/`, because commit `39bb6ad` fixed
three faults in the code that produced them:

| fault, as the commit describes it | what it does to the numbers below |
|---|---|
| `init_agent()` lost its `return` at the merge `c701e30` and returned `None` | the Chainlit demo was dead; the driver built its own agent, so **the tool board these rows paid for is not the board the demo now has** |
| `NCBI_API_KEY` was empty and nothing said so — `geo.py` and `ncbi_lib/eutils.py` never called `load_dotenv()` | every NCBI call ran at the anonymous 3 req/sec ceiling shared with the venue, so **`tool_seconds` here is a floor, not a measurement** |
| one denial abandoned a model for the rest of the matrix | why `argo_claudeopus5` has 2 rows and `argo_claudesonnet45` has 4 — the missing rows are transport, not model |

None of that voids a number below: each was observed and each is reproducible from the
archived file named beside it. It changes what they generalise to. **Token counts, routing
and trap behaviour stand. Wall-clock and cost-per-question do not transfer to the
current tree**, and Finding 6 measures exactly how far the board has moved since.

### Finding 1 — D6 contradicted: the mechanism was right, the algebra was backwards

D6 predicted "input tokens near-linear in round trips with a **large intercept and a small
slope**", intercept within ±15% of 33k, R² > 0.8. Measured on `argo_gpt4o`, n = 9:

| | predicted | measured |
|---|---|---|
| intercept | 33,000 ± 15% | **457** |
| slope | small | **25,140 tok/trip** |
| R² | > 0.8 | 0.958 |

The fit is excellent and the shape is inverted. The error is mine and it is arithmetic: if
the tool schemas are re-sent on every round trip, then input ≈ block × trips, which puts
the block in the **slope**, not the intercept. I wrote the right mechanism and then
predicted the regression of a model where the block is paid once per question.

The slope confirms the mechanism rather than weakening it. `argo_gpt4o`'s measured prompt
block is 22,204 tokens — the first turn of `evals/runs/_archive-pre-matrix/argo_gpt4o/q02.jsonl`, before any
tool result exists — against a fitted slope of 25,140. Those agree to 13%, and the
difference is the conversation history that accumulates across trips.

The share prediction held and held harder than expected: **78–89% of all input tokens are
the re-sent prompt block**, not "over half". The clearest single row is
`evals/runs/_archive-pre-matrix/argo_gpt4o/q08.jsonl` — 8 round trips, 211,169 input tokens, of which
8 × 22,204 = 177,632 is the same tool schemas sent eight times. **84% of that question's
input cost bought nothing new.**

The refutation criterion did *not* fire: a poor fit would have meant tool results dominate
and truncation was the lever. R² = 0.958 says otherwise. The lever is the number of tool
schemas on the board and the number of round trips, not result size.

### Finding 2 — the same tool board costs 2.5× more on one model than another

Not a hypothesis I registered; it fell out of D6. The smallest first-turn input per model,
which is the system prompt plus all ~90 tool schemas plus a question of a few dozen tokens:

| model | prompt block | rows seen | transcript |
|---|---|---|---|
| `argo_gpt4o` | **22,204** | 9 | `evals/runs/_archive-pre-matrix/argo_gpt4o/q02.jsonl` |
| `argo_claudesonnet45` | **32,998** | 4 | `evals/runs/_archive-pre-matrix/argo_claudesonnet45/q02.jsonl` |
| `argo_claudeopus5` | **55,201** | 2 | `evals/runs/_archive-pre-matrix/argo_claudeopus5/q03.jsonl` |

Identical servers, identical schemas, identical question set. The gap is tokenizer and
whatever each route prepends. Two consequences. First, **the ~33k per-turn overhead quoted
in `_reports/HARDENING-PLAN.md` is a Claude number, not a constant** — it matches
`claudesonnet45` to within 0.1% and overstates `gpt4o` by 49%. Second, cost comparisons
between models are partly a comparison of how expensively each one reads the same tool
board, before any question is asked.

Each figure is the minimum over that model's rows, so it is an upper bound on the true
floor, and the bound is loosest for `claudeopus5` where n = 2.

### Finding 3 — routing is not answering, and that cuts against the plateau hypothesis

`laptop_codeathon`'s hypothesis, on record above: *these questions need tool selection, not
reasoning, so capability plateaus early.* The first evidence runs the other way.

`argo_gpt4o` routed **9/9** and answered **9/9**. On the six questions where
`PIPELINES.md` pins a ground truth, judge records **1 correct, 0 wrong, 5 never stated**
(`evals/judge-report.md`, `## argo_gpt4o`). It picked the right tool every time and then
did not report what the tool returned.

The worked example is Q2. `evals/runs/_archive-pre-matrix/argo_gpt4o/q02.jsonl` calls
`ncbi_pathogen_isolate_count` — the correct tool — with an organism outside Pathogen
Detection's 106 curated groups. The service answers 0, not an error, and the answer
repeats the zero. The true figure is 581,464. Judge flags the same row twice,
`pathogen_wrong_group` and `zero_as_absence`, and the identical pattern repeats on Q6 and
Q9. My four independent detectors agree with judge on every one of the fifteen
transcripts.

So the failure is not tool selection. It is one argument inside a correctly selected tool,
and then believing the result. **If that is where the difficulty lives, a plateau in
routing says nothing about a plateau in capability** — which is the trap D8 named in
advance, arriving earlier than expected. The hypothesis is not refuted: no small-tier
model has run, and a cheap model might make the same mistake. But the dimension that would
separate the tiers is now visibly D5-credulity and ground truth, not D1.

### Finding 4 — a transport failure is sitting in the matrix as a model result

`evals/runs/_archive-pre-matrix/argo_claudesonnet45/q01.jsonl`: 33,028 input tokens, **0 output tokens**, 1
round trip, 0 tool calls, 2.7 seconds, `"denied": false`, `"error": null`. The input is
the model's own prompt block (32,998) plus the question. The model was billed for reading
the whole tool board and returned nothing, and nothing in the record marks it as a
failure.

Scored as it stands, this is a model that cannot answer Q1. It is more likely a dropped
stream. **This row should be rerun by `runner` before anyone scores it**, and until it is,
`argo_claudesonnet45` reads as 2/4 in D0 for a reason that may not be the model's.
Reported to `laptop_codeathon` and `judge`; not patched — `run_questions.py` is not my
file.

**Closed at 14:32.** Commit `39bb6ad` retries the silent empty, and its message quotes
this row back with the same five numbers. It also stops one denial from abandoning a
model for the rest of the matrix, which is the second half of the same problem: the
`argo_claudesonnet45` Q4 row is a real Argo `ACCESS DENIED` returned as answer text after
14 tool calls and 224,806 input tokens, so a denial can arrive *mid-question*, after the
work is paid for. **Part 1 cites that same transcript as D2's measured depth anchor —
"whether those fourteen calls bought anything" — and the answer is no, they bought a
denial.** The anchor was chosen from a metrics table that did not include the `denied`
column. Part 1 stands as written; this is the correction.

### Finding 5 — "routed: yes" can hide a wander into the wrong source

`evals/runs/_archive-pre-matrix/argo_gpt4o/q01.jsonl`. Documented chain, parsed from `QUESTIONS.md`:
`uniprot_search` → `uniprot_get_entry`, two calls. What ran, in order:

```
uniprot_search · lapis_list_organisms · uniprot_get_entry ·
lapis_describe_organism · lapis_get_mutations · lapis_list_organisms
```

Six calls, three of them into Pathoplexus on a UniProt question, and `lapis_list_organisms`
called twice. Judge records this as routed `yes` and first-tool `yes`, both correct — the
question was answered and the right source was reached. D2 is what makes the detour
visible: depth ratio 3.0 against a documented chain of 2, and one off-source server
touched. **Neither measure is wrong; they answer different questions, which is why the
design keeps them apart.**

### Finding 6 — the biggest cost lever is the tool board, not the model

Two questions were rerun on `argo_gpt4o` at 14:37 as a smoke test of the patched driver.
They are not a repeat — the tool board changed between the two runs — and that is what
makes them useful. Same model, same question, same single tool call each time:

| question | run | first-turn input | second-turn input | total input |
|---|---|---|---|---|
| Q2 | `_archive-pre-matrix/argo_gpt4o/q02.jsonl` (14:09) | 22,204 | 22,738 | 44,942 |
| Q2 | `_archive-pre-matrix/argo_gpt4o/q2.jsonl` (14:37) | 27,393 | 27,927 | 55,320 |
| Q3 | `_archive-pre-matrix/argo_gpt4o/q03.jsonl` (14:10) | 22,204 | 35,908 | 58,112 |
| Q3 | `_archive-pre-matrix/argo_gpt4o/q3.jsonl` (14:38) | 27,393 | 41,097 | 68,490 |

**The difference is +5,189 tokens, and it is the same on all four turns to the token.**
Not a mean, not a spread — the identical integer on two independent questions and on both
turns of each. That is a fixed block that grew, and nothing else changed: `tools_in_order`
is `['ncbi_pathogen_isolate_count']` for both Q2 runs and `['geo_search']` for both Q3
runs, and both pairs routed identically.

What grew it is the board. `evals/runs/_archive-pre-matrix/argo_gpt4o/run-manifest.json`
records the 14:37 run against **13 servers**, including `nde` and `bv-brc`, which arrived
with the `main` merge at `c701e30`; commit `39bb6ad` states the count as 117 tools. The
earlier run has no manifest — the registry did not exist yet — so the pre-merge board size
is not recorded, only its price: 22,204 tokens.

**Across all 17 archived transcripts, `nde_*` and `bv-brc` tools were called zero times.**
Twenty-seven distinct tools were called in total. If the 117-tool figure is right, 90 tool
schemas were paid for on every one of the 65 round trips in this archive and never used.
Applying +5,189 per trip to those 65 trips is 337,285 tokens, **15.4% of the 2,185,343
input tokens the whole archive consumed**, bought by two servers no question touched.

This outranks every model-choice finding above. Finding 2 puts the spread between the
cheapest and dearest model's prompt block at 2.5×, which is real but is a choice made
once. The board is a choice made on every turn of every question by every model, it grew
23.4% in half an hour without anyone measuring it, and it grows again with each server a
teammate merges. **D6's queue item should be a board-trim measurement — the same questions
with the unused servers unwired — before any further model rows are spent.** That is a
recommendation to `laptop_codeathon`, not a change I can make: `chatbot.py` `MCP_SERVERS`
is a shared integration point and `nde` is someone else's server.

*Limit: the +5,189 is one model. Whether the same schema text costs the same on Claude
rows is untested — Finding 2 says tokenisation differs by model, so expect a different
integer and the same mechanism.*

### What is still not decidable

- **D3 fabrication.** Judge reports 0 flags across the 15 transcripts it has scored and 2
  unmatched-but-truncated on `argo_gpt4o`. With no gap questions run yet, the cases most
  likely to produce fabrication have not been attempted. 0 here means "not yet observed",
  not "does not happen".
- **D4 refusal quality.** Zero rows scored. Q10, Q11 and Q13–Q15 have not run on any model.
- **D7 transport.** No `anthropic/` row exists. Until one does, every number in this file
  is a measurement of the models *through Argo*, and Argo's neutrality is assumed, not
  shown.
- **D8 plateau.** Three models, all frontier or reasoning tier, no small tier, no repeat.
  The registered prediction compares `claudehaiku45` with `claudeopus5`; neither pairing
  exists yet.

- **Everything, against the current tree.** The archive was produced by code with three
  faults now fixed (see the provenance table at the top of Part 2) and by a tool board
  5,189 tokens per turn smaller than today's. The base matrix has to be re-run on
  `39bb6ad` or later before any of this is quoted as a property of the system we demo.

### Queued for `runner`, in priority order

1. **A board-trim pair** (Finding 6, new and top of the list): the same two questions on
   `argo_gpt4o` with `nde` and `bv-brc` unwired from `MCP_SERVERS`, against the same two
   with them wired. Two runs, four questions, and it prices the largest cost lever found
   so far. Needs `laptop_codeathon` to agree the wiring change, since `MCP_SERVERS` is
   shared.
2. Rerun `argo_claudesonnet45` Q1 and Q4 on the patched driver — Q1 for the silent empty,
   Q4 because its denial arrived after 14 tool calls were paid for (Finding 4).
3. Any small-tier model through Q1–Q9 — the plateau has no cheap end yet.
4. Q13–Q15 on any model, to give D4 and D3 something to score.
5. One `anthropic/` row, to make D7 exist.
6. `--repeat 3` on one model, so "unreplicated" can come off these findings. Finding 6's
   four rows are *not* a repeat: the board changed between them.

---

<!-- BEGIN GENERATED -->

*Generated by `evals/analyze.py` from 17 transcripts across 3 model rows under `evals/runs/_archive-pre-matrix/`. Expected chains parsed from `QUESTIONS.md`; fabrication read from `judge-report.md`.*

**Every gap below is unreplicated** — one run per cell. Nothing here is a real difference between models until `--repeat 3` shows it exceeds a model's spread against itself.

**Input notes:** **`argo_gpt4o` Q2 has 2 transcripts** (q02.jsonl, q2.jsonl) — both are counted as rows, so this model's row count exceeds its question count. **`argo_gpt4o` Q3 has 2 transcripts** (q03.jsonl, q3.jsonl) — both are counted as rows, so this model's row count exceeds its question count.

### D0 · Completion

| model | tier | answered | denied | errors | incomplete rows |
|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 2/2 | 0 | 0 | — |
| `argo_claudesonnet45` | frontier | 2/4 | 1 | 0 | Q1 **silent empty**, Q4 denied |
| `argo_gpt4o` | frontier | 11/11 | 0 | 0 | — |

### D1 · Routing

`reached` = any expected tool called, by question class. `first ok` = the first tool called was an expected one. Expected sets are parsed from the step tables in `QUESTIONS.md`.

| model | tier | reached Q1–9,12 | unwired Q10–11 | gap Q13–15 | first ok | off-source servers touched |
|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 2/2 | — | — | 2/2 | ncbi, pubmed, string |
| `argo_claudesonnet45` | frontier | 3/4 | — | — | 3/4 | pubmed |
| `argo_gpt4o` | frontier | 11/11 | — | — | 11/11 | pdn |

**Cross-check against `judge.py`.** Every row agrees.

### D2 · Chain depth

`depth ratio` = tool calls ÷ the documented chain length for that question in `QUESTIONS.md`. 1.0 is the documented chain; below 0.5 is skipping, above 2.0 is thrashing.

| model | tier | mean calls | mean LLM trips | median depth ratio | mean servers | ratio ≥ 2 (thrashing) |
|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 7.0 | 4.0 | 4.00 | 2.5 | Q1 (6.0×), Q3 (2.0×) |
| `argo_claudesonnet45` | frontier | 5.0 | 3.5 | 2.50 | 1.0 | Q3 (4.0×) |
| `argo_gpt4o` | frontier | 3.6 | 3.9 | 1.00 | 1.5 | Q1 (3.0×), Q4 (2.67×), Q9 (2.0×) |

### D3 · Fabrication (read from `judge-report.md`, not re-implemented)

| model | tier | fabricated | unmatched | judge routed |
|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 0 | 2 | 2/2 |
| `argo_claudesonnet45` | frontier | 0 | 0 | 2/3 |
| `argo_gpt4o` | frontier | 0 | 2 | 9/9 |
**The unmatched column is large, so D3 is not yet decidable.** `run_questions.py` keeps the first 600 characters of each tool result; an unmatched number may have been in the part the transcript does not hold. Raising `RESULT_EXCERPT` converts these into decidable flags.

### D4 · Refusal quality

The four parts of a good refusal, from `PIPELINES.md` P8, on Q10, Q11 and Q13–Q15. Reported as components: a 2/4 with the proof number is a different failure from a 2/4 with the reframing.

| model | tier | rows scored | proof number | reason | reframing | named source | mean /4 |
|---|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | — | — | — | — | — | — |
| `argo_claudesonnet45` | frontier | — | — | — | — | — | — |
| `argo_gpt4o` | frontier | — | — | — | — | — | — |

### D5 · Trap avoidance

Which traps fired is `judge.py`'s call, read from `judge-report.md`. The split is mine: an **arg** trap needs the model to know an argument exists, a **credulity** trap needs it to disbelieve a number a tool just handed it. D5 predicted the prompt fixes the first and model quality fixes the second.

| model | tier | arg traps | credulity traps | unclassified | which (judge) |
|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 0 | 0 | 0 | none |
| `argo_claudesonnet45` | frontier | 0 | 0 | 0 | none |
| `argo_gpt4o` | frontier | 4 | 4 | 0 | `pathogen_wrong_group` Q2,Q6,Q9,Q2; `zero_as_absence` Q2,Q6,Q9,Q2 |

- `ena_50` — **credulity**
- `ena_keywords` — **credulity**
- `gds_513` — **credulity**
- `geo_no_entry_type` — **arg**
- `meca_94336` — **credulity**
- `pathogen_wrong_group` — **arg**
- `rows_as_isolates_150926` — **credulity**
- `zero_as_absence` — **credulity**

**My four detectors vs judge.** Every transcript agrees.

### D6 · Cost

Tokens are measured. **Dollars are not** — `LIST_PRICE_PER_M` in `run_questions.py` is documented there as prices *as remembered* on 17 Sep 2026, and has no row at all for `claudeopus5` or `gpt5`. Treat the dollar column as unverified and never as the basis of a cost-per-quality claim.

| model | tier | rows | mean in tok | mean out tok | in tok / round trip | mean TTFT s | model s | tool s | list $ (unverified) |
|---|---|---|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 2 | 274,006 | 5,054 | 71,167 | 4.34 | 65.3 | 4.8 | **no price row** |
| `argo_claudesonnet45` | frontier | 2 | 135,560 | 3,210 | 45,187 | 3.00 | 73.8 | 1.8 | $0.91 |
| `argo_gpt4o` | frontier | 11 | 100,762 | 894 | 26,291 | 3.51 | 28.7 | 1.4 | $2.87 |

**What the tool board costs before anyone asks anything.** The smallest first-turn input across a model's rows — the system prompt, all ~90 tool schemas and a question of a few dozen tokens. It is an upper bound on the true floor, tighter the more rows a model has.

| model | tier | prompt block (tok) | rows seen | share of all input tokens |
|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 55,201 | 2 | 78.0% |
| `argo_claudesonnet45` | frontier | 32,998 | 4 | 78.1% |
| `argo_gpt4o` | frontier | 22,204 | 11 | 86.0% |

**Is input cost schema overhead × round trips?** Least squares of `input_tokens` on `llm_round_trips`, per model. The intercept is the fixed cost of one turn — system prompt plus every tool schema. D6 predicted ≈33k ± 15% with R² > 0.8.

| model | intercept (tok) | slope (tok/trip) | R² | n |
|---|---|---|---|---|
| `argo_gpt4o` | 6,199 | 24,190 | 0.954 | 11 |

### D7 · Transport

*Not yet runnable: no model has been recorded through both `argo/` and `anthropic/`. Queue item 6.*

### D8 · Plateau — does more model buy anything here

Quality proxies against what they cost. A plateau here is a plateau **on the dimensions that can be measured mechanically**, which is not the same as a plateau in answer quality; the stronger claim needs a human reading answers against `PIPELINES.md`.

| model | tier | answered | routed | ground truth (judge) | trap hits | fabricated | total tokens | mean s |
|---|---|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 2/2 | 2/2 | 1✓ / 0✗ / 0 unstated | 0 | 0 | 558,119 | 70.2 |
| `argo_claudesonnet45` | frontier | 2/4 | 3/4 | 2✓ / 0✗ / 0 unstated | 0 | 0 | 536,372 | 49.6 |
| `argo_gpt4o` | frontier | 11/11 | 11/11 | 1✓ / 0✗ / 5 unstated | 4 | 0 | 1,118,212 | 30.1 |

<!-- END GENERATED -->

*The block above is written by `uv run evals/analyze.py --write` and is the only part of
this file the script may touch. Part 1 is never rewritten by a tool.*
