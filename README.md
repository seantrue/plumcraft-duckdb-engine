# DuckDB for PlumCraft

A [PlumCraft](https://github.com/seantrue/plumcraft) engine on [DuckDB](https://duckdb.org): it reads data files into tables and reshapes them with SQL, so a graph can start from the JSON, CSV or Parquet your scripts already write, and hand the result to any other engine. It is the import engine the craft apps lack: GridCraft, WordCraft and the rest take rows and objects, and DuckDB makes them.

## Install

You need PlumCraft: the editor from its [installers](https://github.com/seantrue/plumcraft/releases), or `cargo install plumcraft plumcraft-editor`. Then:

```sh
uv tool install git+https://github.com/seantrue/plumcraft-duckdb-engine   # or pipx install git+…
duckdb-engine register                                        # every PlumCraft editor now offers it
```

`duckdb-engine register` writes `~/.config/plumcraft/engines/duckdb.toml`, which starts the engine with the Python it was installed with. A running editor offers it after Engine › Reload Engine List. `duckdb-engine unregister` withdraws it.

## The nodes

| Node | Takes | Gives |
|---|---|---|
| Read File (`duckdb.read`) | a CSV, TSV, JSON, JSON lines, Parquet or Excel file, and SQL on the view `file` (`SELECT * FROM file`) | table, rows, value |
| SQL Query (`duckdb.query`) | SQL, and up to three tables from any engine as the views `a`, `b` and `c` | table, rows, value |
| SQL to SVG (`duckdb.query_svg`) | SQL, up to three tables, and an SVG template as the view `template` (column `svg`) | an SVG: the query's first value |
| Table to Rows, Rows to Table | a table, or a list of lists | the other, each the inverse of the other |
| Table to CSV | a table | CSV text |

The three outputs are one result three ways:

- **table**: PlumCraft's columnar TABLE. Column types are `INT`, `FLOAT`, `BOOL`, `STRING`, `DATE`, `TIME` and `TIMESTAMP` (those three as ISO text), and `JSON` for structs, lists and maps. A table wired back into a query keeps its types, times included.
- **rows**: a header row, then one list per row. GridCraft's Write range takes it as its `values`.
- **value**: the first column of the first row, as JSON. End a query with `SELECT json_object(...)` or `struct_pack(...)` and it is an object, which a craft's `params` input takes (WordCraft's mail merge, say).

## Recipes

Columnar JSON (`{"t": [...], "down": [...]}`), one row per entry:

```sql
SELECT to_timestamp(unnest(t)) AS taken, unnest(down) AS down FROM file
```

Snapshots of many services (`{"history": [{"t": …, "up": {"web": 3.1, "mail": null}}, …]}`), one row per service per snapshot. `INCLUDE NULLS` keeps the checks a service missed; plain `UNPIVOT` drops them:

```sql
WITH h AS (SELECT unnest(history, recursive := true) FROM file)
FROM h UNPIVOT INCLUDE NULLS (ms FOR service IN (COLUMNS(* EXCLUDE t)))
```

A bar per row, into an SVG template (SQL to SVG):

```sql
SELECT replace(svg, '{{bars}}', (SELECT string_agg(format('<rect x="{}" y="0" width="8" height="{}"/>', 10 * hour, down / 20), '') FROM a)) FROM template
```

A mail merge's recipients from a table:

```sql
SELECT json_object('fields', ['service', 'uptime'], 'rows', list(json_array(service, uptime))) FROM a
```

**Things to know:**

- Times are UTC unless a query says otherwise (`taken AT TIME ZONE 'America/New_York'`), so a graph gives the same answer on every machine.
- `at` is an SQL keyword: name a column `taken`, not `at`.
- Lambdas are `lambda x: x.ok`; DuckDB 2 refuses the old `x -> x.ok`.
- Each query runs in its own in-memory database. A path inside the SQL isn't resolved against the workflow; read files with Read File.
- Nothing is downloaded behind your back: DuckDB's automatic extension install is off. Excel files need the `excel` extension once: `uv run python -c "import duckdb; duckdb.sql('INSTALL excel')"`.
- A result is at most 1,000,000 rows and 20,000,000 cells; aggregate, or add a `LIMIT`.
- Cancel interrupts a running query.

## The first five minutes

```sh
uv sync                                  # the project's environment, with the PlumCraft SDK
scripts/check                            # ruff, the tests, the golden catalogue and conformance
scripts/nodes                            # the catalogue
scripts/run --watch verdict.value        # the demo: is the internet slow after dinner?
```

| Script | What it runs | Needs |
|---|---|---|
| `scripts/check` | `ruff check`, `ruff format --check`, `pytest` | uv |
| `scripts/nodes` | `plumcraft-engine nodes`: the catalogue, in process | uv |
| `scripts/run [GRAPH] [--watch NODE.PORT]... [--cancel-after S] [--attach URL]` | `plumcraft-engine run`: the demo by default; exits 0 when every node is done or cached | uv |
| `scripts/serve [PORT]` | `python -m plumcraft_duckdb --listen 127.0.0.1:PORT`, 7311 by default | uv |
| `scripts/conform` | `plumcraft-engine check`: PlumCraft's conformance cases, in process and over HTTP; then `plumcraft conform`, the Rust half on a socket and over HTTP, when PlumCraft is built | uv |
| `scripts/cli-run [GRAPH] [--http]` | `plumcraft run --engine-manifest`: the same run through PlumCraft's Rust CLI | a built PlumCraft |
| `scripts/editor [GRAPH]` | `plumcraft-editor --engine-manifest`: the editor with this engine, on the demo by default | a built PlumCraft |
| `scripts/install`, `scripts/uninstall` | Link `plumcraft-engine.toml` into `~/.config/plumcraft/engines/`, so every editor offers this engine | |

"A built PlumCraft" means the Rust editor and CLI: `cargo install plumcraft plumcraft-editor`, or a checkout with `PLUMCRAFT_HOME` set to it. The other scripts need only uv.

## Using it in the editor

```sh
scripts/editor
```

The editor starts this engine from `plumcraft-engine.toml` and opens the demo. `scripts/_plumcraft.sh` looks for the editor in `$PLUMCRAFT_BIN`, on PATH, then in your PlumCraft checkout's `target/`; build it there with `cargo build --release -p plumcraft -p plumcraft-editor`.

**The loop:** edit a node, Engine › Reconnect (a fresh process, the same graph), run.

To have every editor offer this engine, `scripts/install`, then Engine › Reload Engine List in one already running. The Engine menu lists it with two hostings, a process and HTTP. Picking it while another engine is connected adds it beside that one.

**Installed rather than checked out:** `uv build` makes a wheel that carries the demo and its data. Install it with `uv tool install duckdb_engine-0.1.0-py3-none-any.whl`, then run `duckdb-engine register`. Until the SDK release with `register` is on PyPI, add `--with` and the path of a PlumCraft checkout's `python` folder.

To debug, or to reach an engine on another machine, serve it yourself and attach: `scripts/serve`, then Engine › Attach…, `http://127.0.0.1:7311`, with the token from `.engine-token`, which `scripts/serve` makes on first use and git ignores. An HTTP engine always needs a token, on loopback too: a web page you have open can reach loopback.

## PyCharm

Open this folder as a project. PyCharm 2024.3 or later sees `uv.lock` (after a first `uv sync`) and offers the uv environment; with an older one, run `uv sync`, then add `.venv/bin/python` as an existing interpreter.

Run configurations, in the toolbar's list:

| Configuration | What it does |
|---|---|
| Serve (HTTP 7311) | The engine, for Engine › Attach |
| Run demo | The demo in process |
| Run demo (attached) | The demo against Serve |
| Nodes | The catalogue |
| Tests | Everything in `tests/` |
| Conformance | The conformance checks alone |
| Editor | `scripts/editor` |

**To stop at a breakpoint in a node:** debug "Serve (HTTP 7311)", attach from the editor with Engine › Attach… (or run "Run demo (attached)"), and run. The node stops at the breakpoint. The engine keeps answering while it is paused, so the editor shows the node as running; after a long pause it may report the run as failed, and the real reply replaces that when you resume. "Run demo" under Debug needs no editor at all.

`.run/`, `.idea/duckdb-engine.iml` and `.idea/modules.xml` are committed. `.idea/misc.xml` names your machine's interpreter, so it isn't.

## Adding a node

Add a method to `DuckdbEngine` in `src/plumcraft_duckdb/__init__.py`:

```python
@node("Reverse")
def reverse(self, text: Text) -> Text:
    """Text backwards. The docstring is the node's description."""
    return text[::-1]
```

`Text` and the other types come from `plumcraft_engine`; add the import if this file doesn't have it yet.

- **Inputs:** annotations make the ports. A plain `str`, `int`, `float`, `bool` or `Literal` is a widget that can also be wired; a wire type such as `Text` is a socket.
- **Output:** the return annotation. A `NamedTuple` gives several outputs.
- **Errors:** raise `ConfigError` for something the user can fix, `OpError` for a failure on this input.
- **A long node:** take `*, cx: Cx`, call `cx.progress(fraction, stage)`, and stop when `cx.cancelled()`.
- **A conversion ships with its inverse,** and a test in `tests/test_roundtrips.py` that goes there and back.

Then add a test in `tests/test_nodes.py`, and run `UPDATE_GOLDEN=1 uv run pytest` once, because the catalogue changed. The golden file is what catches a renamed port, which would break saved workflows. If you add a node to `examples/demo.json`, the conformance check runs it too.

The PlumCraft SDK's `python/README.md` lists every annotation.

## The SDK

`pyproject.toml` pins the PlumCraft SDK to a commit of PlumCraft's `headless-write-files` branch, which has what this engine needs (`register`, among others), until `plumcraft-engine` has a release with it. To work against a PlumCraft checkout instead, point the source at it:

```toml
plumcraft-engine = { path = "../plumcraft/python", editable = true }
```

## Licence

MIT or Apache 2.0, at your option: `LICENSE-MIT`, `LICENSE-APACHE`. As PlumCraft.
