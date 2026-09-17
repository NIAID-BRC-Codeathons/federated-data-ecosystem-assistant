"""Run every MCP server in mcp_servers/, each on its own port.

Usage:
    uv run run_mcp_servers.py
"""

import subprocess

SERVERS = [
    "pdn.py",            # 8001
    "mygene.py",         # 8002
    "uniprot.py",        # 8003
    "myvariant.py",      # 8004
    "ncbi.py",           # 8005
    "brc_analytics.py",  # 8006
    "geo.py",            # 8007
]

procs = [subprocess.Popen(["uv", "run", f"mcp_servers/{name}"]) for name in SERVERS]

try:
    for proc in procs:
        proc.wait()
except KeyboardInterrupt:
    pass
finally:
    for proc in procs:
        proc.terminate()
