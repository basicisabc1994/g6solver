# SPDX-License-Identifier: Apache-2.0
"""Without the abstraction extra (numpy, hypothesis), ``import g6.abstraction`` still works, so discovery and
``g6_invoke`` answer; a public name that needs the extra raises an ``ImportError`` that says what to install, never a
bare ``ModuleNotFoundError: No module named 'numpy'``. With the extra, every public name imports as before."""
from __future__ import annotations

import json
import subprocess
import sys

import g6.abstraction

_CHILD = r'''
import importlib.abc, json, sys
BLOCKED = {"numpy", "hypothesis"}
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, Absent())
out = {}
import g6.abstraction as a
out["extras"] = a.__g6_extras__
from g6.mcp import describe_reply, invoke_reply
out["described"] = describe_reply("abstraction").get("name")
reply = invoke_reply("abstraction", "evaluate", "{}")
out["invoke"] = [reply["ok"], reply["warning_card"]["code"]]
try:
    from g6.abstraction import Library
except ImportError as exc:
    out["library"] = [type(exc).__name__, str(exc)]
print(json.dumps(out))
'''


def test_the_package_imports_and_answers_without_numpy_or_hypothesis(tmp_path) -> None:
    done = subprocess.run([sys.executable, "-I", "-c", _CHILD], cwd=tmp_path, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["extras"] == {"abstraction": ["hypothesis", "numpy"]} and out["described"] == "abstraction"
    assert out["invoke"][0] is False and out["invoke"][1].startswith("G6_E_")
    kind, message = out["library"]
    assert kind in ("ImportError", "ModuleNotFoundError")
    assert "pip install" in message and "g6[abstraction]" in message, message


def test_survival_with_the_extra_every_public_name_imports() -> None:
    from g6.abstraction import ConcreteDomain, Library, Quotient, replay, run_gate  # noqa: F401

    assert all(getattr(g6.abstraction, name) is not None for name in g6.abstraction.__all__)
    assert g6.abstraction.Library is Library and g6.abstraction.__version__ == "0.3.0"
