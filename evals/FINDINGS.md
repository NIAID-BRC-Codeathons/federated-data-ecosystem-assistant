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
archived file named beside it. **But that file is not in the repo.** `.gitignore`
line 29 (`evals/runs/**/q*.jsonl`, added at `18e40e2`) keeps the scorecards, the manifests
and `RUNS.md` and excludes every transcript, so a reviewer who clones this repository gets
the paths cited below and none of the files. They exist on the laptop at
`evals/runs/_archive-pre-matrix/` — 31 files, 484 KB in total. **Every citation in Part 2
is checkable there and nowhere else**, which is a weaker claim than this file was written
to make, and the reader should know which one they are getting. It changes what they generalise to. **Token counts, routing
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

> **Withdrawn in part, 14:58.** Every judge-derived number in this finding (`1 correct, 0 wrong, 5 never stated`; the trap flags; `routed 9/9`) came from the
> version of `evals/judge-report.md` that existed before commit `9228627` at 14:38.
> That report has been replaced by one whose only section is `## Nothing scorable`:
> the judge now declines to score the archive at all, because it *"straddles the 14:08
> port-move commit"*. Those numbers no longer have a source and are not to be quoted.
> What survives is stated below the rule; it is what I read from the transcripts myself.

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

**What survives the withdrawal.** The Q2 worked example does, entirely: the tool call, its argument, the returned 0 and the 0 in the answer are all read straight from `q02.jsonl`, and the true 581,464 is pinned in `PIPELINES.md`. The replacement `judge-report.md` keeps the same example in its own glossary — *`zero_as_absence` ... observed on argo/gpt4o, 17 Sep, against a true 581,464*. So the mechanism stands on the transcript and on the judge's current text. The **rate** does not: `1 correct, 0 wrong, 5 never stated` has no live source, and until the judge scores a re-run there is no measured ground-truth rate for any model.

So the failure is not tool selection. It is one argument inside a correctly selected tool,
and then believing the result. **If that is where the difficulty lives, a plateau in
routing says nothing about a plateau in capability** — which is the trap D8 named in
advance, arriving earlier than expected. The hypothesis is not refuted: no small-tier
model has run, and a cheap model might make the same mistake. But the dimension that would
separate the tiers is now visibly D5-credulity and ground truth, not D1.

### Finding 4 — a transport failure is sitting in the matrix as a model result

**Half withdrawn, 15:10.** This finding calls the fault *"closed at 14:32 by `39bb6ad`"*.
Detection and retry did land and do work; the **recording** did not, and the fault is
still live in the run happening now at a rate of 4 questions in 9. See Finding 8, which
also says why I got this wrong: I checked a fix against the code that contained it rather
than against a transcript it produced.

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
called twice. ~~Judge records this as routed `yes` and first-tool `yes`~~ — that came from the pre-`9228627` judge report and is **withdrawn** with it (see the banner on Finding 3). The routing claim does not depend on it: the first call is `uniprot_search`, which is the documented first step, read from the transcript. Both readings are correct — the
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

**Confirmed at 14:56, with the sign reversed.** A third run of the same two questions on
the same model landed after this finding was written:
`evals/runs/_archive-pre-matrix/smoke-1456-13f49c3/`, `run_id
20260917-145609-13f49c3-dirty`. Per-turn input tokens, read from the `usage` block of each
step rather than from the totals:

| run | Q2 turn 1 | Q2 turn 2 | Q3 turn 1 | Q3 turn 2 |
|---|---|---|---|---|
| 14:09 / 14:10, pre-merge board | 22,204 | 22,738 | 22,204 | 35,908 |
| 14:37 / 14:38, 13 servers | 27,393 | 27,927 | 27,393 | 41,097 |
| 14:56, 13 servers, `13f49c3` | 25,344 | 25,878 | 25,344 | 39,048 |

| step | per-turn delta, Q2 | per-turn delta, Q3 |
|---|---|---|
| 14:09 -> 14:37 | **+5,189, +5,189** | **+5,189, +5,189** |
| 14:37 -> 14:56 | **-2,049, -2,049** | **-2,049, -2,049** |

All three runs called the identical tool — `ncbi_pathogen_isolate_count` for Q2,
`geo_search` for Q3, one call each — and routed identically. Two things follow.

**The mechanism is now measured twice, in both directions.** Each time the change is a
single integer repeated on every turn of two unrelated questions. That is a fixed block
being re-sent, not run-to-run variance, and it is the only claim in this file that has
been measured more than once.

**Server count is the wrong unit.** The manifest lists the same 13 servers at 14:37 and at
14:56, and the block still fell by 2,049 tokens a turn (the run note says `MCP_NO_BROWSER
guard`). A board trim has to be priced by measuring the prompt block, not by counting
servers off `MCP_SERVERS`.

*A weak side-benefit for D1: the same model gave the same routing decision on these two
questions three times across three different boards. Two questions on one model is not
repeatability, but it is the first time anything here has been seen twice.*

*Limit: both integers are one model. Whether the same schema text costs the same on Claude
rows is untested — Finding 2 says tokenisation differs by model, so expect a different
integer and the same mechanism.*

