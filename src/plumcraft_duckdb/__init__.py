"""DuckDB: a PlumCraft engine that reads data files into tables and reshapes them with SQL.

- **Read File** reads a CSV, JSON, Parquet or Excel file as the view `file`, and runs a query on
  it (`SELECT * FROM file` by default). Nested JSON keeps its structure, so `unnest` and `unpivot`
  can flatten it in the same step.
- **SQL Query** runs SQL over up to three tables wired from any engine, as the views `a`, `b`
  and `c`.
- Both give the result three ways: a **table** (columnar, PlumCraft's TABLE), **rows** (a header
  row, then one list per row: what GridCraft's Write range and other `values` inputs take), and
  a **value** (the first column of the first row as JSON: a `json_object` or `struct_pack` there
  is an object, which a craft's `params` input takes).

Every query runs on its own in-memory database, closed afterwards. DuckDB never installs an
extension by itself here: one a query needs that isn't installed is a config error saying how to
install it.
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import decimal
import io
import json
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

import duckdb
import numpy as np
from plumcraft_engine import ConfigError, Cx, Engine, File, Json, NodeError, OpError, Port, Range, Svg, Table, Text, node, svg

__version__ = "0.1.0"

#: The most rows a result keeps; a query that makes more is a config error (add a LIMIT).
MAX_ROWS = 1_000_000
#: The most cells (rows × columns) a result keeps.
MAX_CELLS = 20_000_000
#: The largest JSON object DuckDB reads in one piece (a whole file of columnar JSON is one).
MAX_JSON_OBJECT = 1 << 30
#: The largest SVG template SQL to SVG reads.
MAX_TEMPLATE = 16 << 20
#: SQL to SVG's template when it is given none.
_EMPTY_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80" viewBox="0 0 400 80"><text x="20" y="48" font-size="24">{{text}}</text></svg>'
#: How often a running query is checked for cancel and progress, in seconds.
WATCH = 0.05

EXTENSIONS = {
    "csv": "csv",
    "tsv": "csv",
    "txt": "csv",
    "json": "json",
    "jsonl": "json",
    "ndjson": "json",
    "parquet": "parquet",
    "pq": "parquet",
    "xlsx": "xlsx",
}
FILTERS = (("Data", ("csv", "tsv", "json", "jsonl", "ndjson", "parquet", "xlsx")),)

# PlumCraft TABLE column types for DuckDB's.
_INTS = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"}
_FLOATS = {"FLOAT", "DOUBLE", "REAL"}
_STRINGS = {"VARCHAR", "UUID", "INTERVAL", "BIT"}
# Times keep their own column types, their values ISO text, so they come back as times.
_TIMES = {"DATE": "DATE", "TIME": "TIME", "TIMESTAMP": "TIMESTAMPTZ"}


class Result(NamedTuple):
    table: Table
    rows: Json
    value: Json


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def column_type(duck_type: str) -> str:
    """The TABLE column type for a DuckDB type: INT, FLOAT, BOOL, STRING, DATE, TIME, TIMESTAMP
    (those three as ISO text), or JSON for anything nested."""
    t = duck_type.upper()
    if t in _INTS:
        return "INT"
    if t in _FLOATS or t.startswith("DECIMAL"):
        return "FLOAT"
    if t == "BOOLEAN":
        return "BOOL"
    if t == "DATE":
        return "DATE"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if t.startswith("TIME"):
        return "TIME"
    if t in _STRINGS or t.startswith(("ENUM", "VARCHAR")):
        return "STRING"
    return "JSON"


def plain(v: Any) -> Any:
    """A DuckDB result value as JSON: dates and times as ISO text, decimals as floats, NaN and
    infinities as null, bytes as base64, and structs, maps and lists all the way down."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(v)).decode("ascii")
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, np.generic):
        return plain(v.item())
    return str(v)


