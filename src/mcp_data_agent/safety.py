"""Read-only enforcement for user-supplied SQL.

Defence in depth, four independent layers:

1. Lexical validation here: a single statement, starting with SELECT
   (or WITH ... SELECT), containing no write / DDL / admin keywords and
   no SQL comments that could hide a second payload.
2. A capped LIMIT is always applied, whatever the query asked for.
3. The SQLite connection is opened read-only (``mode=ro`` URI).
4. A SQLite authorizer callback denies every non-read action at the
   engine level, plus a progress-handler deadline enforces a timeout.
"""

from __future__ import annotations

import re

MAX_ROWS = 100
QUERY_TIMEOUT_SECONDS = 5.0

_FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "grant", "revoke", "pragma", "attach", "detach", "vacuum",
    "reindex", "analyze", "begin", "commit", "rollback", "savepoint",
    "release", "into",
}
_WORD_RE = re.compile(r"[A-Za-z_]+")
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\s*$", re.IGNORECASE)


class UnsafeQueryError(ValueError):
    """Raised when a query fails read-only validation."""


def validate_and_limit(sql: str, row_limit: int | None = None) -> str:
    """Validate *sql* and return it with a capped LIMIT applied."""
    if not isinstance(sql, str) or not sql.strip():
        raise UnsafeQueryError("Query must be a non-empty string.")

    text = sql.strip()
    if "--" in text or "/*" in text or "*/" in text:
        raise UnsafeQueryError("SQL comments are not allowed.")

    # One statement only: a single optional trailing semicolon.
    body = text[:-1].rstrip() if text.endswith(";") else text
    if ";" in body:
        raise UnsafeQueryError("Multiple statements are not allowed.")

    words = [w.lower() for w in _WORD_RE.findall(body)]
    if not words:
        raise UnsafeQueryError("Query contains no SQL.")
    if words[0] == "with":
        if "select" not in words:
            raise UnsafeQueryError("WITH queries must end in a SELECT.")
    elif words[0] != "select":
        raise UnsafeQueryError("Only SELECT queries are allowed.")

    forbidden = sorted(set(words) & _FORBIDDEN)
    if forbidden:
        raise UnsafeQueryError(
            f"Forbidden keyword(s) in read-only query: {', '.join(forbidden)}."
        )

    limit = MAX_ROWS if row_limit is None else max(1, min(int(row_limit), MAX_ROWS))
    match = _LIMIT_RE.search(body)
    if match:
        requested = int(match.group(1))
        if requested > limit:
            body = body[: match.start()] + f"LIMIT {limit}"
    else:
        body = f"{body} LIMIT {limit}"
    return body
