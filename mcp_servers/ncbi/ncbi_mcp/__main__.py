"""Entry point: ``python -m ncbi_mcp`` or the ``fdea-ncbi-mcp`` console script.

Speaks MCP over stdio, so stdout belongs to the protocol. Anything this server
needs to say to a human goes to stderr --- a stray print() to stdout corrupts
the JSON-RPC stream and the client disconnects with an opaque parse error.
"""

from __future__ import annotations

import sys

from .server import run


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
