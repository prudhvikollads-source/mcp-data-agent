"""Core tool implementations, independent of the transport.

The MCP server is a thin wrapper around these functions, which keeps the
logic directly unit-testable without spawning a process.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .db import db_path_from_env, ensure_database
from .safety import MAX_ROWS, QUERY_TIMEOUT_SECONDS, UnsafeQueryError, validate_and_limit

__all__ = ["list_tables", "describe_table", "run_query", "UnsafeQueryError"]


def _connect_readonly(db_path: Path, *, with_authorizer: bool = False) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    if not with_authorizer:
        # Trusted internal queries (list / describe) run without the
        # authorizer: even table-valued pragmas are denied by it. Only
        # user-supplied SQL in run_query gets the authorizer layer.
        return conn

    def authorizer(action, _arg1, _arg2, _dbname, _source):
        allowed = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION)
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    return conn


def _resolve(path: Path | str | None) -> Path:
    db_path = Path(path) if path else db_path_from_env()
    return ensure_database(db_path)


def list_tables(db_path: Path | str | None = None) -> dict[str, Any]:
    path = _resolve(db_path)
    conn = _connect_readonly(path)
    try:
        names = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for name in names:
            count = conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
            tables.append({"name": name, "row_count": int(count)})
        return {"tables": tables}
    finally:
        conn.close()


def describe_table(table: str, db_path: Path | str | None = None) -> dict[str, Any]:
    path = _resolve(db_path)
    conn = _connect_readonly(path)
    try:
        valid = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if table not in valid:
            raise ValueError(f"Unknown table: {table!r}. Known tables: {sorted(valid)}")
        columns = [
            {
                "name": row["name"],
                "type": row["type"],
                "nullable": not bool(row["notnull"]),
                "primary_key": bool(row["pk"]),
            }
            for row in conn.execute(
                "SELECT name, type, \"notnull\", pk FROM pragma_table_info(?)", (table,)
            )
        ]
        return {"table": table, "columns": columns}
    finally:
        conn.close()


def run_query(
    sql: str,
    row_limit: int | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run one validated read-only SELECT and return rows as dicts."""
    # PRAGMA table_info in describe_table uses a separate trusted path; here
    # the authorizer would block PRAGMA anyway, and validation rejects it.
    safe_sql = validate_and_limit(sql, row_limit=row_limit)
    effective_limit = MAX_ROWS if row_limit is None else max(1, min(int(row_limit), MAX_ROWS))

    path = _resolve(db_path)
    conn = _connect_readonly(path, with_authorizer=True)
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    calls = {"n": 0}

    def progress() -> int:
        calls["n"] += 1
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(progress, 1000)
    # Probe one row past the *applied* limit to learn whether results were
    # truncated. The applied limit is the smaller of the caller's cap and
    # any LIMIT already in the (validated) SQL: execute with LIMIT n+1,
    # report the capped SQL, return only n rows.
    import re

    match = re.search(r"\blimit\s+(\d+)\s*$", safe_sql, flags=re.IGNORECASE)
    applied_limit = min(effective_limit, int(match.group(1))) if match else effective_limit
    probe_sql = re.sub(
        r"\blimit\s+\d+\s*$", f"LIMIT {applied_limit + 1}", safe_sql, flags=re.IGNORECASE
    )
    try:
        cursor = conn.execute(probe_sql)
        fetched = cursor.fetchmany(applied_limit + 1)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        truncated = len(fetched) > applied_limit
        rows = [dict(row) for row in fetched[:applied_limit]]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "limit": effective_limit,
            "truncated": truncated,
            "sql": safe_sql,
        }
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise TimeoutError(
                f"Query exceeded the {QUERY_TIMEOUT_SECONDS:.0f}s timeout."
            ) from exc
        raise
    finally:
        conn.close()
