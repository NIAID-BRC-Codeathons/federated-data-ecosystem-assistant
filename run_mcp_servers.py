"""Run every MCP server, each on its own port.

Usage:
    uv run run_mcp_servers.py
"""

import subprocess

SERVERS = ["pdn.py", "mygene.py", "uniprot.py", "myvariant.py", "ncbi.py", "pubmed.py"]

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
