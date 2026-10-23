"""Runnable offline demo.

Usage::

    python -m mcp_data_agent.demo "which plan has the most subscribers?"

Spawns the MCP server over stdio, discovers its tools, runs the
planner -> tool-call loop, and prints every tool call plus the answer.
No API keys and no network are needed: the default planner is the
deterministic :class:`~mcp_data_agent.llm.MockLLM`.
"""

from __future__ import annotations

import json
import sys

from .client import ask

DEFAULT_QUESTION = "which plan has the most subscribers?"


def _short(value: object, limit: int = 320) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "..."


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    question = " ".join(args).strip() or DEFAULT_QUESTION

    result = ask(question)

    print(f"Question: {question}")
    print(f"Tools discovered: {', '.join(result.tools_discovered)}")
    print("Tool calls:")
    for i, obs in enumerate(result.trace, start=1):
        call = f"{obs.tool}({json.dumps(obs.arguments, default=str)})" if obs.arguments else f"{obs.tool}()"
        print(f"  {i}. {call}")
        print(f"     -> {_short(obs.result)}")
    print(f"Final answer: {result.answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
