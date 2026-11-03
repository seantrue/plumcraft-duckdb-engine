"""PlumCraft's conformance checks: what every engine must do, in process and over HTTP."""

from plumcraft_engine.conformance import assert_conformant

from plumcraft_duckdb import DuckdbEngine


def test_the_engine_is_conformant(demo):
    assert_conformant(DuckdbEngine, demo=demo, slow="duckdb.slow")