### Finding 7 — the archive under-reports its own denial rate, and the proof survives by accident

`argo_claudeopus5` Q1 exists twice, at the same relative path in two archived directories,
and the two are not the same run:

| transcript | denied | calls | in_tok | out_tok | elapsed | answer |
|---|---|---|---|---|---|---|
| `evals/runs/_premerge-1405-0f0101e/argo_claudeopus5/q01.jsonl` | **true** | 2 | 44,015 | 373 | 22.7 s | Argo's `ACCESS DENIED` text, 480 chars |
| `evals/runs/_archive-pre-matrix/argo_claudeopus5/q01.jsonl` | false | 12 | 395,016 | 5,741 | 87.6 s | a real 8,674-char report |

Same model, same question (`question_number: 1` in both), same opening chain
`uniprot_search -> string_resolve_proteins`. The first attempt was denied; a later one
answered.

**Why only one of them is in the matrix.** `run_questions.py` line 447, inside `run_one`,
opens the transcript with `path.open("w")`. A retry calls `run_one` again with the same
model and number, so it truncates the very file it is retrying. The denied attempt
survives only because the whole `runs/` tree was copied by hand at 14:14:58 — all 14 files
in `_premerge-1405-0f0101e` carry that identical mtime, and 13 of them are byte-identical
to their `_archive-pre-matrix` twins (`md5sum`). The fourteenth is this one.

**What it does to the numbers.** The archive on its own shows 1 denial in 17 transcripts,
5.9%. Counting the overwritten attempt it is 2 in 18, 11.1%. Neither is a measurement —
the first is a floor set by how many overwrites happened to be copied. Any denial rate
quoted from pre-`39bb6ad` transcripts is a floor and must be labelled one.

**The discarded attempt was not free.** 44,015 input tokens and 373 output, billed and
logged to `ac.ni`, for an answer that was thrown away — 11.1% on top of the 395,016 the
successful retry then cost. One question, one model, 439,031 input tokens total.

**This contradicts the obvious explanation.** Argo went live for `ac.ni` at 15:10. Both of
these runs are before that: the denial no later than 14:14:58, the success at 14:26:52.
So the denial is intermittent rather than a clean before-and-after on authorization, and a
model that answers is not proof the gateway is open. The driver's own retry message says
the same thing — `"Argo returned ACCESS DENIED as content (intermittent)"`.

**Already fixed, at `39bb6ad`.** Lines 585–607 retry only the two transport faults, and
keep each thrown-away attempt in `attempts_discarded` with its token cost, under the
comment *"Keep what we are throwing away, or the fault rate disappears along with the
fault."* Measured across the 31 archived transcripts: 29 have no `retries` field at all,
and the 2 written by the patched driver carry `retries: 0, attempts_discarded: []`. So the
fault rate is recoverable from here on and not before.

*Residual limitation, reported to `laptop_codeathon` and not patched — `run_questions.py`
is not my file.* `attempts_discarded` keeps five scalars per discarded attempt, not its
transcript, because `run_one` still writes the same path on every attempt. The denied
answer text is the only thing that distinguishes an Argo denial from the model's own
refusal, and it does not survive the retry. That lands on D4: a refusal scored from a
retried row is scored on the retry, and the discarded attempt cannot be re-read.

### Finding 8 - the live matrix loses 6 questions in 15 to a transport fault, and the transcript is structurally unable to say so

Measured on `run_id 20260917-145854-bafed0f-dirty`, `code_sha bafed0f-dirty`, under
`evals/runs/argo_claudesonnet45/`, which finished all 15 questions at 15:12. This is the
first data from the fixed tree, and it is the most important thing in this file.

**The fault.** Six of the fifteen questions came back with nothing, and nothing said
why.

| transcript | input tok | output tok | LLM trips | tool calls | denied | error | elapsed |
|---|---|---|---|---|---|---|---|
| `q01.jsonl` | 37,583 | **0** | 1 | 0 | false | null | 1.3s |
| `q05.jsonl` | 37,571 | **0** | 1 | 0 | false | null | 3.3s |
| `q07.jsonl` | 37,585 | **0** | 1 | 0 | false | null | 1.4s |
| `q09.jsonl` | 37,592 | **0** | 1 | 0 | false | null | 1.4s |
| `q14.jsonl` | 37,582 | **0** | 1 | 0 | false | null | 1.3s |
| `q15.jsonl` | 37,578 | **0** | 1 | 0 | false | null | 1.3s |

The input spread across the six is 21 tokens - the prompt block plus the question, and
not one thing more. The model was billed and produced nothing.

**The record cannot report it.** `run_questions.py` detects this case correctly:
`is_silent_empty` at line 144, `MAX_EMPTY_RETRIES = 2` at line 139, and the retry loop at
583-615 which keeps every discarded attempt because, in its own comment, *"a retry that
hides what it retried would erase the transport-fault rate, and that rate is a finding in
its own right."* The loop is right. It runs too late. `run_one` writes the transcript at
line 447 with the `"retries": 0` and `"attempts_discarded": []` of lines 440-441 already
in it, and returns. The loop then sets `retries`, `attempts_discarded` and
`error = "silent empty after N retries"` on the returned record, in memory. Nothing writes
the file again. **No transcript this driver produces can ever report a retry.**

