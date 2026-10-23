"""LLM / planner interface.

The agent never talks to a model directly; it talks to a planner with two
methods:

* ``next_step(question, observations)`` -- return a tool call to make, or
  ``None`` when the observations already answer the question.
* ``summarise(question, observations)`` -- turn the final observation into
  a plain-English answer.

The default planner, :class:`MockLLM`, is deterministic and heuristic: it
always explores the schema first (list, then describe the relevant
tables), then builds one aggregate SQL query from the question pattern.
That makes the full client <-> server loop runnable offline, with no API
keys and no network.

A real model can be dropped in behind the same interface. When the
environment variables ``MCP_DATA_AGENT_LLM_ENDPOINT``,
``MCP_DATA_AGENT_LLM_MODEL`` and ``MCP_DATA_AGENT_LLM_API_KEY`` are all
set, :func:`planner_from_env` returns :class:`EnvLLM`, which POSTs the
same prompt shape to that endpoint (a generic chat-completions style JSON
API) using only the standard library, and expects a JSON tool call back.
No provider is hardcoded anywhere in this codebase.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    tool: str
    arguments: dict[str, Any]
    result: Any


class Planner(Protocol):
    def next_step(self, question: str, observations: list[Observation]) -> ToolCall | None: ...
    def summarise(self, question: str, observations: list[Observation]) -> str: ...


# --------------------------------------------------------------------------
# Deterministic offline planner
# --------------------------------------------------------------------------

_TABLE_KEYWORDS = {
    "plans": ("plan", "plans", "price", "pricing"),
    "subscriptions": ("subscription", "subscribers", "subscriber", "subscribed", "churn"),
    "customers": ("customer", "customers", "city", "country", "segment"),
    "invoices": ("invoice", "invoices", "revenue", "paid", "unpaid", "overdue", "spending", "spent", "billing"),
}
_CITIES = [
    "Austin", "Toronto", "London", "Berlin", "Dubai", "Singapore",
    "Sydney", "Bangalore", "Amsterdam", "Auckland",
]


class MockLLM:
    """Rule-based planner. Deterministic for a given question."""

    name = "mock"

    # -- planning ------------------------------------------------------
    def _relevant_tables(self, question: str) -> list[str]:
        q = question.lower()
        hits = [t for t, keys in _TABLE_KEYWORDS.items() if any(k in q for k in keys)]
        # Joins need both sides described before querying.
        if "plans" in hits and "subscriptions" not in hits and "subscriber" in q:
            hits.append("subscriptions")
        if "revenue" in q or "spending" in q:
            for t in ("invoices", "customers", "plans"):
                if t not in hits:
                    hits.append(t)
        return hits or ["customers", "plans", "subscriptions", "invoices"]

    def build_query(self, question: str) -> str:
        q = question.lower()

        city = next((c for c in _CITIES if c.lower() in q), None)
        if city and "customer" in q:
            return (
                "SELECT city, COUNT(*) AS customers FROM customers "
                f"WHERE city = '{city}' GROUP BY city"
            )
        if "most subscribers" in q or ("which plan" in q and "subscriber" in q) or "most popular plan" in q:
            return (
                "SELECT p.name AS plan, COUNT(*) AS subscribers "
                "FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
                "GROUP BY p.name ORDER BY subscribers DESC LIMIT 5"
            )
        if "revenue" in q:
            return (
                "SELECT ROUND(SUM(amount_usd), 2) AS total_revenue_usd, COUNT(*) AS paid_invoices "
                "FROM invoices WHERE status = 'paid'"
            )
        if "average" in q and "invoice" in q:
            return "SELECT ROUND(AVG(amount_usd), 2) AS average_invoice_usd FROM invoices"
        if "unpaid" in q or "overdue" in q:
            return (
                "SELECT status, COUNT(*) AS invoices, ROUND(SUM(amount_usd), 2) AS amount_usd "
                "FROM invoices WHERE status != 'paid' GROUP BY status ORDER BY invoices DESC"
            )
        if "top customer" in q or "highest spending" in q or "spend the most" in q:
            return (
                "SELECT c.name AS customer, ROUND(SUM(i.amount_usd), 2) AS total_spent_usd "
                "FROM invoices i JOIN customers c ON c.id = i.customer_id "
                "WHERE i.status = 'paid' GROUP BY c.name ORDER BY total_spent_usd DESC LIMIT 5"
            )
        if "how many customers" in q or "number of customers" in q:
            return "SELECT COUNT(*) AS customers FROM customers"
        if "how many subscriptions" in q or "active subscriptions" in q:
            return (
                "SELECT status, COUNT(*) AS subscriptions FROM subscriptions "
                "GROUP BY status ORDER BY subscriptions DESC"
            )
        if "plan" in q and ("price" in q or "cost" in q or "expensive" in q):
            return (
                "SELECT name AS plan, monthly_price_usd FROM plans "
                "ORDER BY monthly_price_usd DESC LIMIT 5"
            )
        return "SELECT COUNT(*) AS customers FROM customers"

    def next_step(self, question: str, observations: list[Observation]) -> ToolCall | None:
        done = [o.tool for o in observations]
        if "list_tables" not in done:
            return ToolCall("list_tables")

        described = {
            o.arguments.get("table") for o in observations if o.tool == "describe_table"
        }
        for table in self._relevant_tables(question):
            if table not in described:
                return ToolCall("describe_table", {"table": table})

        if "run_query" not in done:
            return ToolCall("run_query", {"sql": self.build_query(question)})
        return None

    # -- answering -----------------------------------------------------
    def summarise(self, question: str, observations: list[Observation]) -> str:
        queries = [o for o in observations if o.tool == "run_query"]
        if not queries:
            return "I explored the schema but did not run a query, so I cannot answer yet."
        result = queries[-1].result
        if isinstance(result, dict) and result.get("error"):
            return f"The query failed: {result['error']}"
        rows = result.get("rows", []) if isinstance(result, dict) else []
        if not rows:
            return "The query returned no rows."
        first = rows[0]
        q = question.lower()
        if "subscriber" in q or "popular plan" in q:
            return (
                f"The plan with the most subscribers is {first['plan']} "
                f"with {first['subscribers']} subscriptions."
            )
        if "revenue" in q:
            return (
                f"Total revenue from paid invoices is ${first['total_revenue_usd']:,.2f} "
                f"across {first['paid_invoices']} paid invoices."
            )
        if "average" in q and "invoice" in q:
            return f"The average invoice amount is ${first['average_invoice_usd']:,.2f}."
        if len(rows) == 1:
            pairs = ", ".join(f"{k} = {v}" for k, v in first.items())
            return f"Answer: {pairs}."
        lines = "; ".join(
            ", ".join(f"{k} = {v}" for k, v in row.items()) for row in rows
        )
        return f"Results ({len(rows)} rows): {lines}."


# --------------------------------------------------------------------------
# Optional real provider, configured by environment only
# --------------------------------------------------------------------------

class EnvLLM:
    """Planner backed by a generic HTTP chat-completions style endpoint.

    Configuration (environment only, nothing hardcoded):
    MCP_DATA_AGENT_LLM_ENDPOINT, MCP_DATA_AGENT_LLM_MODEL,
    MCP_DATA_AGENT_LLM_API_KEY.
    """

    name = "env"

    def __init__(self) -> None:
        self.endpoint = os.environ["MCP_DATA_AGENT_LLM_ENDPOINT"]
        self.model = os.environ["MCP_DATA_AGENT_LLM_MODEL"]
        self.api_key = os.environ["MCP_DATA_AGENT_LLM_API_KEY"]
        self._mock = MockLLM()  # summarising stays local and deterministic

    def _ask(self, prompt: str) -> str:
        body = json.dumps(
            {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        ).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - user-configured endpoint
            payload = json.loads(resp.read())
        return payload["choices"][0]["message"]["content"]

    def next_step(self, question: str, observations: list[Observation]) -> ToolCall | None:
        history = [
            {"tool": o.tool, "arguments": o.arguments, "result": o.result}
            for o in observations
        ]
        prompt = (
            "You are a data agent. Tools: list_tables(), "
            "describe_table(table), run_query(sql) -- SELECT only.\n"
            f"Question: {question}\nObservations so far: {json.dumps(history, default=str)}\n"
            'Reply with ONLY JSON: {"tool": "<name>", "arguments": {...}} '
            'or {"tool": null} when the observations answer the question.'
        )
        raw = self._ask(prompt)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        if not data.get("tool"):
            return None
        return ToolCall(data["tool"], data.get("arguments") or {})

    def summarise(self, question: str, observations: list[Observation]) -> str:
        return self._mock.summarise(question, observations)


def planner_from_env() -> Planner:
    keys = ("MCP_DATA_AGENT_LLM_ENDPOINT", "MCP_DATA_AGENT_LLM_MODEL", "MCP_DATA_AGENT_LLM_API_KEY")
    if all(os.environ.get(k) for k in keys):
        return EnvLLM()
    return MockLLM()
