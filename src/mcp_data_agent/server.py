"""MCP server exposing the data tools over stdio.

Built on the official Python ``mcp`` SDK (v1 line; see pyproject.toml).
Run it directly with::

    python -m mcp_data_agent.server

It speaks only on stdin/stdout, as the protocol requires -- diagnostics
go to stderr.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import tools as core

mcp = FastMCP("mcp-data-agent")


@mcp.tool()
def list_tables() -> str:
    """List every table in the database with its row count."""
    return json.dumps(core.list_tables())


@mcp.tool()
def describe_table(table: str) -> str:
    """Describe one table: column names, types, nullability, primary key."""
    return json.dumps(core.describe_table(table))


@mcp.tool()
def run_query(sql: str, row_limit: int = 100) -> str:
    """Run a single read-only SELECT query.

    Writes, DDL, multiple statements and SQL comments are rejected.
    Results are capped at 100 rows and queries time out after 5 seconds.
    """
    try:
        return json.dumps(core.run_query(sql, row_limit=row_limit))
    except Exception as exc:  # surfaced to the client as tool output text
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
