"""Every conversion ships with its inverse: going there and back gives what you started with."""

import pytest

TABLES = [
    {"columns": [{"name": "a", "type": "INT", "values": [1, 2, None]}, {"name": "b", "type": "STRING", "values": ["x", "ünï 🐕", None]}]},
    {"columns": [{"name": "x", "type": "FLOAT", "values": [0.5, -2.25]}, {"name": "ok", "type": "BOOL", "values": [True, False]}]},
    {"columns": [{"name": "day", "type": "DATE", "values": ["2026-11-03"]}, {"name": "at", "type": "TIMESTAMP", "values": ["2026-11-03T05:21:25+00:00"]}]},
]


@pytest.mark.parametrize("table", TABLES)
def test_table_to_rows_and_back(engine, table):
    assert engine.rows_table(engine.table_rows(table, True), True)["columns"][0]["values"] == table["columns"][0]["values"]


@pytest.mark.parametrize("table", TABLES)
def test_a_table_through_duckdb_is_itself(engine, table):
    from tests.test_nodes import FakeCx

    assert engine.query("SELECT * FROM a", table, cx=FakeCx()).table == table