**The retry did fire, and here is the proof from outside the file.** Each transcript is
written when its question ends, so the gap between consecutive mtimes is that question's
true wall cost. For every answered question that gap equals the recorded `elapsed_s`:

| answered | q02 | q03 | q04 | q06 | q08 | q10 |
|---|---|---|---|---|---|---|
| wall gap | 40.1s | 76.9s | 86.0s | 86.5s | 114.4s | 88.5s |
| recorded `elapsed_s` | 40.0s | 76.9s | 86.0s | 86.5s | 114.4s | 88.5s |

For the silent empties it does not:

| empty | recorded | true wall | unaccounted |
|---|---|---|---|
| `q05.jsonl` | 3.3s | 12.0s | **8.7s** |
| `q07.jsonl` | 1.4s | 12.1s | **10.7s** |
| `q09.jsonl` | 1.4s | 10.3s | **8.9s** |
| `q14.jsonl` | 1.3s | 9.8s | **8.5s** |
| `q15.jsonl` | 1.3s | 9.9s | **8.6s** |

The loop sleeps `2.0 * attempts` between tries, so two retries cost 2.0s + 4.0s = **6.0s**
of sleep before any model time - a constant that appears nowhere else in the driver. Add
two discarded calls of roughly 1.4s each and 8.8s is predicted, against 8.5s, 8.6s, 8.7s,
8.9s and 10.7s measured. `q01.jsonl` has no predecessor to measure a gap against, but its
five-number signature is identical to the other five.

**What it costs.** Three attempts at about 37.58k input tokens each is roughly 112.7k per
silent-empty question, of which the transcript reports 37.58k. Across the six: **676,473
input tokens spent, 225,491 recorded, 450,982 in nobody's total.** The row's whole
recorded input is 2,293,768 tokens, so the invisible spend is **20% on top of everything
this model row admits to** - and that row bought six blank answers with it. On a 36-model
matrix it is not a rounding error.

**What it does to everyone else's numbers.** `judge-report.md` at 15:02 scores Q1 and Q5
as `routed: no`, `first: no`, `empty answer` - that is, as `argo/claudesonnet45`
behaviour, and the same happens to every empty it reaches. Six of the fifteen questions
never reached the model. Any routing or completion rate computed from this run is wrong by
the empty rate until the driver is fixed, and the empty rate is the one number the
transcript will not give you.

**It also ate the dimension it could least afford to.** Q14 and Q15 are two of the three
gap questions, the ones D4 exists to score - can the system say a clean *no*, with a
reason and a redirection. Both came back empty. D4 has been "no rows yet" all afternoon;
it is now *no rows because the transport swallowed them*, which is a different sentence
and a worse one. A blank is not a refusal, and if this reaches a slide as "the system
declined" it would be the exact failure this project was set up to prevent.

**This contradicts Finding 4, which is mine.** Finding 4 records the silent empty as
*"closed at 14:32 by `39bb6ad`"*. That was half right and I should not have written it.
The detection landed, the retry landed, and both work - I have now watched the retry fire
six times. The *recording* did not land, and I called the defect closed on the commit
message and the code I had read rather than on a transcript produced by the fixed driver.
The check I had was one that could not come back dirty: the code plainly contains a retry,
so reading the code could only ever agree with me. The transcript could have disagreed,
and when it finally arrived, it did.

**What I changed in my own file because of this.** The `discarded attempts` column in D0
used to print `0` for any transcript carrying a `retries` field. That zero was a constant
dressed as a measurement. It now prints `**inert**`, and `hidden_retry_note` in
`analyze.py` recovers the retry from mtime gaps and prints the table above. Both states
were proven: the note fires on `evals/runs/` and stays silent on
`evals/runs/_archive-pre-matrix/`, whose files were hand-copied and share one mtime.

*Limit: one model, one complete run of 15. Whether the empty rate is a property of Argo,
of `claudesonnet45`, or of this afternoon is not established here - the next model row
settles it, and nothing about that row's rate should be read off this one. Reported to `laptop_codeathon` as blocking, and to `judge` as a rubric change.*

**15:18, the next model row settles half of it.** `argo_claudeopus5` began at
15:13:49, 78 seconds after `argo_claudesonnet45`'s last question, in the **same process**
- both rows carry `run_id 20260917-145854-bafed0f-dirty` and `code_sha bafed0f-dirty`, so
the driver, the tool board and the Argo gateway are held constant across the comparison.
Five questions in, opus5 has **no empties and no unaccounted seconds**:

