"""Entry point: ``python -m ncbi_mcp`` or the ``fdea-ncbi-mcp`` console script.

Two transports, matching the convention the other servers in this repo use:
HTTP by default so ``chatbot.py``'s MultiServerMCPClient can reach it, and
``--stdio`` for a client that launches the server itself (Claude Code, and the
``.mcp.json`` next to this package).

Under ``--stdio``, stdout belongs to the protocol. Anything this server needs to
say to a human goes to stderr --- a stray print() to stdout corrupts the
JSON-RPC stream and the client disconnects with an opaque parse error. That is
why the startup line below is printed only on the HTTP path.
"""

from __future__ import annotations

import argparse
import sys

from .server import DEFAULT_PORT, HTTP_PATH, run, run_http


def main() -> None:
    parser = argparse.ArgumentParser(prog="fdea-ncbi-mcp", description=__doc__)
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Speak MCP over stdio instead of HTTP.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="HTTP port (default: %(default)s).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "HTTP bind address (default: %(default)s). This server has no "
            "auth and spends a shared per-IP NCBI budget; think before "
            "binding it to 0.0.0.0."
        ),
    )
    args = parser.parse_args()

    try:
        if args.stdio:
            run()
        else:
            # flush=True: stdout is block-buffered when redirected to a log,
            # so without it this line lands after uvicorn's output.
            print(
                f"NCBI MCP Server starting on "
                f"http://{args.host}:{args.port}{HTTP_PATH} ...",
                flush=True,
            )
            run_http(host=args.host, port=args.port)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