def _int64(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and -(2**63) <= x < 2**63


def _register(con: duckdb.DuckDBPyConnection, name: str, table: Any) -> None:
    """A PlumCraft table as the view `name`. DATE, TIME and TIMESTAMP columns of ISO text come back
    as times; a JSON column, and one whose values don't fit its type, arrive as DuckDB's JSON."""
    columns = table.get("columns") if isinstance(table, dict) else None
    if not isinstance(columns, list) or not columns:
        raise ConfigError(f"{name}: a table with no columns")
    data: dict[str, np.ndarray] = {}
    cast: dict[str, str] = {}
    length = None
    for c in columns:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not isinstance(c.get("values"), list):
            raise ConfigError(f"{name}: each column needs a name and values")
        col, values = c["name"], c["values"]
        if length is None:
            length = len(values)
        elif len(values) != length:
            raise ConfigError(f"{name}: column {col!r} has {len(values)} values, not {length}")
        if col in data:
            raise ConfigError(f"{name}: two columns are called {col!r}")
        kind = str(c.get("type", "")).upper()
        present = [x for x in values if x is not None]
        whole = len(present) == len(values)
        if kind == "INT" and all(_int64(x) for x in present):
            data[col] = np.array(values, dtype=np.int64) if whole else np.array(values, dtype=object)
        elif kind == "FLOAT" and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in present):
            # a null stays null (NaN would be a number)
            floats = [None if x is None else float(x) for x in values]
            data[col] = np.array(floats, dtype=np.float64) if whole else np.array(floats, dtype=object)
        elif kind == "BOOL" and all(isinstance(x, bool) for x in present):
            data[col] = np.array(values, dtype=bool) if whole else np.array(values, dtype=object)
        elif kind in ("STRING", "TEXT", "ENUM", *_TIMES) and all(isinstance(x, str) for x in present):
            data[col] = np.array(values, dtype=object)
            if kind in _TIMES:
                cast[col] = _TIMES[kind]
        else:
            data[col] = np.array([None if x is None else json.dumps(x, ensure_ascii=False) for x in values], dtype=object)
            cast[col] = "JSON"
    raw = f"__{name}_columns"
    con.register(raw, data)
    select = ", ".join(f"{_quote_ident(n)}::{cast[n]} AS {_quote_ident(n)}" if n in cast else _quote_ident(n) for n in data)
    con.execute(f"CREATE TEMP VIEW {_quote_ident(name)} AS SELECT {select} FROM {_quote_ident(raw)}")


def _reader(path: str, fmt: str) -> str:
    """The table function that reads `path` as `fmt` (`auto`: from the extension)."""
    if fmt == "auto":
        ext = Path(path).suffix.lower().lstrip(".")
        fmt = EXTENSIONS.get(ext, "")
        if not fmt:
            raise ConfigError(f"{Path(path).name}: can't tell its format from its name; choose one")
    p = _quote_literal(path)
    return {
        "csv": f"read_csv({p}, auto_detect = true)",
        "json": f"read_json_auto({p}, maximum_object_size = {MAX_JSON_OBJECT})",
        "parquet": f"read_parquet({p})",
        "xlsx": f"read_xlsx({p}, header = true)",
    }[fmt]


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    # nothing is fetched from the network behind the user's back
    con.execute("SET autoinstall_known_extensions = false")
    # the same answer on every machine (the memo depends on it): times are UTC unless a query says AT TIME ZONE
    con.execute("SET TimeZone = 'UTC'")
    return con


_USER_ERRORS = (
    duckdb.ParserException,
    duckdb.BinderException,
    duckdb.CatalogException,
    duckdb.ConversionException,
    duckdb.IOException,
    duckdb.InvalidInputException,
    duckdb.NotImplementedException,
    duckdb.TypeMismatchException,
    duckdb.ConstraintException,
    duckdb.OutOfRangeException,
)


def _fail(e: duckdb.Error) -> NodeError:
    """A DuckDB error as the node's: what the user can fix (the SQL, the file, an extension to
    install) is a config error; anything else an op error."""
    text = str(e).strip()
    if isinstance(e, duckdb.InterruptException):
        return NodeError.cancelled()
    low = text.lower()
    if "extension" in low and ("install" in low or "autoload" in low):
        how = "uv run python -c \"import duckdb; duckdb.sql('INSTALL excel')\" (or the extension named)"
        return ConfigError(f"{text}\nInstall it once, in this engine's project: {how}")
    if isinstance(e, _USER_ERRORS):
        return ConfigError(text)
    return OpError(text)