| transcript | recorded `elapsed_s` | mtime gap | unaccounted |
|---|---|---|---|
| `evals/runs/argo_claudeopus5/q02.jsonl` | 65.6 | 65.6 | -0.0 |
| `evals/runs/argo_claudeopus5/q03.jsonl` | 51.5 | 51.5 | 0.0 |
| `evals/runs/argo_claudeopus5/q04.jsonl` | 71.1 | 71.1 | 0.0 |
| `evals/runs/argo_claudeopus5/q05.jsonl` | 69.6 | 69.7 | 0.1 |

(`q01` has no predecessor, so it has no gap; its 113.0s is unchecked.)

That clean result is worth something only because the identical command comes back dirty
on the row above it: the same arithmetic flags all five measurable sonnet45 empties at
8.5-10.7 unaccounted seconds each. So the fault is **not** the driver, **not** the board,
and **not** Argo as such. It tracks the model endpoint: 6 in 15 on `argo/claudesonnet45`,
0 in 5 so far on `argo/claudeopus5`.

*What this does not establish:* the two rows ran consecutively, not interleaved, so a
gateway fault that comes and goes over minutes would produce the same picture. Re-running
`claudesonnet45` now, or interleaving the two, is what would separate them. Until that is
done, "sonnet45 drops answers" and "Argo dropped answers between 14:59 and 15:12" both fit.

**The fix landed and this matrix cannot use it.** `run_questions.py` in the working tree
now defines `rewrite_summary(record)` and calls it whenever `attempts` is non-zero, which
is the right fix and matches the diagnosis above; its docstring states the mechanism in the
same terms. But the file's mtime is **15:17:30**, after `argo_claudeopus5/q04.jsonl` was
written at 15:16:57, and the running process imported the module at 14:58:54. Python does
not reload a module because the file changed. So **every remaining row of this matrix will
still write `retries: 0`**, the fix takes effect on the next run, and D0's `**inert**` cell
remains the correct thing to print for this dataset. A restart is a decision for `runner`,
not for me: it would cost the 20 rows already spent.
### What is still not decidable

- **D3 fabrication.** Judge reports 0 flags across the 15 transcripts it has scored and 2
  unmatched-but-truncated on `argo_gpt4o`. With no gap questions run yet, the cases most
  likely to produce fabrication have not been attempted. 0 here means "not yet observed",
  not "does not happen".
- **D4 refusal quality.** Zero rows scored. Q10, Q11 and Q13–Q15 have not run on any model.
- **D7 transport.** No `anthropic/` row exists. Until one does, every number in this file
  is a measurement of the models *through Argo*, and Argo's neutrality is assumed, not
  shown. Finding 7 makes that assumption worse, not better: Argo denied and then
  answered the same question on the same model twelve minutes apart, so the transport
  is not even stable against itself.
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
   Q4 because its denial arrived after 14 tool calls were paid for (Finding 4). Add
   `argo_claudeopus5` Q1: it is the one row with a known discarded attempt (Finding 7),
   so it is the cheapest way to see `attempts_discarded` populated by the fix.
3. Any small-tier model through Q1–Q9 — the plateau has no cheap end yet.
4. Q13–Q15 on any model, to give D4 and D3 something to score.
5. One `anthropic/` row, to make D7 exist.
6. `--repeat 3` on one model, so "unreplicated" can come off these findings. Finding 6's
   four rows are *not* a repeat: the board changed between them.

---

<!-- BEGIN GENERATED -->

*Generated by `evals/analyze.py` from 32 transcripts across 3 run rows (3 distinct models) under `evals/runs/`. Expected chains parsed from `QUESTIONS.md`; fabrication read from `judge-report.md`.*

**Every gap below is unreplicated** — one run per cell. Nothing here is a real difference between models until `--repeat 3` shows it exceeds a model's spread against itself.


**Input notes:** `_archive-pre-matrix/` holds no `qNN.jsonl` of its own but 19 one level deeper — treated as a snapshot and **not loaded**. `_premerge-1405-0f0101e/` holds no `qNN.jsonl` of its own but 14 one level deeper — treated as a snapshot and **not loaded**. **`judge-report.md` has scored 25 of 32 transcripts.** Every judge-derived cell below (D3, D5, the ground-truth column of D8) is a verdict on that subset only; the rest are unscored, not clean — `argo_claudeopus5` Q11, Q12, Q13, Q14, Q15; `argo_gpt4o` Q1, Q2.

### D0 · Completion

| model | tier | answered | denied | discarded attempts | errors | incomplete rows |
|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 15/15 | 0 | **inert** | 0 | — |
| `argo_claudesonnet45` | frontier | 9/15 | 0 | **inert** | 0 | Q1 **silent empty**, Q5 **silent empty**, Q7 **silent empty**, Q9 **silent empty**, Q14 **silent empty**, Q15 **silent empty** |
| `argo_gpt4o` | frontier | 2/2 | 0 | **inert** | 0 | — |
**The `discarded attempts` column is inert, so here is the retry measured from outside the file.** Every silent empty below was retried and stayed empty; the transcript records `retries: 0` for all of them. `unaccounted` is the wall gap between consecutive transcripts minus the run's own `elapsed_s`. Under `code_sha bafed0f`, the build that produced these rows, the retry sleep is `2.0 * attempts`: 2.0s for one retry, 6.0s for two. The working tree has since changed it to `EMPTY_BACKOFF = (4.0, 10.0)`, so read this column against the `code_sha` in the summary, not against the driver as it stands today.

