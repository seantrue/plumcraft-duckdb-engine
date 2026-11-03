"""Each node called as a plain method: no host, no PlumCraft, quick to run and to debug."""

import json
import threading
import time

import pytest
from plumcraft_engine import ConfigError, NodeError

import plumcraft_duckdb as duck


class FakeCx:
    """A Cx for calling nodes directly: never cancelled, unless told to."""

    def __init__(self, cancel_after: float | None = None):
        self.start = time.monotonic()
        self.cancel_after = cancel_after
        self.progressed: list[float] = []

    def cancelled(self) -> bool:
        return self.cancel_after is not None and time.monotonic() - self.start > self.cancel_after

    def progress(self, f, stage=None):
        self.progressed.append(f)


def col(table, name):
    return next(c for c in table["columns"] if c["name"] == name)


@pytest.fixture
def files(tmp_path):
    (tmp_path / "pets.csv").write_text("name,legs,good\ncorgi,4,true\nheron,2,false\nsnake,0,\n")
    (tmp_path / "speed.json").write_text(json.dumps({"t": [1793404800, 1793408400], "down": [2300.5, None], "err": [None, "timeout"]}))
    (tmp_path / "nested.json").write_text(json.dumps({"history": [{"t": 1, "up": {"web": 3.5, "mail": None}}, {"t": 2, "up": {"web": 4.0, "mail": 1.0}}]}))
    (tmp_path / "rows.jsonl").write_text('{"a": 1}\n{"a": 2}\n')
    return tmp_path


def test_read_csv_types_its_columns(engine, files):
    r = engine.read(str(files / "pets.csv"), "auto", "SELECT * FROM file", cx=FakeCx())
    assert [(c["name"], c["type"]) for c in r.table["columns"]] == [("name", "STRING"), ("legs", "INT"), ("good", "BOOL")]
    assert col(r.table, "good")["values"] == [True, False, None]
    assert r.rows[0] == ["name", "legs", "good"] and r.rows[1] == ["corgi", 4, True]
    assert r.value == "corgi"


def test_read_columnar_json_unnests(engine, files):
    sql = "SELECT to_timestamp(unnest(t)) AS taken, unnest(down) AS down, unnest(err) AS err FROM file"
    r = engine.read(str(files / "speed.json"), "auto", sql, cx=FakeCx())
    assert col(r.table, "taken") == {"name": "taken", "type": "TIMESTAMP", "values": ["2026-10-31T00:00:00+00:00", "2026-10-31T01:00:00+00:00"]}
    assert col(r.table, "down")["values"] == [2300.5, None]


def test_read_nested_json_unpivots(engine, files):
    sql = (
        "WITH h AS (SELECT unnest(history, recursive := true) FROM file) "
        "FROM h UNPIVOT INCLUDE NULLS (ms FOR service IN (COLUMNS(* EXCLUDE t))) ORDER BY t, service"
    )
    r = engine.read(str(files / "nested.json"), "auto", sql, cx=FakeCx())
    # the README's recipe: a missed check stays, as null
    assert r.rows == [["t", "service", "ms"], [1, "mail", None], [1, "web", 3.5], [2, "mail", 1.0], [2, "web", 4.0]]


def test_read_jsonl_and_a_chosen_format(engine, files):
    assert engine.read(str(files / "rows.jsonl"), "auto", "SELECT sum(a) AS s FROM file", cx=FakeCx()).value == 3
    copy = files / "pets.data"
    copy.write_text((files / "pets.csv").read_text())
    with pytest.raises(ConfigError, match="choose one"):
        engine.read(str(copy), "auto", "SELECT * FROM file", cx=FakeCx())
    assert engine.read(str(copy), "csv", "SELECT count(*) AS n FROM file", cx=FakeCx()).value == 3


def test_read_refuses_what_the_user_can_fix(engine, files):
    with pytest.raises(ConfigError, match="choose a file"):
        engine.read("", "auto", "SELECT * FROM file", cx=FakeCx())
    with pytest.raises(ConfigError, match="no such file"):
        engine.read(str(files / "nope.csv"), "auto", "SELECT * FROM file", cx=FakeCx())
    with pytest.raises(ConfigError, match="Parser Error"):
        engine.read(str(files / "pets.csv"), "auto", "SELEKT 1", cx=FakeCx())


