"""NCBI MCP Server

A remote MCP server exposing two NCBI services as 17 tools: E-utilities (SRA
sequencing runs, BioSample, BioProject, Taxonomy, Gene, Assembly and sequence
databases) and the Pathogen Detection Isolates Browser, a curated index of
bacterial isolates with computed antimicrobial-resistance genotypes. PubMed is
not among them: the standalone pubmed server covers it.

Run over HTTP:  python mcp_servers/ncbi.py
Run over stdio: python mcp_servers/ncbi.py --stdio

Unlike its siblings, the tools live in the ncbi_lib package next to this file
rather than inline. NCBI rate-limits per source IP at 3 requests/second, so
every call has to pass one process-global limiter; that pacing, the retry and
error handling around it, and the provenance each result carries come to ~3,300
lines, which is more than belongs in one module. ncbi_lib/README.md explains
the split. This file is the entry point the repo's conventions ask for.
"""

import sys
from pathlib import Path

# Running this as a script puts mcp_servers/ on sys.path, so `ncbi_lib`
# imports cleanly. Importing it as a module (the tests do) does not, so add it.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ncbi_lib.server import DEFAULT_PORT, HTTP_PATH
from ncbi_lib.server import server as mcp

__all__ = ["DEFAULT_PORT", "HTTP_PATH", "mcp"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NCBI MCP server")
    parser.add_argument("--stdio", action="store_true", help="run over stdio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port")
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.port = args.port
        print(f"NCBI MCP Server starting on http://localhost:{args.port}{HTTP_PATH} ...")
        mcp.run(transport="streamable-http")