| model | Q | transcript | recorded s | true wall s | unaccounted s | input tok recorded |
|---|---|---|---|---|---|---|
| `argo_claudesonnet45` | Q5 | `evals/runs/argo_claudesonnet45/q05.jsonl` | 3.3 | 12.0 | 8.7 | 37,571 |
| `argo_claudesonnet45` | Q7 | `evals/runs/argo_claudesonnet45/q07.jsonl` | 1.4 | 12.1 | 10.7 | 37,585 |
| `argo_claudesonnet45` | Q9 | `evals/runs/argo_claudesonnet45/q09.jsonl` | 1.4 | 10.3 | 8.9 | 37,592 |
| `argo_claudesonnet45` | Q14 | `evals/runs/argo_claudesonnet45/q14.jsonl` | 1.3 | 9.8 | 8.5 | 37,582 |
| `argo_claudesonnet45` | Q15 | `evals/runs/argo_claudesonnet45/q15.jsonl` | 1.3 | 9.9 | 8.6 | 37,578 |
So one silent empty really costs about three times what its transcript reports, and the `input_tokens` of the two discarded attempts are in nobody's total. Reported to `laptop_codeathon` at 15:08; a fix landed in the working tree at 15:17 as `rewrite_summary()`, called whenever `attempts` is non-zero. It cannot help this matrix: the running process imported the driver at 14:58:54 and Python does not reload a changed module, so every remaining row here still writes `retries: 0`.


### D1 · Routing

`reached` = any expected tool called, by question class. `first ok` = the first tool called was an expected one. Expected sets are parsed from the step tables in `QUESTIONS.md`.

| model | tier | reached Q1–9,12 | unwired Q10–11 | gap Q13–15 | first ok | off-source servers touched |
|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 10/10 | 2/2 | — | 9/12 | brc_analytics, ncbi, pubmed, string, uniprot |
| `argo_claudesonnet45` | frontier | 6/10 | 2/2 | — | 6/12 | geo, pubmed |
| `argo_gpt4o` | frontier | 2/2 | — | — | 2/2 | pdn |

**Cross-check against `judge.py`.** Every row agrees.

### D2 · Chain depth

`depth ratio` = tool calls ÷ the documented chain length for that question in `QUESTIONS.md`. 1.0 is the documented chain; below 0.5 is skipping, above 2.0 is thrashing.

| model | tier | mean calls | mean LLM trips | median depth ratio | mean servers | ratio ≥ 2 (thrashing) |
|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 9.8 | 5.3 | 3.04 | 2.1 | Q1 (6.5×), Q2 (3.5×), Q3 (2.0×), Q4 (4.0×) |
| `argo_claudesonnet45` | frontier | 3.9 | 3.3 | 2.25 | 1.0 | Q3 (4.0×), Q4 (3.33×), Q8 (2.5×), Q10 (2.0×) |
| `argo_gpt4o` | frontier | 1.5 | 2.0 | 0.75 | 1.5 | — |

### D3 · Fabrication (read from `judge-report.md`, not re-implemented)

| model | tier | fabricated | unmatched | judge routed |
|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 0 | 10 | 10/10 |
| `argo_claudesonnet45` | frontier | 0 | 8 | 8/8 |
**The unmatched column is large, so D3 is not yet decidable.** `run_questions.py` keeps the first 600 characters of each tool result; an unmatched number may have been in the part the transcript does not hold. Raising `RESULT_EXCERPT` converts these into decidable flags.

### D4 · Refusal quality

The four parts of a good refusal, from `PIPELINES.md` P8, on Q10, Q11 and Q13–Q15. Reported as components: a 2/4 with the proof number is a different failure from a 2/4 with the reframing.

| model | tier | rows scored | proof number | reason | reframing | named source | mean /4 |
|---|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 5 | 3/4 | 0/5 | 3/5 | 2/5 | 1.60 |
| `argo_claudesonnet45` | frontier | 3 | 2/3 | 0/3 | 0/3 | 1/3 | 1.00 |
| `argo_gpt4o` | frontier | — | — | — | — | — | — |

### D5 · Trap avoidance

Which traps fired is `judge.py`'s call, read from `judge-report.md`. The split is mine: an **arg** trap needs the model to know an argument exists, a **credulity** trap needs it to disbelieve a number a tool just handed it. D5 predicted the prompt fixes the first and model quality fixes the second.

| model | tier | arg traps | credulity traps | unclassified | which (judge) |
|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 0 | 0 | 0 | none |
| `argo_claudesonnet45` | frontier | 0 | 0 | 0 | none |
| `argo_gpt4o` | frontier | 0 | 0 | 0 | none |

- `ena_50` — **credulity**
- `ena_keywords` — **credulity**
- `gds_513` — **credulity**
- `geo_no_entry_type` — **arg**
- `meca_94336` — **credulity**
- `pathogen_wrong_group` — **arg**
- `rows_as_isolates_150926` — **credulity**
- `zero_as_absence` — **credulity**