def test_query_needs_no_input(engine):
    r = engine.query("SELECT 42 AS answer", cx=FakeCx())
    assert r.value == 42 and r.rows == [["answer"], [42]]


def test_query_joins_tables_from_anywhere(engine):
    pets = {"columns": [{"name": "name", "type": "STRING", "values": ["corgi", "heron"]}, {"name": "legs", "type": "INT", "values": [4, 2]}]}
    sounds = {"columns": [{"name": "name", "type": "STRING", "values": ["corgi", "heron"]}, {"name": "says", "type": "STRING", "values": ["woof", "kraak"]}]}
    r = engine.query("SELECT a.name, legs, says FROM a JOIN b USING (name) ORDER BY legs", pets, sounds, cx=FakeCx())
    assert r.rows == [["name", "legs", "says"], ["heron", 2, "kraak"], ["corgi", 4, "woof"]]


def test_a_tables_nulls_types_and_json_survive(engine):
    t = {
        "columns": [
            {"name": "n", "type": "INT", "values": [1, None]},
            {"name": "x", "type": "FLOAT", "values": [1.5, None]},
            {"name": "ok", "type": "BOOL", "values": [True, None]},
            {"name": "day", "type": "DATE", "values": ["2026-11-03", None]},
            {"name": "tags", "type": "JSON", "values": [["a", "b"], {"k": 1}]},
            {"name": "liar", "type": "INT", "values": ["not a number", 2]},
        ]
    }
    r = engine.query("SELECT * FROM a", t, cx=FakeCx())
    got = {c["name"]: (c["type"], c["values"]) for c in r.table["columns"]}
    assert got["n"] == ("INT", [1, None])
    assert got["x"] == ("FLOAT", [1.5, None])
    assert got["ok"] == ("BOOL", [True, None])
    assert got["day"] == ("DATE", ["2026-11-03", None])
    assert got["tags"][0] == "JSON"
    assert got["liar"][0] == "JSON"
    assert engine.query("SELECT dayname(day) AS d FROM a LIMIT 1", t, cx=FakeCx()).value == "Tuesday"


def test_the_value_of_a_json_object_is_an_object(engine):
    r = engine.query("SELECT json_object('fields', ['a', 'b'], 'rows', [[1, 2]]) AS params", cx=FakeCx())
    assert r.value == {"fields": ["a", "b"], "rows": [[1, 2]]}
    assert engine.query("SELECT struct_pack(a := 1, b := 'x') AS s", cx=FakeCx()).value == {"a": 1, "b": "x"}


def test_the_readmes_mail_merge_recipe(engine):
    t = {"columns": [{"name": "service", "type": "STRING", "values": ["web", "mail"]}, {"name": "uptime", "type": "FLOAT", "values": [99.9, 100.0]}]}
    sql = "SELECT json_object('fields', ['service', 'uptime'], 'rows', list(json_array(service, uptime))) FROM a"
    assert engine.query(sql, t, cx=FakeCx()).value == {"fields": ["service", "uptime"], "rows": [["web", 99.9], ["mail", 100.0]]}


def test_odd_values_become_json(engine):
    r = engine.query("SELECT 'nan'::DOUBLE AS f, 1.25::DECIMAL(5,2) AS d, '\\x01'::BLOB AS b, INTERVAL 90 SECOND AS i, uuid() IS NOT NULL AS u", cx=FakeCx())
    assert r.rows[1] == [None, 1.25, "AQ==", 90.0, True]


def test_query_refuses_what_the_user_can_fix(engine):
    with pytest.raises(ConfigError, match="empty"):
        engine.query("   ", cx=FakeCx())
    with pytest.raises(ConfigError, match="does not exist"):
        engine.query("SELECT * FROM a", cx=FakeCx())
    with pytest.raises(ConfigError, match="called 'x'"):
        engine.query("SELECT 1 AS x, 2 AS x", cx=FakeCx())
    with pytest.raises(ConfigError, match="no columns"):
        engine.query("SELECT 1", {"columns": []}, cx=FakeCx())
    with pytest.raises(ConfigError, match="has 1 values"):
        engine.query("SELECT 1", {"columns": [{"name": "a", "type": "INT", "values": [1, 2]}, {"name": "b", "type": "INT", "values": [1]}]}, cx=FakeCx())


