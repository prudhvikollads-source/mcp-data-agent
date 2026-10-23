"""Agent client: connects to the MCP server over stdio and answers questions.

The loop is deliberately explicit:

1. spawn the server as a subprocess and initialise a session,
2. discover its tools (``list_tools``),
3. ask the planner for the next step,
4. execute that tool call through the session,
5. feed the result back to the planner as an observation,
6. repeat until the planner stops, then summarise.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .db import db_path_from_env, ensure_database
from .llm import MockLLM, Observation, Planner, planner_from_env

MAX_STEPS = 12


@dataclass
class AgentResult:
    question: str
    answer: str
    trace: list[Observation] = field(default_factory=list)
    tools_discovered: list[str] = field(default_factory=list)


def _server_params(db_path: Path | None = None) -> StdioServerParameters:
    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
    if db_path is not None:
        env["MCP_DATA_AGENT_DB"] = str(db_path)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_data_agent.server"],
        env=env,
    )


def _parse_tool_text(raw: Any) -> Any:
    """MCP tool results arrive as content blocks; ours carry JSON text."""
    if hasattr(raw, "content"):
        text = "".join(getattr(block, "text", "") for block in raw.content)
    else:
        text = str(raw)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


class DataAgent:
    def __init__(self, planner: Planner | None = None, db_path: Path | str | None = None):
        self.planner: Planner = planner if planner is not None else planner_from_env()
        self.db_path = Path(db_path) if db_path else db_path_from_env()

    async def ask(self, question: str) -> AgentResult:
        ensure_database(self.db_path)
        observations: list[Observation] = []
        async with stdio_client(_server_params(self.db_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                discovered = [tool.name for tool in listing.tools]

                for _ in range(MAX_STEPS):
                    step = self.planner.next_step(question, observations)
                    if step is None:
                        break
                    raw = await session.call_tool(step.name, step.arguments)
                    result = _parse_tool_text(raw)
                    observations.append(Observation(step.name, step.arguments, result))

        return AgentResult(
            question=question,
            answer=self.planner.summarise(question, observations),
            trace=observations,
            tools_discovered=discovered,
        )


async def ask_async(question: str, **kwargs: Any) -> AgentResult:
    return await DataAgent(**kwargs).ask(question)


def ask(question: str, **kwargs: Any) -> AgentResult:
    """Synchronous convenience wrapper used by the demo and the tests."""
    import asyncio

    return asyncio.run(ask_async(question, **kwargs))
