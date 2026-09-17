"""Run every MCP server, each on its own port.

Usage:
    uv run run_mcp_servers.py
"""

import os
import subprocess

from dotenv import load_dotenv

# Load .env HERE, in the parent, because Popen passes the parent's environment to
# every child and `uv run` does not read .env on its own.
#
# Without this, NCBI_API_KEY reached exactly one server. pubmed.py calls
# load_dotenv() itself, so it was fine; geo.py and ncbi_lib/eutils.py read
# os.environ directly and both got an empty string. The effect was invisible and
# expensive: geo.py's `if NCBI_API_KEY: MIN_REQUEST_GAP = 0.15` branch never fired,
# so it kept the anonymous 3 req/sec pace and omitted the api_key parameter, and
# ncbi_lib set self.api_key = None the same way. At a venue where every laptop
# shares one egress IP, that is our own 10 req/sec traded for 3 req/sec split with
# the whole room -- and a likely source of the 429s we have been seeing while
# pacing at 2 req/sec from a single thread.
#
# Found by the runner chat, 17 Sep, by testing the key's visibility inside a
# `uv run` child rather than in the shell where it looked present.
load_dotenv()

# Then say out loud which pace we are actually running at.
#
# The plumbing above was only half the fault. On 17 Sep the key was ALSO empty:
# `.env` carried the line `NCBI_API_KEY=` with nothing after it, so even a correct
# load produced an empty string and every NCBI server quietly fell back to the
# anonymous 3 req/sec. Nothing anywhere said so. That is the same failure this
# project keeps meeting -- an empty value standing in for "I could not look" -- and
# the cure is the same: make the absence say its own name at startup.
#
# Never print the key, only whether there is one.
if os.environ.get("NCBI_API_KEY", "").strip():
    print("NCBI_API_KEY loaded -- NCBI servers may pace at 10 req/sec on this key.")
else:
    print(
        "WARNING: no NCBI_API_KEY. Every NCBI server falls back to 3 req/sec, and\n"
        "         that ceiling is PER IP, shared with everyone on this network --\n"
        "         not per process and not per laptop. A long eval matrix will see\n"
        "         429s that look like tool failures. A key is free from an NCBI\n"
        "         account (Account settings -> API Key Management); put it in .env\n"
        "         as NCBI_API_KEY=<key> and restart these servers."
    )

SERVERS = [
    "pdn.py",            # 8001
    "mygene.py",         # 8002
    "uniprot.py",        # 8003
    "myvariant.py",      # 8004
    "ncbi.py",           # 8005
    "pubmed.py",         # 8006
    "geo.py",            # 8009
    "brc_analytics.py",  # 8008
]

procs = [subprocess.Popen(["uv", "run", f"mcp_servers/{name}"]) for name in SERVERS]

# The NDE server lives in its own subdirectory with a separate pyproject.toml.
procs.append(
    subprocess.Popen(
        ["uv", "run", "python", "-m", "nde_mcp.server"],
        cwd="NIAID-Data-Ecosystem",
    )
)

try:
    for proc in procs:
        proc.wait()
except KeyboardInterrupt:
    pass
finally:
    for proc in procs:
        proc.terminate()