**My four detectors vs judge.** The rows below fired for one and not the other.

| model | Q | trap | seen by | transcript |
|---|---|---|---|---|
| `argo_claudesonnet45` | Q10 | `ena_50` | analyst only | `evals/runs/argo_claudesonnet45/q10.jsonl` |
| `argo_claudesonnet45` | Q11 | `ena_50` | analyst only | `evals/runs/argo_claudesonnet45/q11.jsonl` |

### D6 · Cost

Tokens are measured. **Dollars are not** — `LIST_PRICE_PER_M` in `run_questions.py` is documented there as prices *as remembered* on 17 Sep 2026, and has no row at all for `claudeopus5` or `gpt5`. Treat the dollar column as unverified and never as the basis of a cost-per-quality claim.

| model | tier | rows | mean in tok | mean out tok | in tok / round trip | mean TTFT s | model s | tool s | list $ (unverified) |
|---|---|---|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 15 | 393,749 | 5,836 | 70,228 | 4.68 | 74.7 | 5.4 | **no price row** |
| `argo_claudesonnet45` | frontier | 9 | 229,809 | 3,539 | 49,092 | 3.53 | 75.3 | 3.6 | $6.68 |
| `argo_gpt4o` | frontier | 2 | 51,176 | 790 | 25,588 | 3.57 | 18.0 | 0.4 | $0.27 |

**What the tool board costs before anyone asks anything.** The smallest first-turn input across a model's rows — the system prompt, all ~90 tool schemas and a question of a few dozen tokens. It is an upper bound on the true floor, tighter the more rows a model has.

| model | tier | prompt block (tok) | rows seen | share of all input tokens |
|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 50,102 | 15 | 78.2% |
| `argo_claudesonnet45` | frontier | 37,553 | 15 | 82.9% |
| `argo_gpt4o` | frontier | 25,344 | 2 | 99.0% |

**Is input cost schema overhead × round trips?** Least squares of `input_tokens` on `llm_round_trips`, per model. The intercept is the fixed cost of one turn — system prompt plus every tool schema. D6 predicted ≈33k ± 15% with R² > 0.8.

| model | intercept (tok) | slope (tok/trip) | R² | n |
|---|---|---|---|---|
| `argo_claudeopus5` | -294,029 | 128,958 | 0.644 | 15 |
| `argo_claudesonnet45` | 39,597 | 39,812 | 0.635 | 9 |

### D7 · Transport

*Not yet runnable: no model has been recorded through both `argo/` and `anthropic/`. Queue item 6.*

### D8 · Plateau — does more model buy anything here

Quality proxies against what they cost. A plateau here is a plateau **on the dimensions that can be measured mechanically**, which is not the same as a plateau in answer quality; the stronger claim needs a human reading answers against `PIPELINES.md`.

| model | tier | answered | routed | ground truth (judge) | trap hits | fabricated | total tokens | mean s |
|---|---|---|---|---|---|---|---|---|
| `argo_claudeopus5` | reasoning | 15/15 | 12/12 | 5✓ / 0✗ / 1 unstated | 0 | 0 | 5,993,777 | 80.1 |
| `argo_claudesonnet45` | frontier | 9/15 | 8/12 | 3✓ / 0✗ / 2 unstated | 2 | 0 | 2,325,615 | 48.1 |
| `argo_gpt4o` | frontier | 2/2 | 2/2 | — | 0 | **unread** | 103,930 | 18.4 |

**Tools this script could not map to a server** (so their off-source status is unknown, not clean) — add them to `SERVER_PREFIX` or `BRC_TOOLS`: `get_organism`

<!-- END GENERATED -->

*The block above is written by `uv run evals/analyze.py --write` and is the only part of
this file the script may touch. Part 1 is never rewritten by a tool.*

---

## Part 3 — Pre-registration: the silent-empty padding test

**Registered 15:28, 17 Sep 2026, before any arm has run.** `runner` asked for this because two
named parties predict opposite outcomes, and a prediction written after the result is worth
nothing. Everything below is stated before a single padded question has been sent.

I own no runs and ran no model. Every number here comes from transcripts already on disk.

### What I verified of `runner`'s report

All four claims reproduce exactly against my own read of `evals/runs/argo_claudesonnet45/`.

| claim | reproduces | my numbers |
|---|---|---|
| turn-1 input separates empty from answered with zero overlap | **yes, exactly** | EMPTY n=6: 37571 37578 37582 37583 37585 37592 · ANSWERED n=9: 37553 37555 37555 37555 37557 37560 37562 37565 37567 · max answered 37567, min empty 37571, gap 4 |
| sonnet45 answered a turn carrying 140,337 input tokens | **yes** | `q10.jsonl`, turn 4 of 4, max turn input 140,337, answered |
| `argo/claudeopus5` is clean at a higher turn-1 baseline | **yes, and now stronger** | opus5 is **15/15 answered**, turn-1 range 50,102–50,133, zero empties, zero unaccounted wall time |
| all six empties have `llm_round_trips: 1` | **yes** | and `ttft_s: null` on all six against 2.45–5.49s on all nine answered |

