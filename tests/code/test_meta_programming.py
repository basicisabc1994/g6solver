# SPDX-License-Identifier: Apache-2.0
"""g6.code.meta_programming: a scope-aware rename and an extract that change only the characters they must, each
re-parsed, compiled and checked against the tree it should give; parse and analyze from the syntax tree; and the
caller's tests run in g6.core.sandbox, never in this process."""
from __future__ import annotations

import builtins
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.code.meta_programming as package
from g6.code.meta_programming import MetaProgrammingBlock, MetaProgrammingInput, edits
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply

MIXED = '''# counts things
count = 10  # the module's count


def tally(items, count=0):
    """Add up items; count is a parameter here."""
    for item in items:
        count += item.count  # an attribute named count
    return count


def report(box):
    total = count * 2  # the module's count
    print("count", box.count, f"{count}")
    return dict(count=total)
'''
RENAMED = '''# counts things
limit = 10  # the module's count


def tally(items, count=0):
    """Add up items; count is a parameter here."""
    for item in items:
        count += item.count  # an attribute named count
    return count


def report(box):
    total = limit * 2  # the module's count
    print("count", box.count, f"{limit}")
    return dict(count=total)
'''
PRICE = '''def total_price(items, tax):
    subtotal = 0
    for item in items:
        subtotal += item
    taxed = subtotal * (1 + tax)  # with tax
    return round(taxed, 2)
'''
EXTRACTED = '''def total_price(items, tax):
    subtotal = sum_items(items)
    taxed = subtotal * (1 + tax)  # with tax
    return round(taxed, 2)


def sum_items(items):
    subtotal = 0
    for item in items:
        subtotal += item
    return subtotal
'''


def run(op: str, **params) -> dict:
    return MetaProgrammingBlock().infer(MetaProgrammingInput(op=op, **params)).unwrap()


def namespace(source: str) -> dict:
    found: dict = {}
    exec(compile(source, "<test>", "exec"), found)  # the test's own fixture code
    return found


# --- D1: an edit never breaks the structure a text edit would ---------------------------------------------------------

def test_d1_a_rename_changes_one_binding_and_nothing_a_text_edit_would_hit() -> None:
    out = run("rename", source=MIXED, old="count", new="limit", line=2)
    assert out["edited"] == RENAMED and out["completion_state"] == "qualified-draft"
    assert [(c["line"], c["before"], c["after"]) for c in out["changes"]] == [(2, "count", "limit"),
                                                                            (13, "count", "limit"),
                                                                            (14, "count", "limit")]
    assert [c["check"] for c in out["checks"]] == ["parses_and_compiles", "tree_is_the_rename", "comments_kept"]
    naive = re.sub(r"\bcount\b", "limit", MIXED)  # the fragile text edit the failure mode names
    assert "dict(limit=total)" in naive and "item.limit" in naive and "dict(count=total)" in out["edited"]
    assert namespace(out["edited"])["report"](type("Box", (), {"count": 1})()) == {"count": 20}


def test_d1_an_edit_that_does_not_give_the_tree_it_should_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = edits._apply

    def drops_one(source: str, spans: list) -> str:
        return real(source, sorted(spans)[:-1])

    monkeypatch.setattr(edits, "_apply", drops_one)
    out = run("rename", source=MIXED, old="count", new="limit", line=2)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_SYNTAX_EDIT_UNCHECKED"
    assert "edited" not in out


# --- the archive's findings, each with a test where its path survives -------------------------------------------------

def test_meta_programming_01_parameters_are_renamed_and_another_scopes_name_is_not() -> None:
    source = "def f(old):\n    return old  # old stays here\n\n\ndef g(old):\n    return old\n"
    out = run("rename", source=source, old="old", new="count", line=1)
    assert out["edited"] == "def f(count):\n    return count  # old stays here\n\n\ndef g(old):\n    return old\n"
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["verified"] is False


def test_meta_programming_01_a_name_bound_in_two_scopes_needs_a_line() -> None:
    source = "def f(old):\n    return old\n\n\ndef g(old):\n    return old\n"
    out = run("rename", source=source, old="old", new="count")
    assert out["code"] == "G6_E_SYNTAX_RENAME_REFUSED" and "give line" in out["warning_card"]["message"]


