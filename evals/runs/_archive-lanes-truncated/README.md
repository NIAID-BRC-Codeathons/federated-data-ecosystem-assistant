# Archived: the second BOBBY-LANES attempt, answers truncated at 4096 tokens/turn

Ran 15:52–16:18, about 174 of 560 transcripts, 10 models with scorecards.
**Not deleted, and not comparable to the run that replaced it.**

Every long answer here is cut mid-sentence. The Argo branch of
`load_chat_model()` carried `extra_body={"max_tokens": 4096}`, and that cap is
**per round trip** — which is why the summary figures hide it. One question
recorded 10,184 output tokens and still ended mid-word, because that total is
several capped turns.

It is not cosmetic. The system prompt asks for a paper with Discussion,
Limitations and References **last**, so the cut lands exactly on the caveats, the
named alternatives and the provenance URLs — the content every scorer looks for.
A headline refusal case was nearly filed as a flat failure when the refusal had
simply been cut off before it could appear.

These transcripts also predate three server-side fixes: the 40,000-character
payload cap (tool results reached 1,073,223 characters and caused hard HTTP
400s), the shared NCBI rate budget, and the stub transcript written when a
question raises.

Kept because a stopped run is still the evidence for why it was stopped, and
because clearing a directory another agent was reading has already destroyed one
set of transcripts on this project. **Do not score these and do not mix them with
the replacement.**
