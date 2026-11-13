# AI Contributions — DuckDB for PlumCraft

*About AI slop.* Fair enough: this engine was written by a machine, start to finish. Call it instant ramen. It's fast, it's cheap, it's genuinely edible, and nobody should pretend it simmered on the stove all afternoon. The honest answer isn't a claim about quality. It's the record below: what the maintainer asked for, what the model built, and what the model got wrong, including the defects that the test suite, PlumCraft's conformance suite and a live run against real data caught. Bon appétit, but mind the sodium content.

Tracks what AI helpers contributed to DuckDB for PlumCraft versus the human maintainer.

## Human maintainer

The maintainer asked for the engine and chose the language. They wrote PlumCraft, its Python engine SDK and the `plumcraft-engine new` generator that made this project's skeleton (the scripts, the PyCharm configurations, the conformance and golden tests). There was no code here before the AI work.

## Contributions

### 2026-11-13 — The SDK pin moved to PlumCraft 0.7.0: commonkit's hash switch (Claude Opus 5.5)

**What the maintainer asked for:** the commonkit hash facade, layer 3 (plum `docs/plans/2026-11-12-commonkit-hash-facade.md` §3.8, the maintainer's D23 1a): move the PlumCraft dependents onto the release that hashes through commonkit's facade, so every handle is in one algorithm, chosen by commonkit's `HASH` (the store's `.policy.json`, then `COMMONKIT_HASH`, then `b3`).

**What the AI did:**
- `pyproject.toml` (2+2 lines): the SDK's `rev` moves from `81dd28f` to `34f6aea`, the commit tagged `plumcraft-v0.7.0`. The SDK's bound `plumcraft-engine>=0.1,<0.2` becomes `>=0.2,<0.3`. `uv.lock` (+99 −6): `plumcraft-engine` 0.2.0, `pyuncommonkit` 0.2.0 and `blake3` 1.0.11.
- `tests/test_hash.py` (85 lines, the same in the three engines): under md5 and then b3, each in a child process with a fresh `COMMONKIT_STORE_ROOT`, `COMMONKIT_HASH` and `COMMONKIT_MARK_DIR`, the engine's hello reports exactly `[algo]`, commonkit's stderr line names the algorithm and its source, every handle the demo returns and an array published through the SDK's data plane are `<algo>:` with the algorithm's length, and no `shm_*` marker is left.

The engine has no hashing of its own: every handle comes from the SDK.

**Tests failed before the code:** at `81dd28f` the test fails in the child with `KeyError: 'algos'` (the old hello has no `data.algos`); checked on opencv-engine.