def run_query(con: duckdb.DuckDBPyConnection, sql: str, cx: Cx | None) -> Result:
    """Run `sql` on `con`, watching for cancel and reporting progress, and give its result."""
    if not sql.strip():
        raise ConfigError("the query is empty")
    done = threading.Event()

    def watch() -> None:
        while not done.wait(WATCH):
            if cx is not None and cx.cancelled():
                con.interrupt()
                return
            try:
                p = con.query_progress()
            except duckdb.Error:
                continue
            if cx is not None and p is not None and p >= 0:
                cx.progress(min(p / 100.0, 1.0), "querying")

    watcher = threading.Thread(target=watch, daemon=True) if cx is not None else None
    if watcher is not None:
        watcher.start()
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        types = [column_type(str(d[1])) for d in cur.description]
        limit = min(MAX_ROWS, MAX_CELLS // max(len(names), 1))
        rows = cur.fetchmany(limit + 1)
    except duckdb.Error as e:
        raise _fail(e) from None
    finally:
        done.set()
        if watcher is not None:
            watcher.join()
    if cx is not None and cx.cancelled():
        raise NodeError.cancelled()
    if len(rows) > limit:
        raise ConfigError(f"the result has more than {limit:,} rows: aggregate it, or add a LIMIT")
    dup = next((n for n in names if names.count(n) > 1), None)
    if dup is not None:
        raise ConfigError(f"two result columns are called {dup!r}: name them with AS")
    cells = [[plain(v) for v in r] for r in rows]
    table = {"columns": [{"name": n, "type": t, "values": [r[i] for r in cells]} for i, (n, t) in enumerate(zip(names, types, strict=True))]}
    value = cells[0][0] if cells and cells[0] else None
    if isinstance(value, str) and types[0] == "JSON":
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return Result(table, [names, *cells], value)


def _infer(values: list) -> str:
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, bool) for v in present):
        return "BOOL"
    if present and all(_int64(v) for v in present):
        return "INT"
    if present and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "FLOAT"
    if all(isinstance(v, str) for v in present):
        return "STRING"
    return "JSON"


