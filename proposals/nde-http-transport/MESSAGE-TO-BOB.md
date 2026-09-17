# Message to Bob (Slack) — Bobby sends this, nobody else

Hi Bob — your `nde_mcp` server is the entry point the whiteboard stars, but `chatbot.py`
can't reach it yet: every `MCP_SERVERS` entry there is `streamable_http` and your
`main()` runs stdio, so the whole Discovery hop is dark in the demo.

I put a proposal in `proposals/nde-http-transport/` on my branch. It's one file that
imports your package unchanged and serves your 9 tools on port 8009 at `/mcp-nde`, in
the same pattern as `geo.py` and `brc_analytics.py` — I didn't touch a line of your code
and I'm not committing anything under `NIAID-Data-Ecosystem/`. I tested it against the
repo's pins: all 9 tools load over HTTP, `nde_raw_query` returns 60,107 for
`infectiousAgent.name:"escherichia coli"`, and it's about 10x faster per call than the
stdio alternative. One thing worth a look regardless of transport — production lists
BV-BRC in `nde_list_repositories` with 118,625 records but returns 0 for every query
against it, while staging returns the 118,625, so the wrapper stamps the answering host
on each response.

It's your code and your call: happy to open a small PR with the wrapper plus the two
registration lines, or to hand you the file and stay out of it. The README has the real
output and the alternative I considered.