def test_meta_programming_01_closures_globals_imports_and_except_names_follow_their_binding() -> None:
    source = ("import os as old\nfrom json import dumps\n\n\ndef outer():\n    x = 1\n\n    def inner():\n"
              "        nonlocal x\n        x += 1\n        return x\n    return inner\n\n\ndef use():\n"
              "    global old\n    try:\n        return old.sep\n    except OSError as error:\n        return error\n")
    out = run("rename", source=source, old="old", new="osmod")
    assert out["edited"].splitlines()[0] == "import os as osmod"
    assert "    global osmod" in out["edited"] and "return osmod.sep" in out["edited"]
    closure = run("rename", source=source, old="x", new="n")
    assert "    n = 1" in closure["edited"] and "nonlocal n" in closure["edited"] and "n += 1" in closure["edited"]
    bare = run("rename", source=source, old="dumps", new="to_json")
    assert bare["edited"].splitlines()[1] == "from json import dumps as to_json"
    caught = run("rename", source=source, old="error", new="problem")
    assert "except OSError as problem:" in caught["edited"] and "return problem" in caught["edited"]


def test_meta_programming_01_a_rename_that_would_capture_or_change_an_interface_is_refused() -> None:
    capture = run("rename", source="total = 1\n\n\ndef f(x):\n    count = 2\n    return total + count\n",
                  old="total", new="count")
    keyword = run("rename", source="def f(old):\n    return old\n\n\nf(old=1)\n", old="old", new="new", line=1)
    in_class = run("rename", source="class A:\n    old = 1\n", old="old", new="new")
    assert capture["code"] == keyword["code"] == in_class["code"] == "G6_E_SYNTAX_RENAME_REFUSED"
    assert "by keyword" in keyword["warning_card"]["message"] and "attribute" in in_class["warning_card"]["message"]


def test_meta_programming_02_no_op_marks_its_output_verified() -> None:
    replies = [run("parse", source=MIXED), run("analyze", source=MIXED), run("parse", source="def broken(:\n"),
               run("rename", source=MIXED, old="count", new="limit", line=2),
               run("rename", source=MIXED, old="absent", new="other"),
               run("extract_function", source=PRICE, start_line=2, end_line=4, name="sum_items")]
    assert {r["completion_state"] for r in replies} <= {"qualified-draft", "blocked-escalated"}
    assert replies[4]["code"] == "G6_E_SYNTAX_RENAME_REFUSED"  # a rename of nothing is a refusal, never a no-op


def test_meta_programming_03_the_callers_code_runs_in_the_sandbox_not_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_TEST_SECRET_VALUE", "s3cret")
    code = "import builtins, os\nbuiltins.G6_META_RAN = True\nSEEN = os.environ.get('G6_TEST_SECRET_VALUE')\n"
    tests = "import subject\n\ndef test_no_secret():\n    assert subject.SEEN is None\n"
    out = run("run_tests", source=code, tests=tests)
    assert out["completion_state"] == "qualified-draft" and out["results"][0]["outcome"] == "passed"
    assert not hasattr(builtins, "G6_META_RAN")
    assert "a claim, not a check G6 made" in out["evidence"][0]["whose"]


