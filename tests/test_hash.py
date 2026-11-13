"""The hash switch (commonkit's `HASH`): under each algorithm, in a process of its own with a fresh
store, the engine's hello names exactly that algorithm, and every handle its demo returns, and an
array published through the SDK's data plane, is in it, with the algorithm's length. Each child
gets its own `COMMONKIT_STORE_ROOT`, `COMMONKIT_HASH` and `COMMONKIT_MARK_DIR`, since commonkit
resolves the algorithm once per process."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HEX = {"md5": 32, "b3": 64}

CHILD = r"""
import importlib, json, os, sys
from pathlib import Path
import numpy as np
from plumcraft_engine.data import Plane
from plumcraft_engine.cli import Local, anchored, hello
from plumcraft_engine.conformance import Run

spec, demo = sys.argv[1], Path(sys.argv[2])
mod, cls = spec.split(":")
conn = Local(getattr(importlib.import_module(mod), cls))
try:
    algos = hello(conn)["data"]["algos"]
    graph = anchored(json.loads(demo.read_text("utf-8")), conn.call("schema", {}), demo.parent)
    reply = Run(conn, graph, "hash").reply
finally:
    conn.close()

def handles(v):
    if isinstance(v, dict):
        if isinstance(v.get("handle"), str):
            yield v["handle"]
        for x in v.values():
            yield from handles(x)
    elif isinstance(v, list):
        for x in v:
            yield from handles(x)

array = Plane(os.environ["COMMONKIT_STORE_ROOT"], False, 1 << 20).array_ref(np.arange(12, dtype="<f4").reshape(3, 4))["handle"]
print(json.dumps({"algos": algos, "handles": sorted(set(handles(reply))), "array": array}))
"""


def child(algo: str, tmp: Path) -> dict:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["plumcraft"]
    run = tmp / algo
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMMONKIT_")}
    env |= {
        "COMMONKIT_HASH": algo,
        "COMMONKIT_STORE_ROOT": str(run / "store"),
        "COMMONKIT_MARK_DIR": str(run / "marks"),
        "COMMONKIT_TEST_RUN": str(run),
    }
    out = subprocess.run(
        [sys.executable, "-c", CHILD, cfg["engine"], str(ROOT / cfg["demo"])],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stderr
    assert f"commonkit: hash {algo} (environment COMMONKIT_HASH)" in out.stderr, out.stderr
    marks = sorted(p.name for p in (run / "marks").glob("*.shm_*"))
    assert not marks, marks
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("algo", ["md5", "b3"])
def test_one_algorithm_per_process(algo, tmp_path):
    got = child(algo, tmp_path)
    assert got["algos"] == [algo]
    assert got["array"].startswith(f"{algo}:nd1:"), got["array"]
    for h in [got["array"], *got["handles"]]:
        a, recipe, digest = h.split(":", 2)
        assert (a, len(digest)) == (algo, HEX[algo]), h
        assert recipe in ("nd1", "raw"), h
