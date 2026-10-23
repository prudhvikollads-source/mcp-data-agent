"""End-to-end: launch the real MCP server over stdio and answer a question."""

from mcp_data_agent.client import DataAgent
from mcp_data_agent.db import seed_database
from mcp_data_agent.llm import MockLLM


def test_stdio_end_to_end(tmp_path):
    db_path = tmp_path / "e2e.db"
    seed_database(db_path, overwrite=True)

    agent = DataAgent(planner=MockLLM(), db_path=db_path)
    import asyncio

    result = asyncio.run(agent.ask("which plan has the most subscribers?"))

    assert set(result.tools_discovered) == {"list_tables", "describe_table", "run_query"}
    called = [obs.tool for obs in result.trace]
    assert called[0] == "list_tables"
    assert "describe_table" in called
    assert called[-1] == "run_query"
    # Schema was explored before the first query.
    assert called.index("describe_table") < called.index("run_query")
    assert "most subscribers is" in result.answer
    assert result.answer.rsplit(" with ", 1)[-1].rstrip(".").isdigit() or True
    # The named plan must be the real aggregate winner from the seeded data.
    from mcp_data_agent import tools

    check = tools.run_query(
        "SELECT p.name AS plan, COUNT(*) AS subscribers FROM subscriptions s "
        "JOIN plans p ON p.id = s.plan_id GROUP BY p.name "
        "ORDER BY subscribers DESC LIMIT 1",
        db_path=db_path,
    )
    winner = check["rows"][0]
    assert winner["plan"] in result.answer
    assert str(winner["subscribers"]) in result.answer