**Checked** (through plum's `hf2_env.py`, a fresh store per run): `uv run pytest` under md5 and under b3 (31 passed each), and `scripts/conform` under both with PlumCraft 0.7.0's `plumcraft` binary on `PLUMCRAFT_BIN` (the Python half and 18 of 18 Rust-side cases); `ruff check` and `ruff format --check`. No marker but `.started` in any run.

**Defects in AI-written code:** the first draft of the test published its array with no inline budget and no shared store, which the SDK refuses; caught by running it.

### 2026-11-04 — The SDK pin moved to PlumCraft's Windows support (Claude Opus 5.5)

The maintainer installed this engine from PlumCraft's editor on Windows. It registered and appeared, then died on start: `module 'socket' has no attribute 'AF_UNIX'`. The SDK was pinned to PlumCraft `3995cb7`, from before PlumCraft ran on Windows, and Windows Python has no Unix sockets. The maintainer: "bump the engine pins".

The AI moved the pin to PlumCraft `81dd28f` (`main`, merging PR #19, "PlumCraft on Windows"). There the SDK also reads a `tcp:127.0.0.1:<port>` locator, which the host offers on Windows. `uv.lock` was regenerated. `scripts/check` passes: ruff, format, and 29 tests on macOS.

The same SDK had already been tried on Windows: on `win11-dev`, with the installed OpenCV engine's SDK swapped for the checkout. A fresh install there with this pin hasn't been tried.

### 2026-11-03 — Published, with `register` (Claude Opus 5.5)

The maintainer: "Let's get the python engines (duckdb, opencv, ?) into one or more public repos". They chose one repo per engine, named `plumcraft-<name>-engine`, and the SDK pinned to PlumCraft's pushed `headless-write-files` commit until a release.

The AI:

- pinned the SDK to that commit over https, in place of a path on the maintainer's machine;
- added the MIT and Apache 2.0 licences, PlumCraft's;
- put `examples/` in the wheel, so `register` finds the demo;
- wrote the Install section: `uv tool install git+…`, then `<engine> register`.

The tests and ruff passed against the SDK fetched from GitHub.

DuckDB's Table to Rows, Rows to Table and Table to CSV are now marked converters (`@node(converts=True)`), so PlumCraft offers them on a wire whose types don't meet. The golden catalogue was refreshed for the marks.

### 2026-11-03 — SQL to SVG, for the server's-month graph (Claude Opus 5.5)

**What the maintainer asked for:** the sysadmin report graph that this engine was built for ("Look at the sysadmin reporting code and build a graph using grid,word,and vector craft (others as useful). Make it useful as a demo and pretty."), then "Go back 30 days for data".

**What the AI did:**

- **SQL to SVG** (`duckdb.query_svg`, ~45 LOC). It is SQL over up to three tables and an SVG template (the view `template`), and its first value becomes an SVG. VectorCraft can't add text from wired values, and no node drew a picture from a table, so this is how the graph's status card is made. Two tests cover it.
- **README:** a row for the new node, a bar-chart recipe, and the DuckDB 2 lambda syntax.
- **Multiline SQL:** the three SQL fields carry PlumCraft's new `multiline` widget hint (`Port(widget="multiline")`), so the editor shows a query as a text box.

**Defects in AI-written work, and how each was caught:**

- **The README's unpivot recipe dropped every missed check.** Plain `UNPIVOT` leaves out nulls, and in a status history a null means the service was down, so uptime would always have read 100%. Caught while building the graph, when its uptime query had to keep the nulls. The recipe and its test now use `UNPIVOT INCLUDE NULLS`.
- **Two tests were wrong:** one put a window function inside an aggregate, and the other expected the wrong error class for a bad SVG. Both were caught by running them.

### 2026-11-03 — The engine: Read File, SQL Query and table conversions (Claude Opus 5.5)

**What the maintainer asked for:** "Check to see if duckdb can act as an import engine. If so, build a duckdb engine for plumcraft." Then, once a Rust crate had been started: "Build in python instead."

The request came out of a graph the AI was building from the maintainer's sysadmin reports. Those graphs needed to reshape nested JSON, and to build JSON objects for the crafts' `params` inputs, and no PlumCraft node did either.

**What the AI did:**

- **The engine** (`src/plumcraft_duckdb/__init__.py`, ~405 LOC). Read File and SQL Query each give a table, rows and a value. Alongside them are Table to Rows, Rows to Table, Table to CSV and a Slow node for conformance. The rest of the file covers:
  - Mapping DuckDB's types to PlumCraft's TABLE column types and back, times included.
  - Cancel through `interrupt()` from a watcher thread, which also reports DuckDB's query progress.
  - Limits on rows and cells.
  - Splitting errors into config errors (the SQL, the file, a missing extension) and op errors.
  - A UTC session, with extension auto-install off.
- **Tests** (~215 LOC, 27 tests): each node, refusals, nulls and types through a round trip, the README's recipes, cancel, parallel queries, and the generator's golden catalogue and conformance tests.
- **The demo** (`examples/demo.json` and two small data files written for it): a made-up week of hourly speed tests, with a verdict on the slowest hour.
- **The README:** the nodes, the three outputs, recipes and caveats.
- **Research:** the DuckDB Rust crate's features and API, before the switch to Python, and the DuckDB 2.0 Python API on the maintainer's real homepage JSON.

**Defects in AI-written work, and how each was caught:**

- **Wrong first target: a Rust crate.** The maintainer redirected it to Python before any code beyond a scaffold was written, and the scaffold was removed.
- **Timestamps failed outright.** DuckDB's Python package needs `pytz` for `TIMESTAMP WITH TIME ZONE` results. Caught by the first demo run; `pytz` is now a dependency.
- **Times came back as text.** A timestamp column wired into a second query arrived as VARCHAR, so `hour(...)` failed. Caught by the demo; times now keep their own column types and are cast back when read in.
- **A null FLOAT became NaN,** a number, when a table was read in. Found while reviewing the registration code; nulls now stay null.
- **The demo's day boundaries depended on the machine's time zone** (8 days instead of 7). Caught by the demo's output; the session is now UTC.
- **A column named `at`** in the demo was an SQL keyword. Caught by DuckDB's parser on the first run.
- **A dead check:** "the last statement returns no rows" can't happen, because DuckDB gives every statement a result. Caught by its test, which never raised.
- **A README recipe that didn't run:** `list([service, uptime])` mixes types. Caught by running it; it now uses `json_array`, and is a test.

**Found in PlumCraft on the way, and fixed there** (see PlumCraft's `AI.md`):

- `plumcraft-engine check` didn't anchor a demo's relative paths, and neither did the generator's test fixture.
- Headless runs ignored a workflow's `engines` list once `--engine-manifest` was given.

**Verification:** on 2026-11-03, 27 pytest tests passed, PlumCraft's conformance passed in process and over HTTP, and `plumcraft conform` held 14 of 14 and 18 of 18 cases. A live run read the maintainer's live speed-test JSON, and GridCraft charted and exported the daily averages.

**Division of labor:** the maintainer asked for the engine and chose Python; the AI researched, built, tested and documented it.