def test_no_extension_is_installed_behind_the_users_back(engine):
    with pytest.raises(ConfigError):
        engine.query("SELECT * FROM read_xlsx('nothing.xlsx')", cx=FakeCx())


def test_too_many_rows_is_refused(engine, monkeypatch):
    monkeypatch.setattr(duck, "MAX_ROWS", 10)
    with pytest.raises(ConfigError, match="more than 10 rows"):
        engine.query("SELECT * FROM range(11)", cx=FakeCx())
    assert len(engine.query("SELECT * FROM range(10)", cx=FakeCx()).rows) == 11


def test_a_long_query_stops_on_cancel(engine):
    start = time.monotonic()
    with pytest.raises(NodeError) as e:
        engine.query("SELECT count(*) FROM range(100000000000)", cx=FakeCx(cancel_after=0.2))
    assert e.value.kind == "cancelled"
    assert time.monotonic() - start < 2.0


def test_queries_run_side_by_side(engine):
    out = []

    def one(i):
        out.append(engine.query(f"SELECT {i} AS i", cx=FakeCx()).value)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out) == list(range(8))


def test_table_rows_and_back(engine):
    t = {"columns": [{"name": "a", "type": "INT", "values": [1, 2]}, {"name": "b", "type": "STRING", "values": ["x", None]}]}
    rows = engine.table_rows(t, True)
    assert rows == [["a", "b"], [1, "x"], [2, None]]
    assert engine.rows_table(rows, True) == t
    assert engine.table_rows(t, False) == [[1, "x"], [2, None]]
    assert engine.rows_table([[1, 2.5], [3, 4]], False)["columns"][1] == {"name": "column2", "type": "FLOAT", "values": [2.5, 4]}
    with pytest.raises(ConfigError):
        engine.rows_table({"a": 1}, True)
    with pytest.raises(ConfigError):
        engine.rows_table([["a", "a"], [1, 2]], True)


def test_table_csv(engine):
    t = {"columns": [{"name": "name", "type": "STRING", "values": ["a, b", 'say "hi"']}, {"name": "tags", "type": "JSON", "values": [[1], None]}]}
    assert engine.table_csv(t) == 'name,tags\n"a, b",[1]\n"say ""hi""",\n'


def test_sql_to_svg_fills_a_template(engine, tmp_path):
    (tmp_path / "card.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40"><text>{{n}} corgis</text>{{bars}}</svg>')
    t = {"columns": [{"name": "h", "type": "INT", "values": [3, 7]}]}
    sql = (
        "SELECT replace(replace(svg, '{{n}}', (SELECT count(*)::VARCHAR FROM a)), '{{bars}}', "
        "(SELECT string_agg(format('<rect x=\"{}\" height=\"{}\"/>', 10 * h, h), '') FROM a)) FROM template"
    )
    b = engine.query_svg(sql, str(tmp_path / "card.svg"), t, cx=FakeCx())
    assert b.meta["media"] == "image/svg+xml" and b.meta["form"] == {"kind": "vector", "width": 200.0, "height": 40.0}
    assert b"2 corgis" in b.data and b.data.count(b"<rect") == 2


def test_sql_to_svg_without_a_template_and_its_refusals(engine, tmp_path):
    assert b"a corgi naps" in engine.query_svg("SELECT replace(svg, '{{text}}', 'a corgi naps') FROM template", "", cx=FakeCx()).data
    with pytest.raises(NodeError, match="not an SVG") as e:
        engine.query_svg("SELECT '<html/>'", "", cx=FakeCx())
    assert e.value.kind == "config"
    with pytest.raises(ConfigError, match="as text"):
        engine.query_svg("SELECT 42", "", cx=FakeCx())
    with pytest.raises(ConfigError, match="no such file"):
        engine.query_svg("SELECT svg FROM template", str(tmp_path / "nope.svg"), cx=FakeCx())