Two structural facts I add, both from the same files. The empties return in **1.3–3.3s**
against **40.0–114.4s** for every answered question — bimodal by a factor of twelve. And the
assistant turn of an empty is literally this, with the input billed:

```json
{"role": "assistant", "text": "", "tool_calls": [], "usage": {"input_tokens": 37592, "output_tokens": 0, "total_tokens": 37592}}
```

No first token ever arrived. Whatever this is, it happens before generation, not during it.

### The one claim of `runner`'s I could not confirm

> "turn-1 input size is an almost perfect proxy for question length"

**It is not.** Question length in characters does not separate the two groups at all:

| | chars |
|---|---|
| answered | 45, 57, 61, 62, 64, 66, 83, **87**, **106** |
| EMPTY | **65**, **76**, 90, 100, 126, 136 |

Four empty questions are shorter than an answered one. `q05` is EMPTY at 65 characters while
`q10` is answered at 106. Yet `q05` carries **+18** turn-1 tokens above the baseline question
and `q10` carries **+12**. A 41-character-shorter question costing 6 more tokens is not a
length effect.

### The measurement that settles it, and it needed no model call

Hold the question constant and compare the two models. Same question text, same driver, same
process, same board — only the model differs. Offsets are each model's turn-1 input minus its
own baseline (sonnet45 37,553; opus5 50,102).

| Q | sonnet45 offset | opus5 offset | **excess on sonnet45** | sonnet45 | opus5 |
|---|---|---|---|---|---|
| q01 | +30 | +18 | **+12** | EMPTY | answered |
| q05 | +18 | +0 | **+18** | EMPTY | answered |
| q07 | +32 | +24 | **+8** | EMPTY | answered |
| q09 | +39 | +31 | **+8** | EMPTY | answered |
| q14 | +29 | +15 | **+14** | EMPTY | answered |
| q15 | +25 | +7 | **+18** | EMPTY | answered |
| q02 | +0 | +0 | 0 | answered | answered |
| q03 | +2 | +3 | −1 | answered | answered |
| q04 | +4 | +4 | 0 | answered | answered |
| q06 | +14 | +17 | −3 | answered | answered |
| q08 | +9 | +10 | −1 | answered | answered |
| q10 | +12 | +16 | −4 | answered | answered |
| q11 | +7 | +7 | 0 | answered | answered |
| q12 | +2 | +4 | −2 | answered | answered |
| q13 | +2 | +0 | +2 | answered | answered |

```
excess on the six EMPTY  : +8 +8 +12 +14 +18 +18
excess on the nine answered: -4 -3 -2 -1 -1 0 0 0 +2
max answered +2 | min empty +8 | gap 6 | zero overlap
```

**The six failing requests carried 8 to 18 tokens that the identical question did not carry on
the other model.** Question length is held exactly constant by construction here, so the
excess is not length. Something is added to exactly those six requests on sonnet45, and
exactly those six come back empty in 1.4 seconds.

*The alternative I cannot rule out from these files:* the two models may tokenize differently,
so part of the excess could be tokenizer drift. But drift would scale with question length and
would not sort perfectly by outcome — `q05` at 65 characters shows +18 while `q10` at 106
characters shows −4. A tokenizer difference cannot produce that.

### What the archive adds: the same question fails on a different board

`_archive-pre-matrix/argo_claudesonnet45` and `_premerge-1405-0f0101e/argo_claudesonnet45`
each hold four questions on a tool board **4,555 tokens smaller**:

| Q | archive turn-1 | offset | live turn-1 | offset | outcome, both runs |
|---|---|---|---|---|---|
| q02 | 32,998 | +0 | 37,553 | +0 | answered |
| q03 | 33,000 | +2 | 37,555 | +2 | answered |
| q04 | 33,002 | +4 | 37,557 | +4 | answered |
| q01 | 33,028 | **+30** | 37,583 | **+30** | **EMPTY** |

The per-question offsets are **identical across two boards and two code shas**, so the offset
is a reproducible property of the request, not noise. And `q01` is empty at 33,028 on the old
board while `q02` answers at 37,553 on the new one. **A fixed absolute turn-1 token ceiling is
therefore already falsified by records on disk** — 37,553 passes, 33,028 fails.

### Determinism, from the retries nobody recorded

Each empty was attempted three times: the driver retries a silent empty twice and only stops
early on recovery. None recovered. That is **18 of 18 attempts empty across six questions**,
plus `q01` empty on two further runs on a different board. The nine answered all answered
first try. The fault is deterministic per question, which is why a single padded call is
informative at all. *Inferred from the retry loop's control flow and the mtime gaps in Finding
8, not from the record — the transcript says `retries: 0`.*

### The three hypotheses, named before the test

- **H-size** — the number of tokens in the turn-1 request is causal; there is a boundary near
  sonnet45's 37,569 and crossing it returns nothing.
