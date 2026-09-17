"""Run every MCP server in mcp_servers/, each on its own port.

Usage:
    uv run run_mcp_servers.py
"""

import subprocess

SERVERS = ["pdn.py", "mygene.py", "uniprot.py", "myvariant.py", "ncbi.py"]

procs = [subprocess.Popen(["uv", "run", f"mcp_servers/{name}"]) for name in SERVERS]

try:
    for proc in procs:
        proc.wait()
except KeyboardInterrupt:
    pass
finally:
    for proc in procs:
        proc.terminate()
