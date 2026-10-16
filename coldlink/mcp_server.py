"""Coldlink MCP server: the seven graph tools (plus read and action tools)
over the lab's FalkorDB graph, for any MCP client (Claude Desktop, Claude Code,
or the Lead agent).

Run:  python -m coldlink.mcp_server                 (stdio)
      python -m coldlink.mcp_server --http 8090     (streamable HTTP)

The official FalkorDB MCP server (falkordb/mcpserver, in docker-compose) can
run alongside it for raw read-only Cypher; Coldlink's server adds the domain
tools whose scoring is fixed in Cypher.
"""
from __future__ import annotations

import argparse

from . import tools as T

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

ACTIONS = [T.request_retest, T.reopen_decision, T.hold_link, T.notify_officer, T.set_link_status, T.action_log,
           T.start_run]

server = _Server("coldlink", instructions=(
    "Forensic lab graph tools. Propose links between evidence; never decide guilt or name a suspect. "
    "Action tools need a run_id from start_run."))

for fn in T.CORE_TOOLS + T.SUPPORT_TOOLS + ACTIONS:
    server.tool()(fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=None, help="serve streamable HTTP on this port")
    a = ap.parse_args()
    if a.http:
        server.run("streamable-http", port=a.http) if _Server.__name__ == "MCPServer" else server.run("streamable-http")
    else:
        server.run()


if __name__ == "__main__":
    main()