def test_meta_programming_04_the_package_ships_no_book_and_no_vendored_folder() -> None:
    folder = Path(package.__file__).parent
    shipped = {p.suffix for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert shipped <= {".py", ".md"}
    assert not [p for p in folder.rglob("*") if "code-graph-rag" in str(p) or "source code" in str(p)]


def test_meta_programming_05_06_no_model_op_no_npx_and_no_program_but_the_sandbox() -> None:
    assert list(package.block._OPS) == ["parse", "analyze", "rename", "extract_function", "run_tests"]
    assert package.__g6_outside__ == () and package.__g6_extras__ == {}


# --- extract_function -------------------------------------------------------------------------------------------------

def test_extract_moves_whole_statements_with_the_names_they_need() -> None:
    out = run("extract_function", source=PRICE, start_line=2, end_line=4, name="sum_items")
    assert out["edited"] == EXTRACTED and out["params"] == ["items"] and out["returns"] == ["subtotal"]
    assert [c["check"] for c in out["checks"]] == ["parses_and_compiles", "moved_statements_unchanged",
                                                   "call_replaces_the_statements"]
    before, after = namespace(PRICE)["total_price"], namespace(out["edited"])["total_price"]
    assert before([1, 2, 3], 0.1) == after([1, 2, 3], 0.1) == 6.6


def test_extract_reads_x_equals_x_plus_one_as_a_parameter_and_keeps_strings_as_they_are() -> None:
    source = ('def f(x, label):\n    x = x + 1\n    text = """first\nsecond"""\n    return x, text, label\n')
    out = run("extract_function", source=source, start_line=2, end_line=4, name="step")
    assert out["params"] == ["x"] and out["returns"] == ["text", "x"]
    assert namespace(out["edited"])["f"](1, "a") == namespace(source)["f"](1, "a")


@pytest.mark.parametrize(("source", "lines", "said"), [
    ("def f(a):\n    b = a\n    return b\n", (2, 3), "a return"),
    ("def f(a):\n    if a:\n        b = 1\n    return b\n", (2, 3), "some paths"),
    ("def f(a):\n    for x in a:\n        if x:\n            break\n    return a\n", (3, 4), "break or continue"),
    ("def f(a):\n    b = (a +\n         1)\n    return b\n", (2, 2), "cut the statement"),
    ("x = 1\ny = x\n", (2, 2), "inside a function"),
    ("def f(a):\n    b = a\n\n    def g():\n        return b\n    b = 2\n    return g\n", (6, 6), "nested function"),
])
def test_extract_refuses_what_cannot_leave_its_function(source: str, lines: tuple[int, int], said: str) -> None:
    out = run("extract_function", source=source, start_line=lines[0], end_line=lines[1], name="moved")
    assert out["completion_state"] == "blocked-escalated" and said in out["warning_card"]["message"], out


# --- the gates --------------------------------------------------------------------------------------------------------

def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for field in ("approved", "verified", "tests_passed", "confidence"):
        with pytest.raises(ValidationError):
            MetaProgrammingInput(op="rename", source="x = 1", old="x", new="y", **{field: True})


def test_g4_failures_tighten() -> None:
    broken = run("rename", source="def f(:\n", old="f", new="g")
    failing = run("run_tests", source="def add(a, b):\n    return a - b\n",
                  tests="from subject import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    silent = run("run_tests", source="import sys\nsys.exit(0)\n", tests="import subject\n\ndef test_x():\n    pass\n")
    slow = run("run_tests", source="while True:\n    pass\n", tests="import subject\n", timeout_s=2)
    assert broken["code"] == "G6_E_SYNTAX_DOES_NOT_PARSE" and failing["code"] == "G6_E_SYNTAX_TESTS_FAILED"
    assert silent["code"] == slow["code"] == "G6_E_SYNTAX_TESTS_DID_NOT_RUN"
    assert {r["completion_state"] for r in (broken, failing, silent, slow)} == {"blocked-escalated"}


def test_g5_running_tests_runs_code_so_it_is_refused_on_an_untrusted_surface() -> None:
    assert package.__g6_sensitive_ops__ == ("run_tests",)
    said = str(get_registry().invoke("meta_programming", "run_tests", {"source": "x = 1", "tests": "def test_x(): pass"},
                                     block_sensitive=True).failure())
    assert said.startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


def test_parse_and_analyze_read_the_tree() -> None:
    parsed = run("parse", source="def broken(:\n")
    assert parsed["parses"] is False and parsed["error"]["line"] == 1 and parsed["code"] == "G6_E_SYNTAX_DOES_NOT_PARSE"
    found = run("analyze", source=MIXED)
    assert [(f["qualname"], f["params"], f["complexity"]) for f in found["functions"]] == [
        ("tally", ["items", "count"], 2), ("report", ["box"], 1)]
    assert found["module_names"] == ["count", "report", "tally"]


# --- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("fragile_text_edit",)
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.code.meta_programming" not in FailureModeRegistry.planned()


def test_d6_rename_round_trips_over_mcp_and_describe_serves_the_skill() -> None:
    reply = invoke_reply("meta_programming", "rename", json.dumps({"source": MIXED, "old": "count", "new": "limit",
                                                                   "line": 2}))
    assert reply["ok"] and reply["result"]["edited"] == RENAMED
    assert describe_reply("meta_programming")["skill"].startswith("---\nname: g6-meta-programming")