- **H-assembly** — something the harness attaches to certain questions on certain models adds
  the 8–18 excess tokens *and* causes the empty. Token count is a symptom, not the cause.
- **H-content** — a gateway-side filter rejects certain questions before generation. The six
  empties ask where resistance determinants sit, how resistance works, or whether the user
  could detect it; the nine answered mostly ask how many and give me the accession. The empty
  set is not explained by keywords alone — `q13` asks about methicillin-resistant strains and
  is answered.

H-assembly and H-content both predict that padding does nothing.

### Registered predictions

| party | arm A: pad `q12` (+~30 null tokens) | arm B: trim `q01` (−~25 tokens) | registered |
|---|---|---|---|
| `laptop_codeathon` | **flips to EMPTY** | (rescues, implied) | before 15:20, relayed by `runner` |
| `runner` | **does not flip** | — | 15:2x, in its message |
| `analyst` (me) | **does not flip**, and I hold this at high confidence | **cannot discriminate — see below** | 15:28, here |

My reason is the cross-model excess table, not a prior. If token count were causal, `q05` at
50,102 on opus5 and `q05` at 37,571 on sonnet45 would not differ in outcome, and `q01` would
not fail at 33,028 while `q02` passes at 37,553.

### A defect in arm B, before it runs

Trimming `q01` by 25 tokens **necessarily deletes its second clause** — "and where are the
fluoroquinolone-resistance mutations in it" is the only removable material; the question has
no filler. So arm B changes length and content together, in the same direction. If `q01` is
rescued, all three hypotheses predict it, and the arm has cost a call to learn nothing.
Replace it with arm C.

### The arms I would run, in order of information per call

**Arm 0 — zero model calls, do this first.** Dump the assembled turn-1 request for `q02`
(+0, answered) and `q05` (+18, EMPTY) on sonnet45 and diff them. The 18 tokens are in that
payload. This needs no gateway, no budget and no matrix time, and if it shows what is being
attached, arms A–D become unnecessary. **I cannot run it — the driver is not my file.**

**Arm C — the decisive single call.** Split `q01` into its two clauses and send each alone:

- `q01a`: "What does the E. coli GyrA protein do?" — benign framing, short
- `q01b`: "Where are the fluoroquinolone-resistance mutations in the E. coli GyrA protein?" —
  mechanistic framing, and **still below the boundary** at roughly +20 against an answered
  maximum of +14... so land it deliberately under `q06`'s +14 if the wording allows

If `q01b` comes back EMPTY while sitting below every answered question's turn-1 count, H-size
is dead and H-content is confirmed in one call. If both halves answer, length returns as a
candidate and arm A becomes worth running.

**Arm A — the hub's arm, registered as proposed.** Pad `q12` (+2) with null filler to land
near +32. Run it as specified; two parties are on record.

**Arm A2 — a better-controlled pad.** Pad `q03` ("Are there any E. coli expression studies
about ciprofloxacin?", +2, answered) up to `q07`'s +32. `q07` is the same topic and fails, so
this holds topic constant while varying only length. Sharper than `q12`.

**Arm D — content swap at matched length.** Rewrite `q05` into a counting frame and `q12` into
a mechanistic frame, each landing within ±2 tokens of the original. If outcomes swap with
length held, H-content wins directly.

### What each outcome licenses — written before the data

| result | what it licenses | what it does not |
|---|---|---|
| A flips | H-size survives; the cross-model excess needs another explanation | still does not explain `q01` failing at 33,028 while `q02` passes at 37,553 |
| A does not flip | length alone is not sufficient | does **not** establish H-content; that needs arm C or D |
| A2 does not flip but `q07` still fails | the difference is in `q07`'s added clause, not its length | which clause, and why |
| C: `q01b` empty below the boundary | H-size dead, H-content confirmed | the mechanism, and whether it is Argo or the model |
| C: both halves answer | length back in play; run A2 | — |
| D: outcomes swap at matched length | H-content confirmed directly | — |

### Requirements on whoever runs it

1. **The pad must be semantically and structurally null** — trailing filler, not a second
   clause. If the real variable is the added clause, a pad that adds a clause flips it for the
   wrong reason and we will call it size. `runner` raised this first and it is correct.
2. **Report the achieved turn-1 `input_tokens` for every arm**, not the intended one. An arm
   that missed its target tests a different question than the one registered here.
3. **n ≥ 2 per arm.** The retries give free replication on anything that comes back empty, but
   an arm that *answers* has n=1 unless it is repeated, and "it answered once" is the weaker
   of the two results.
4. **Do not run any of this during a demo.** Every arm is a real model call on the venue IP.

### What would make me wrong

If arm A flips `q12` to EMPTY with a genuinely null pad, my prediction is wrong and I will say
so here in the same words I used to register it. The cross-model excess table would then need
an explanation I do not have, and H-size would deserve the benefit of it.

*Limits on everything in Part 3: one model row of 15 for the failures, one of 15 for the
control, one afternoon, one gateway. The excess-token table rests on usage figures reported by
Argo, which I have not independently verified against a tokenizer. Nothing here is replicated
across a second sonnet45 run on the current board.*