def _csv_cell(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


class DuckdbEngine(Engine):
    id = "duckdb"
    name = "DuckDB"
    version = __version__
    category = "DuckDB"
    family = "duckdb"

    @node("Read File", output=("table", "rows", "value"))
    def read(
        self,
        path: Annotated[str, File("open", FILTERS), Port(tooltip="A CSV, JSON, Parquet or Excel file")] = "",
        format: Annotated[Literal["auto", "csv", "json", "parquet", "xlsx"], Port(tooltip="auto: from the file's extension")] = "auto",
        sql: Annotated[str, Port(label="SQL", widget="multiline", tooltip="Runs on the view file; unnest flattens nested JSON")] = "SELECT * FROM file",
        *,
        cx: Cx,
    ) -> Result:
        """Read a CSV, JSON, Parquet or Excel file and run SQL on it, as the view `file`. Nested JSON keeps its structure, so
        `unnest(list, recursive := true)` and `unpivot` can flatten it here. Gives the result as a table, as rows (a header row
        first), and as its first value."""
        if not path.strip():
            raise ConfigError("choose a file to read")
        p = Path(path).expanduser()
        if not p.is_file():
            raise ConfigError(f"{path}: no such file")
        con = _connect()
        try:
            try:
                con.execute(f"CREATE TEMP VIEW file AS SELECT * FROM {_reader(str(p), format)}")
            except duckdb.Error as e:
                raise _fail(e) from None
            return run_query(con, sql, cx)
        finally:
            con.close()

    @node("SQL Query", output=("table", "rows", "value"))
    def query(
        self,
        sql: Annotated[str, Port(label="SQL", widget="multiline", tooltip="Tables wired in are the views a, b and c")] = "SELECT 42 AS answer",
        a: Table | None = None,
        b: Table | None = None,
        c: Table | None = None,
        *,
        cx: Cx,
    ) -> Result:
        """Run SQL over the tables wired in, as the views `a`, `b` and `c`; a query needs none (`SELECT * FROM range(10)`).
        Gives the result as a table, as rows (a header row first), and as its first value: end with
        `SELECT json_object('rows', …)` or `struct_pack(…)` to make a JSON object."""
        con = _connect()
        try:
            for name, t in (("a", a), ("b", b), ("c", c)):
                if t is not None:
                    _register(con, name, t)
            return run_query(con, sql, cx)
        finally:
            con.close()

    @node("SQL to SVG")
    def query_svg(
        self,
        sql: Annotated[str, Port(label="SQL", widget="multiline", tooltip="Its first value is the SVG document. The template is the view template (svg)")] = (
            "SELECT replace(svg, '{{text}}', 'a corgi naps') FROM template"
        ),
        template: Annotated[str, File("open", (("SVG", ("svg",)),)), Port(tooltip="An SVG to fill in: the view template, one row, column svg")] = "",
        a: Table | None = None,
        b: Table | None = None,
        c: Table | None = None,
        *,
        cx: Cx,
    ) -> Svg:
        """A picture drawn from data: SQL whose first value is an SVG document, over the tables wired in (`a`, `b`, `c`) and an
        SVG template (the view `template`, column `svg`). Fill a template with `replace`, and draw one shape per row with
        `string_agg(printf('<rect x="%d" width="%.1f" …/>', …), '')`."""
        con = _connect()
        try:
            body = _EMPTY_SVG
            if template.strip():
                p = Path(template).expanduser()
                if not p.is_file():
                    raise ConfigError(f"{template}: no such file")
                if p.stat().st_size > MAX_TEMPLATE:
                    raise ConfigError(f"{template}: larger than {MAX_TEMPLATE >> 20} MiB")
                try:
                    body = p.read_text("utf-8")
                except (OSError, UnicodeDecodeError) as e:
                    raise ConfigError(f"{template}: {e}") from None
            con.execute("CREATE TEMP TABLE template AS SELECT ?::VARCHAR AS svg", [body])
            for name, t in (("a", a), ("b", b), ("c", c)):
                if t is not None:
                    _register(con, name, t)
            value = run_query(con, sql, cx).value
        finally:
            con.close()
        if not isinstance(value, str):
            raise ConfigError("the query's first value must be the SVG document, as text")
        return svg(value)

    @node("Table to Rows", output="rows", converts=True, lossless=True)
    def table_rows(self, table: Table, header: bool = True) -> Json:
        """A table as rows: one list per row, the column names first when `header` is on. What GridCraft's Write range takes.
        The inverse of Rows to Table."""
        columns = table.get("columns", []) if isinstance(table, dict) else []
        if not isinstance(columns, list) or not all(isinstance(c, dict) for c in columns):
            raise ConfigError("table: columns of name, type and values")
        values = [c.get("values") if isinstance(c.get("values"), list) else [] for c in columns]
        if len({len(v) for v in values}) > 1:
            raise ConfigError("table: its columns differ in length")
        body = [list(r) for r in zip(*values, strict=True)] if values else []
        return [[str(c.get("name", "")) for c in columns], *body] if header else body

    @node("Rows to Table", converts=True, lossless=True)
    def rows_table(self, rows: Json, header: bool = True) -> Table:
        """Rows (a list of lists, as GridCraft's Read range gives) as a table, the first row naming the columns when `header` is
        on. Each column's type comes from its values. The inverse of Table to Rows."""
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise ConfigError("rows: a list of lists")
        if header and not rows:
            raise ConfigError("rows: there is no header row")
        width = max((len(r) for r in rows), default=0)
        names = [str(x) for x in rows[0]] if header else [f"column{i + 1}" for i in range(width)]
        if len(set(names)) != len(names):
            raise ConfigError("rows: two columns have the same name")
        body = rows[1:] if header else rows
        cols = [[r[i] if i < len(r) else None for r in body] for i in range(len(names))]
        return {"columns": [{"name": n, "type": _infer(v), "values": v} for n, v in zip(names, cols, strict=True)]}

    @node("Table to CSV", converts=True)
    def table_csv(self, table: Table) -> Text:
        """A table as CSV text, a header line first. Nested values are written as JSON."""
        rows = self.table_rows(table, True)
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows([[_csv_cell(v) for v in r] for r in rows])
        return out.getvalue()

    @node("Slow", category=("DuckDB", "Testing"))
    def slow(self, table: Table, seconds: Annotated[float, Range(0, 600, invalid="clamp")] = 0.05, *, cx: Cx) -> Table:
        """Passes a table through after `seconds`, reporting progress and stopping on cancel: how a long node behaves."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if cx.cancelled():
                raise NodeError.cancelled()
            cx.progress((time.monotonic() - start) / max(seconds, 1e-9), "waiting")
            time.sleep(0.01)
        return table
