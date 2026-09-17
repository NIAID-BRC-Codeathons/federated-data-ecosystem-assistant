# Fixtures that make `judge.py` come back dirty

A check that has never been seen to **fail** is not a passing check — it is equally
consistent with a check that cannot see. Every flag in `evals/judge.py` is therefore
exercised here in both directions, from transcripts small enough to read.

```bash
uv run evals/judge.py --runs evals/judge-fixtures --out /tmp/fixtures.md
```

| fixture | what it proves | expected verdict |
|---|---|---|
| `fixture-good/q03` | a clean answer scores clean | routed yes · no traps · no unmatched numbers |
| `fixture-good/q15` | the full refusal passes | `declines ✓ · reason ✓ · source ✓` |
| `fixture-bad/q03` | every trap can fire at once | `zero_as_absence` **1**, plus `ena_keywords`, `geo_no_entry_type`, `pathogen_wrong_group`, `gds_513`, `ena_50` |
| `fixture-bad/q15` | a bare decline scores nothing | `**no decline** · **no reason** · **no source**` |
| `fixture-source-no-reason/q14` | naming a source is not giving a reason | `declines ✓ · **no reason** · source ✓` |
| `corrupt-complete/q02` | the fabrication check fires | `**fabricated**: 987,654, 1,234,567` |
| `corrupt-truncated/q02` | and refuses to fire when it cannot see | `unmatched: 987,654, 1,234,567` |

## The two that matter most

**`fixture-source-no-reason`** is not invented. It is the shape `argo/gpt4o` produced on
17 Sep 2026 on all four gap questions: it declined, it named BV-BRC or CARD, and it never
said *why* the question could not be answered here. Collapsing "named a source" and "gave
a reason" into one flag would have scored that run 4/4 and hidden the harder half.

**`corrupt-*`** are the same real transcript twice. `corrupt-complete` has `result_chars`
trimmed to the excerpt length, so the evidence is whole and an unmatched number is
decidably fabricated. `corrupt-truncated` is the transcript as `run_questions.py` actually
records it — 600-character excerpts — where the same number is only `unmatched`, meaning a
human has to look. Both carry two invented numbers appended to the answer. If a change to
`judge.py` ever makes these two report the same thing, the truncation guard has been lost.

## Provenance

`corrupt-*/q02.jsonl` is `evals/runs/argo_claudesonnet45/q02.jsonl` from the 17 Sep matrix
with two edits, both listed above. The rest are hand-written to the same schema. Nothing
here was produced by a model call, so none of it costs NCBI budget or an Argo token to
re-run.
