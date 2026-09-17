# Pre-matrix archive

Everything here predates the base matrix and **must not be scored**. Moved out of
`evals/runs/<model>/` by `runner` at 14:40 on 17 Sep, before launching the matrix.

Both `evals/judge.py:890` and `evals/analyze.py:366` find records with
`model_dir.glob("q*.jsonl")`. They are one directory deep, so files under this
archive are outside their reach — `analyze.py:368` notes the nesting instead of
reading it. That is the point of moving rather than deleting.

## Why it had to move

The driver changed its filename scheme in the 14:32 patch: records are now
`q2.jsonl`, not `q02.jsonl`. Old and new therefore coexist in one directory and a
single glob returns both. Observed in `argo_gpt4o/` at 14:39:

    11 files: q01 q02 q03 q04 q05 q06 q07 q08 q09 q2 q3
    question 2 appears in: ['q02.jsonl', 'q2.jsonl']
    question 3 appears in: ['q03.jsonl', 'q3.jsonl']

`sorted()` puts `q02.jsonl` before `q2.jsonl`, so the stale copy is read first.
The stale copies carry no `run_id` and no `code_sha` — the fields that would have
exposed the mix read as absent, not as wrong.

## What each directory holds

- `argo_gpt4o/q01..q09.jsonl` — the void 14:05 matrix. Pre-merge, GEO absent
  because a stale server held port 8007. Nine questions, then the run was stopped.
- `argo_gpt4o/q2.jsonl`, `q3.jsonl` — runner's 14:37 smoke of the patched driver
  at `fa084aa-dirty`. Valid, but two questions only. Superseded by the matrix.
- `argo_claudeopus5/q01.jsonl`, `q03.jsonl` — runner's 14:25 probe confirming the
  `init_agent()` fix. Valid post-merge data, written by the pre-provenance driver,
  so no `run_id`. Q1 twelve tool calls in 87.6s, Q3 `geo_search` twice in 52.8s.
- `argo_claudesonnet45/` — the void 14:05 matrix. Q1 is a silent empty (0 tool
  calls, 0 output tokens, 33,028 input tokens billed, `denied: false`); Q4 was
  denied mid-answer after 14 tool calls and the driver then wrote off Q5-Q15.
  This is the evidence for the intermittent-denial finding, not model behaviour.

Unpacked copies of the 14:05 run also live in `_premerge-1405-0f0101e/`, archived
before a probe overwrote the originals.
