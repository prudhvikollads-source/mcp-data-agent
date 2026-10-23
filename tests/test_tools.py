import pytest

from mcp_data_agent import tools
from mcp_data_agent.db import seed_database
from mcp_data_agent.safety import UnsafeQueryError


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    seed_database(path, overwrite=True)
    return path


def test_list_tables(db_path):
    result = tools.list_tables(db_path)
    names = {t["name"] for t in result["tables"]}
    assert names == {"customers", "plans", "subscriptions", "invoices"}
    counts = {t["name"]: t["row_count"] for t in result["tables"]}
    assert counts["customers"] == 1200
    assert counts["plans"] == 6
    assert counts["subscriptions"] == 1800
    assert counts["invoices"] == 4200


def test_describe_table(db_path):
    result = tools.describe_table("customers", db_path)
    cols = {c["name"] for c in result["columns"]}
    assert {"id", "name", "email", "city", "country", "segment", "created_at"} <= cols
    with pytest.raises(ValueError):
        tools.describe_table("no_such_table", db_path)


def test_run_query_select(db_path):
    result = tools.run_query("SELECT COUNT(*) AS n FROM customers", db_path=db_path)
    assert result["rows"] == [{"n": 1200}]
    assert result["columns"] == ["n"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers WHERE id = 1",
        "UPDATE customers SET city = 'Nowhere' WHERE id = 1",
        "INSERT INTO plans (name, monthly_price_usd, data_gb) VALUES ('X', 1, 1)",
        "DROP TABLE customers",
        "SELECT * FROM customers; DELETE FROM customers",
    ],
)
def test_run_query_rejects_writes(db_path, sql):
    with pytest.raises(UnsafeQueryError):
        tools.run_query(sql, db_path=db_path)
    # And the data is untouched afterwards.
    result = tools.run_query("SELECT COUNT(*) AS n FROM customers", db_path=db_path)
    assert result["rows"] == [{"n": 1200}]


def test_row_limit_enforced(db_path):
    result = tools.run_query("SELECT id FROM customers", row_limit=5, db_path=db_path)
    assert result["row_count"] == 5
    assert result["truncated"] is True

    # A query asking for more than the hard cap is capped anyway.
    capped = tools.run_query("SELECT id FROM customers LIMIT 1000", db_path=db_path)
    assert capped["row_count"] == 100
    assert capped["limit"] == 100
