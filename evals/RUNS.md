# Run registry

Every run of `evals/run_questions.py`, appended automatically.

A run is identified by its start time and the git SHA of the code that
produced it. `-dirty` in a run id means the tree had uncommitted changes,
which is the usual reason two otherwise identical runs disagree.

**Status is set by hand.** A run lands as `exploratory`. Mark one `final`
only when its numbers are the ones being quoted, and mark a superseded run
`void` with a reason rather than deleting it -- a void run is still the
evidence for why it was voided.

`empty` and `denied` count TRANSPORT FAULTS including attempts that were
retried away. They are not model results, and a run with a high count in
either column must not be compared against one without.

| run id | started | branch | questions file | models | qs | prompts | reps | rows | denied | empty | errors | status | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `20260917-143722-fa084aa-dirty` | 2026-09-17 14:37:22 | bobby/ncbi-and-brc-analytics | `evals\QUESTIONS.md` | 1 | 2 | paper | 1 | 2 | 0 | 0 | 0 | exploratory | smoke: verify run_id, code_sha, attempts_discarded and RUNS.md on patched driver 39bb6ad |
