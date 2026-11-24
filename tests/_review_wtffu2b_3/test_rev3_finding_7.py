# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 7: the writer inventory lists names, so a raw writer under another name passes it.

``tests/harness/test_completion_internal_gate.py::test_completion_mutation_inventory_has_no_unguarded_writer`` says "A
newly added direct writer fails this inventory, even for a new filename". Its visitor flags a call only when the last
name of the callee is in a hand-written set. The visitor is taken from that test as it is (not rewritten here) and shown
a function that writes with each of several calls the set does not hold, or reaches a listed one indirectly.
"""
import ast
from pathlib import Path

import pytest

INVENTORY = Path(__file__).parents[1] / "harness" / "test_completion_internal_gate.py"
TEST = "test_completion_mutation_inventory_has_no_unguarded_writer"


def _flagged(body: str) -> set:
    """What the inventory's own visitor finds in a function with ``body``."""
    tree = ast.parse(INVENTORY.read_text(encoding="utf-8"))
    test = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == TEST)
    parts = [n for n in test.body
             if (isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) in ("mutations", "found"))
             or (isinstance(n, ast.FunctionDef) and n.name == "visit")][:3]
    assert [type(n).__name__ for n in parts] == ["Assign", "Assign", "FunctionDef"]
    space = {"ast": ast}
    exec(compile(ast.Module(body=parts, type_ignores=[]), str(INVENTORY), "exec"), space)
    lines = "\n".join("    " + line for line in body.split("\n"))
    space["visit"](ast.parse(f"def writer(p, a, b, session, state, handle):\n{lines}\n"))
    return space["found"]


@pytest.mark.parametrize("body", ["p.write_text('x')", "os.replace(a, b)", "shutil.rmtree(p)", "open(p, 'w')"])
def test_control_the_visitor_flags_the_writers_it_lists(body):
    assert _flagged(body)


def test_control_the_visitor_passes_a_gated_writer():
    assert not _flagged("store.write_text(p, 'x')")


@pytest.mark.parametrize("body", [
    "shutil.move(a, b)", "os.truncate(p, 0)", "tempfile.mkstemp(dir=session)", "shutil.copyfileobj(a, b)",
    "json.dump(state, handle)", "os.utime(p)", "os.removedirs(p)", "p.chmod(0)",
    "getattr(p, 'write_text')('x')", "w = p.write_bytes\nw(b'')",
])
def test_a_raw_writer_under_another_name_fails_the_inventory(body):
    assert _flagged(body), f"the inventory passes a completion module that calls {body!r}"
