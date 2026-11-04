# SPDX-License-Identifier: Apache-2.0
"""Replay 6 (PLAN.md, 2026-11-04, evening): a model of the issue's rule as data, a domain of named factors, a probe and a rule
table from reader 1 and a second table from reader 2, checked by G6 in plain code on every case, compiled to one pytest file and
run on replay 3's machinery. Fakes stand in for the container, Codex and Claude: nothing here runs a model or a container."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import final_review as fr  # noqa: E402
import replay3 as r3  # noqa: E402
import replay4 as r4  # noqa: E402
import replay5 as r5  # noqa: E402
import replay6 as r6  # noqa: E402
import replay_compare as rc  # noqa: E402
import run as we  # noqa: E402
from g6.core import host_models  # noqa: E402
from g6.core.host_models import ClaudeCodeCaller  # noqa: E402
from g6.core.llm import LLMError  # noqa: E402

TITLE = "Padding is lost on short numbers"
TEXT = ("When width is 3 the  result\nshould be padded to three characters.\n\n"
        "A negative width raises ValueError. The size always equals the length.")
ISSUE = f"{TITLE}\n\n{TEXT}"
PROBE = ("def observe(case):\n"
         "    out = str(case['n']).rjust(case['width'])\n"
         "    return {'length': len(out), 'size': len(out), 'out': out}\n")
FACTORS = {"n": [1, 22], "width": [3, 4]}
OBSERVATIONS = ["length", "size", "out"]
QUOTE = "the result should be padded to three characters"
PADDED = {"when": {"width": 3}, "expect": {"length": 3}, "quote": QUOTE}
SAME = {"when": {}, "expect": {"size": {"equals": "length"}}, "quote": "The size always equals the length."}
FAILS = we.ProbeRun(1, {"t::test_formal[c000]": "failed", "t::test_formal[c001]": "passed"}, {}, "")
HOLDS = we.ProbeRun(0, {"t::test_formal[c000]": "passed", "t::test_formal[c001]": "passed"}, {}, "")


def model(**over) -> str:
    return json.dumps({"factors": FACTORS, "observations": OBSERVATIONS, "probe": PROBE, "rules": [PADDED], **over})


def rules(*rows) -> str:
    return json.dumps({"rules": list(rows)})


def wrote(text: str) -> dict[str, str]:
    """Reader 1's answer as ``final_review._step`` reads it, the model where replay 3's reply carries its test."""
    return {"verdict": "defect", "rule": "R1_RULE_LINE", "why": "R1_WHY_LINE", "test": text, "error": ""}


class Caller:
    """Reader 2's fake: a reply, or an error, and every call kept."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user, **_):
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _load(src: str) -> dict:
    space: dict = {"__name__": "test_formal"}
    exec(compile(src, "test_formal.py", "exec"), space)       # the fake probe above, never a model's
    return space


def _rows(root: Path, n: int, task: str = "black-5634") -> list[dict[str, str]]:
    (root / "loop").mkdir(exist_ok=True)
    rows = []
    for k in range(n):
        (root / f"run{k}").mkdir(parents=True)
        (root / f"run{k}" / "final.patch").write_text(f"patch {k}\n", "utf-8")
        rows.append({"task": task, "run": f"run{k}"})
    return rows


def _review(tmp_path, found, caller, base=FAILS, now=FAILS, n=2):
    asked, ran = [], []

    def step(ctx, task, patch, title, text, prompt, where, file, *, candidate, run):
        asked.append((prompt, where, file, candidate, run, title, text))
        return found, None, None

    def run(ctx, task, test, patch):
        ran.append(test)
        return base, now

    rows = _rows(tmp_path, n)
    task = we.task_by_id("black-5634")
    got = r6.review_bug(SimpleNamespace(archive=SimpleNamespace(root=tmp_path)), tmp_path, task, rows, TITLE, TEXT, step=step,
                        caller=caller, run=run)
    saved = fr.read(r3.bug_record(tmp_path, task.id, r6.FORMAL))
    return got, asked, ran, (saved or {}).get("found"), rows


# --- the instruction --------------------------------------------------------------------------------------------------------
def test_replay_6_is_its_own_instruction_with_its_own_digest_folder_and_place():
    ins = r6.FORMAL
    assert ins.text == r6.FORMAL_FIRST and ins.digest == hashlib.sha256(r6.FORMAL_FIRST.encode("utf-8")).hexdigest()[:16]
    assert ins.folder == f"final_review_formal-{ins.digest}" == r6.FOLDER
    assert ins.folder not in (r3.FOLDER, r4.FOLDER, r5.FOLDER, fr.FOLDER, fr.REPRODUCE)
    assert ins.at == f"{we.WE}/formal" and ins.file == "test_formal.py" and ins.at not in (r3.RULE_AT, r4.SYMPTOM.at, r5.RELATION.at)


def test_the_instructions_name_no_bug_or_project_and_keep_replay_3s_limits():
    for text in (r6.FORMAL_FIRST.lower(), r6.READER_2_SYSTEM.lower()):
        for task in (*we.load_tasks(), *we.load_tasks(we.TASKS_FRESH), *we.load_tasks(we.TASKS_UNSEEN),
                     *we.load_tasks(we.TASKS_STAGE4)):
            assert task.id not in text and task.full_name.lower() not in text
        for word in ("verified", "prove", "proof", "provably", "experimental"):
            assert word not in text
    text = r6.FORMAL_FIRST
    limits = r3.RULE_FIRST[r3.RULE_FIRST.index("<hard_limits>"):r3.RULE_FIRST.index("<structured_output_contract>")]
    assert limits in text and text.endswith(r3.RULE_FIRST[r3.RULE_FIRST.index("<default_follow_through_policy>"):])
    for said in ("READ:", "RULE:", "VERDICT: defect or none", "WHY:", "```python"):
        assert said in text                           # replay 3's reply form, which ``final_review._step`` reads
    assert "512" in text and "observe(case" in text and '"quote"' in text and '{"equals"' in text
    assert "write no model" in text and "pytest file" not in text


def test_replay_3s_and_replay_5s_instructions_digests_and_folders_are_unchanged():
    assert r3.INSTRUCTION == "838d8da877502eee" and r3.FOLDER == "final_review_rule-838d8da877502eee"
    assert r3.rule() == r3.Instruction(r3.RULE_FIRST, r3.INSTRUCTION, r3.FOLDER, f"{we.WE}/rule", "test_rule.py")
    assert r5.INSTRUCTION == "fd779fd79374dfe4" and r5.FOLDER == "final_review_relation-fd779fd79374dfe4"
    assert r5.RELATION.at == f"{we.WE}/relation" and r5.RELATION.file == "test_relation.py"


# --- G6's checks ------------------------------------------------------------------------------------------------------------
def test_a_model_of_the_reply_form_parses():
    got, why = r6.parse_model(model(rules=[PADDED, SAME]))
    assert why == "" and got == {"factors": FACTORS, "observations": OBSERVATIONS, "probe": PROBE, "rules": [PADDED, SAME]}


@pytest.mark.parametrize(("text", "says"), [
    (model(factors={"a": list(range(8)), "b": list(range(8)), "c": list(range(9))}, rules=[]), "576 cases"),
    (model(factors={"n": [], "width": [3]}), "0 cases"),
    (model(factors={"n": [1, {"x": 1}], "width": [3]}), "factor"),
    (model(factors={"n": [1, 1], "width": [3]}), "factor"),
    (model(rules=[{"when": {"width": 3}, "expect": {"colour": 3}, "quote": QUOTE}]), "observation"),
    (model(rules=[{"when": {}, "expect": {"size": {"equals": "colour"}}, "quote": QUOTE}]), "observation"),
    (model(rules=[{"when": {"height": 3}, "expect": {"length": 3}, "quote": QUOTE}]), "factor"),
    (model(rules=[{"when": {"width": 5}, "expect": {"length": 3}, "quote": QUOTE}]), "value"),
    (model(rules=[{"when": {"width": 3}, "expect": {}, "quote": QUOTE}]), "expect"),
    (model(rules=[{"when": {"width": 3}, "expect": {"length": 3}}]), "quote"),
    (model(observations=["length", "not a name"]), "identifier"),
    (model(probe="  "), "probe"),
    ("this is not JSON", "JSON"),
    ('{"factors": {"n": [NaN]}, "observations": ["x"], "probe": "p", "rules": []}', "JSON"),
    ("[1, 2]", "object"),
])
def test_a_model_that_breaks_its_form_is_invalid_and_says_why(text, says):
    got, why = r6.parse_model(text)
    assert got is None and says in why


@pytest.mark.parametrize("reply", ["", "no rules here", "[]", '{"rules": {}}', '{"other": []}', '{"rules": []} and more',
                                   '{"rules": [NaN]}'])
def test_a_reply_from_reader_2_that_is_not_one_json_object_with_a_rules_list_is_no_answer(reply):
    assert r6.reader_2_rules(reply) is None


def test_reader_2s_one_json_object_is_read_bare_or_in_one_fence():
    assert r6.reader_2_rules(rules(PADDED)) == [PADDED]
    assert r6.reader_2_rules(f"```json\n{rules(PADDED)}\n```\n") == [PADDED]


def test_a_quote_not_in_the_issue_drops_its_row_and_whitespace_differences_do_not():
    spaced = {**PADDED, "quote": "  the result   should\tbe padded\nto three characters "}
    elsewhere = {**PADDED, "quote": "the result should be padded to four characters"}
    empty = {**PADDED, "quote": "   "}
    assert r6.kept_rows([spaced, elsewhere, empty, SAME], ISSUE) == ([spaced, SAME], 2)


def test_a_table_that_matches_one_case_two_ways_conflicts_with_itself_and_is_refused():
    other = {"when": {"n": 1}, "expect": {"length": 4}, "quote": QUOTE}       # n=1, width=3 is both 3 and 4
    assert r6.table([PADDED, other], FACTORS) is None
    again = {"when": {"n": [1, 22], "width": 3}, "expect": {"length": 3}, "quote": QUOTE}
    assert r6.table([PADDED, again], FACTORS) == {(r6.canon({"n": n, "width": 3}), "length"): 3 for n in (1, 22)}
    got = r6.formal(wrote(model(rules=[PADDED, other])), TITLE, TEXT, Caller(rules(PADDED)))
    assert got["status"] == "conflict" and got["test"] == "" and got["verdict"] == "none" and not r3.no_answer(got)


def test_a_table_value_is_one_of_its_factors_values_before_it_is_a_list_of_them():
    factors = {"pair": [[1, 2], 1, 2]}
    assert r6.table([{"when": {"pair": [1, 2]}, "expect": {"x": 0}, "quote": QUOTE}], factors) == {(r6.canon({"pair": [1, 2]}), "x"): 0}
    assert set(r6.table([{"when": {"pair": [1, 2, [1, 2]]}, "expect": {"x": 0}, "quote": QUOTE}], factors)) == {
        (r6.canon({"pair": v}), "x") for v in ([1, 2], 1, 2)}


def test_two_tables_that_differ_on_one_covered_cell_make_the_bug_ambiguous_and_nothing_is_compiled(tmp_path):
    second = rules({"when": {"width": 3, "n": 1}, "expect": {"length": 4}, "quote": QUOTE},
                   {"when": {"width": 3, "n": 22}, "expect": {"length": 3}, "quote": QUOTE})
    got, _, ran, found, rows = _review(tmp_path, wrote(model()), Caller(second))
    assert found["status"] == "ambiguous" and found["test"] == "" and ran == []
    assert found["comparison"]["cells_differ"] == 1 and found["comparison"]["cells_agree"] == 1
    assert found["comparison"]["cases_differ"] == 1
    assert [g["said"] for g in got] == ["none", "none"] and all(fr.saved(tmp_path, row, r6.FOLDER) for row in rows)


def test_tables_that_agree_compile_to_a_file_whose_cases_are_exactly_the_agreed_cells():
    first = model(rules=[PADDED, {"when": {"n": 1}, "expect": {"out": "  1"}, "quote": QUOTE}])
    second = rules(PADDED, {"when": {"width": 4}, "expect": {"length": 4}, "quote": QUOTE})
    got = r6.formal(wrote(first), TITLE, TEXT, Caller(second))
    assert got["status"] == "stands" and got["verdict"] == "defect" and got["model"] == first
    assert got["comparison"]["cells_agree"] == 2 and got["comparison"]["cells_differ"] == 0
    assert got["comparison"]["cells_reader_1_only"] == 2 and got["comparison"]["cells_reader_2_only"] == 2
    assert got["tables"] == {"reader_1": json.loads(first)["rules"], "reader_2": json.loads(second)["rules"]}
    space = _load(got["test"])
    cells = space["_G6_CELLS"]
    assert {(r6.canon(cell["case"]), cell["observation"]) for cell in cells} == {
        (r6.canon({"n": n, "width": 3}), "length") for n in (1, 22)}
    assert [cell["want"] for cell in cells] == [r6.canon(3)] * 2 and len({cell["id"] for cell in cells}) == 2
    assert all(cell["id"].startswith(("c000-length", "c002-length")) for cell in cells)
    for cell in cells:
        space["test_formal"](cell)                    # the fake probe pads both, so each agreed case holds
    assert r6.formal(wrote(first), TITLE, TEXT, Caller(second))["test"] == got["test"]     # the same file every time


def test_the_compiled_file_embeds_no_model_text_but_the_probe():
    evil = '"); import os; ("'
    factors = {"n": [1], "width": [3], "wid'th\"); import os; (\"": ["'); import os; ('"]}
    row = {"when": {"width": 3}, "expect": {"out": evil}, "quote": QUOTE}
    got = r6.formal(wrote(model(factors=factors, rules=[row])), TITLE, TEXT, Caller(rules(row)))
    src = got["test"]
    assert got["status"] == "stands"
    tree = ast.parse(src)
    assert {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names} == {
        "json", "math", "os", "signal", "subprocess", "sys", "Decimal", "pytest"}  # G6's own; the probe's are in its text
    strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert strings.count(PROBE) == 1                  # the probe, as one text
    assert len([text for text in strings if "import os" in text]) == 1    # the one JSON text json.loads reads
    for text in (QUOTE, "R1_RULE_LINE", "R1_WHY_LINE"):
        assert text not in src
    cell = _load(src)["_G6_CELLS"][0]
    assert cell["want"] == r6.canon(evil) and cell["case"]["wid'th\"); import os; (\""] == "'); import os; ('"
    assert set(cell["id"]) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.,=+-")


def test_a_probe_that_defines_its_own_tests_adds_no_case():
    probe = PROBE + "\n\ndef test_extra():\n    assert 0\n\n\nclass TestMore:\n    def test_one(self):\n        assert 0\n"
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))
    space = _load(got["test"])
    assert "test_extra" not in space and "TestMore" not in space and "test_formal" in space


def test_an_equals_expectation_compiles_to_an_equality_of_two_observations():
    probe = ("def observe(case):\n"
             "    if case['width'] == 4:\n"
             "        raise KeyError('width')\n"
             "    if case['n'] == 22:\n"
             "        return {'length': 3, 'size': 4}\n"
             "    return {'length': 3, 'size': 3}\n")
    got = r6.formal(wrote(model(probe=probe, rules=[SAME])), TITLE, TEXT, Caller(rules(SAME)))
    assert got["status"] == "stands"
    space = _load(got["test"])
    by_case = {(cell["case"]["n"], cell["case"]["width"]): cell for cell in space["_G6_CELLS"]}
    assert set(by_case) == {(1, 3), (1, 4), (22, 3), (22, 4)}
    assert all(cell["observation"] == "size" and cell["equals"] == "length" and "want" not in cell for cell in by_case.values())
    space["test_formal"](by_case[1, 3])                                  # size equals length
    with pytest.raises(AssertionError):
        space["test_formal"](by_case[22, 3])                             # size is not length
    with pytest.raises(pytest.skip.Exception):
        space["test_formal"](by_case[1, 4])                              # observe raised: skipped, never a failure


def test_a_case_whose_observation_is_missing_is_skipped():
    probe = "def observe(case):\n    return {'size': 3}\n"
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))
    space = _load(got["test"])
    with pytest.raises(pytest.skip.Exception):
        space["test_formal"](space["_G6_CELLS"][0])


# --- one rule for a JSON value, wherever two are compared ---------------------------------------------------------------------
def test_one_spelling_compares_numbers_by_their_exact_value_and_a_boolean_never_as_a_number():
    assert r6.canon(2) == r6.canon(2.0) == r6.canon(Decimal("2.00")) == r6.canon(Decimal("0.2E+1"))
    assert r6.canon(Decimal("9007199254740992.0")) != r6.canon(Decimal("9007199254740993.0"))
    assert r6.canon(9007199254740993) == r6.canon(Decimal("9007199254740993.0")) != r6.canon(9007199254740992.0)
    assert r6.canon(0.1) == r6.canon(Decimal("0.1")) and r6.canon(-0.0) == r6.canon(0) and r6.canon(0.5) != r6.canon(0.25)
    assert len({r6.canon(value) for value in (True, 1, False, 0, None, "1", [1], {"a": 1}, [])}) == 9
    assert r6.canon({"b": [1, 2.0], "a": None}) == r6.canon({"a": None, "b": [1.0, 2]})
    for bad in (float("nan"), float("inf"), (1, 2), {1: 2}, object()):
        with pytest.raises(ValueError):
            r6.canon(bad)
    got, why = r6.parse_model(model(factors={"n": [2, 2.0], "width": [3]}))
    assert got is None and "repeats" in why                      # one value, by the one rule
    text = model(factors={"n": ["A", "B"], "width": [3]}).replace('"A"', "9007199254740992.0").replace('"B"', "9007199254740993.0")
    got, why = r6.parse_model(text)
    assert got is None and "exactly" in why                      # two values to the tables, and one float to the probe


@pytest.mark.parametrize("big", ["1e309", "-1e309", "1e9999999999999999999"])
@pytest.mark.parametrize(("first", "second", "says"), [
    (model(factors={"n": [1, "BIG"], "width": [3, 4]}), rules(PADDED), "the model holds"),
    (model(rules=[{**PADDED, "expect": {"length": "BIG"}}]), rules(PADDED), "the model holds"),
    (model(), rules({**PADDED, "expect": {"length": "BIG"}}), "reader 2"),
])
def test_a_number_no_float_holds_makes_the_bug_invalid_with_a_reason_and_never_aborts(first, second, says, big):
    got = r6.formal(wrote(first.replace('"BIG"', big)), TITLE, TEXT, Caller(second.replace('"BIG"', big)))
    assert got["status"] == "invalid" and got["test"] == "" and not r3.no_answer(got)
    assert says in got["status_reason"] and "no float" in got["status_reason"]


def test_the_compiled_assertion_compares_an_observation_by_the_tables_one_rule():
    probe = ("def observe(case):\n"
             "    seen = {'float': 2.0, 'bool': True, 'big': 9007199254740992.0}[case['kind']]\n"
             "    return {'length': seen, 'size': 2}\n")
    rows = [{"when": {"kind": "float"}, "expect": {"length": 2}, "quote": QUOTE},
            {"when": {"kind": "bool"}, "expect": {"length": 1}, "quote": QUOTE},
            {"when": {"kind": "big"}, "expect": {"length": "BIG"}, "quote": QUOTE},
            {"when": {}, "expect": {"size": {"equals": "length"}}, "quote": QUOTE}]
    first = model(factors={"kind": ["float", "bool", "big"]}, probe=probe, rules=rows).replace('"BIG"', "9007199254740993.0")
    got = r6.formal(wrote(first), TITLE, TEXT, Caller(rules(*rows).replace('"BIG"', "9007199254740993.0")))
    assert got["status"] == "stands" and got["comparison"]["cells_unholdable"] == 1     # big's length: no float holds it
    space, held = _load(got["test"]), {}
    for cell in space["_G6_CELLS"]:
        try:
            space["test_formal"](cell)
            held[cell["case"]["kind"], cell["observation"]] = True
        except AssertionError:
            held[cell["case"]["kind"], cell["observation"]] = False
    assert held == {("float", "length"): True, ("bool", "length"): False,
                    ("float", "size"): True, ("bool", "size"): False, ("big", "size"): False}


def test_the_compiled_file_carries_g6s_own_canon_as_the_rule_of_its_assertion():
    src = r6.formal(wrote(model()), TITLE, TEXT, Caller(rules(PADDED)))["test"]
    own = inspect.getsource(r6.canon)
    assert src.count(own) == 1 and src.count("def canon(") == 1
    assert src.index(own) < src.index("_G6_PROBE =")              # G6's fixed text, before any text of the model's
    space = _load(src)
    for value in (2, 2.0, True, None, "x", [1, [2.5]], {"a": 1}, -0.0, 9007199254740992.0):
        assert space["canon"](value) == r6.canon(value)


# --- the probe, in a module of its own ---------------------------------------------------------------------------------------
def test_the_probe_runs_in_a_module_of_its_own_so_no_name_it_defines_reaches_pytest():
    probe = PROBE + ("import pytest\nfrom unittest import SkipTest\n__test__ = False\npytestmark = pytest.mark.skip('probe')\n"
                     "def setup_module(module):\n    raise SkipTest('probe')\n"
                     "def setup_function(function):\n    raise SkipTest('probe')\n"
                     "def teardown_function(function):\n    raise SkipTest('probe')\n"
                     "@pytest.fixture(autouse=True)\ndef probe_fixture():\n    raise SkipTest('probe')\n"
                     "def pytest_collection_modifyitems(items):\n    items.clear()\n"
                     "def test_extra():\n    assert 0\n"
                     "class TestMore:\n    def test_one(self):\n        assert 0\n")
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))
    assert got["status"] == "stands"
    space = _load(got["test"])
    for cell in space["_G6_CELLS"]:
        space["test_formal"](cell)                    # observe, read from the probe's module, pads both cases
    probes = {"__test__", "pytestmark", "setup_module", "setup_function", "teardown_function", "probe_fixture",
              "pytest_collection_modifyitems", "test_extra", "TestMore", "observe", "SkipTest"}
    assert not probes & set(space)
    assert {name for name in space if name.startswith(("test", "Test", "setup", "teardown", "pytest_"))} == {"test_formal"}


@pytest.mark.parametrize("probe", ["raise ImportError('no such module')\n" + PROBE, "import sys\nsys.exit(3)\n",
                                   PROBE + "import pytest\npytest.fail('as it runs')\n"])
def test_a_probe_that_raises_as_it_runs_skips_every_cell_and_fails_none(probe):
    """A probe the parser cannot read never runs: the follow-up makes it invalid (``test_replay6_followup``)."""
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))
    space = _load(got["test"])                        # the probe has not run: no name of it is this file's
    assert len(space["_G6_CELLS"]) == 2
    for cell in space["_G6_CELLS"]:
        with pytest.raises(pytest.skip.Exception):
            space["test_formal"](cell)


# --- the two readers --------------------------------------------------------------------------------------------------------
def test_reader_2_is_given_no_rule_of_reader_1s_and_no_parent_tree(tmp_path):
    first = {"when": {"width": 3}, "expect": {"out": "R1_ONLY_VALUE"}, "quote": QUOTE}
    caller = Caller(rules(PADDED))
    _, asked, _, _, _ = _review(tmp_path, wrote(model(rules=[first, PADDED])), caller)
    assert asked == [(r6.FORMAL_FIRST, r6.FORMAL.at, r6.FORMAL.file, False, False, TITLE, TEXT)]
    [(system, user)] = caller.calls
    assert system == r6.READER_2_SYSTEM
    assert user == r6.reader_2_text(ISSUE, {"factors": FACTORS, "observations": OBSERVATIONS, "probe": PROBE})
    assert ISSUE in user and PROBE in user and json.dumps(OBSERVATIONS) in user and json.dumps(FACTORS, indent=1) in user
    for text in ("R1_ONLY_VALUE", "R1_RULE_LINE", "R1_WHY_LINE", "parent/", "final.patch", "patch 0"):
        assert text not in system and text not in user


def test_reader_2_is_claude_with_no_tool_and_the_two_settings_given_in_its_settings_file(tmp_path):
    caller = r6.reader_2()
    assert isinstance(caller, ClaudeCodeCaller) and caller.model == "claude-opus-5-5"
    work, files = tmp_path / "work", tmp_path / "files"
    work.mkdir()
    files.mkdir()
    argv, stdin = caller._command("claude", "the system", "the request", work, files)   # builds the command; runs nothing
    assert stdin == "the request" and "the request" not in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--model") + 1] == "claude-opus-5-5"
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text("utf-8"))
    assert settings == {"disableAllHooks": True,
                        "env": {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1"}}


@pytest.mark.parametrize(("printed", "answers"), [
    ({"type": "result", "subtype": "success", "is_error": False, "result": "R"}, True),
    ({"type": "result", "subtype": "success", "result": "R"}, True),
    ({"type": "result", "subtype": "error_max_turns", "is_error": False, "result": "R"}, False),
    ({"type": "result", "subtype": "error_during_execution", "result": "R"}, False),
    ({"type": "result", "subtype": "a_status_not_yet_named", "result": "R"}, False),
    ({"type": "result", "result": "R"}, False),
    ({"type": "result", "subtype": "success", "is_error": True, "result": "R"}, False),
    ("not one JSON result {", False),
])
def test_reader_2_takes_a_reply_only_from_a_result_claude_code_marks_a_success(tmp_path, monkeypatch, printed, answers):
    monkeypatch.setattr(host_models, "_tail", lambda text, hide: "<withheld>")     # no key file is read for an error
    printed = printed if isinstance(printed, str) else json.dumps(printed)
    reader = r6.reader_2()
    if answers:
        assert reader._reply(0, printed, "", tmp_path, ()) == "R"
    else:
        with pytest.raises(LLMError):
            reader._reply(0, printed, "", tmp_path, ())


# --- replay 3's machinery, with fakes for the step, reader 2 and the runs ----------------------------------------------------
def test_an_agreed_case_that_fails_at_the_parent_and_on_a_patch_reproduces(tmp_path):
    got, _, ran, found, rows = _review(tmp_path, wrote(model()), Caller(rules(PADDED)), base=FAILS, now=FAILS)
    assert found["status"] == "stands" and ran == [found["test"], found["test"]] and repr(PROBE) in ran[0]
    assert [g["said"] for g in got] == ["reproduced", "reproduced"] and all(g["instruction"] == r6.FORMAL.digest for g in got)
    assert all(fr.saved(tmp_path, row, r6.FOLDER)["said"] == "reproduced" for row in rows)
    assert all(fr.saved(tmp_path, row, folder) is None for row in rows for folder in (r3.FOLDER, r4.FOLDER, r5.FOLDER))


def test_an_agreed_case_that_fails_only_at_the_parent_reproduces_nothing_on_the_patch(tmp_path):
    got, _, ran, found, _ = _review(tmp_path, wrote(model()), Caller(rules(PADDED)), base=FAILS, now=HOLDS)
    assert found["status"] == "stands" and len(ran) == 2
    assert [(g["said"], g["reason"]) for g in got] == [("none", "every case holds with the patch")] * 2


def test_a_patch_whose_failing_cases_all_held_at_the_parent_is_not_flagged(tmp_path):
    """Replay 6's registration: a patch is flagged when an agreed case that fails on the parent still fails on it. Its text,
    digest and folder are as they were."""
    assert r6.FORMAL.same_case and r6.INSTRUCTION == "b8df0b33deca6844" and r6.FOLDER == "final_review_formal-b8df0b33deca6844"
    other = we.ProbeRun(1, {"t::test_formal[c000]": "passed", "t::test_formal[c001]": "failed"}, {}, "")
    got, _, ran, found, _ = _review(tmp_path, wrote(model()), Caller(rules(PADDED)), base=FAILS, now=other)
    assert found["status"] == "stands" and len(ran) == 2
    assert [(g["said"], g["reason"]) for g in got] == [("none", "every case that fails with the patch passed on the parent")] * 2


def test_a_model_whose_cases_all_hold_at_the_parent_reproduces_nothing(tmp_path):
    got, _, _, found, _ = _review(tmp_path, wrote(model()), Caller(rules(PADDED)), base=HOLDS, now=FAILS)
    assert found["status"] == "stands"
    assert {g["reason"] for g in got} == {"the test fails no case on the parent, so it reproduces nothing"}


def test_a_reader_2_that_gives_no_json_object_is_no_answer_and_is_asked_again(tmp_path):
    for n, reply in enumerate(("I cannot help with that.", LLMError("G6_E_LLM_HOST_FAILED: claude exited 1"))):
        root = tmp_path / str(n)
        root.mkdir()
        got, _, ran, found, _ = _review(root, wrote(model()), Caller(reply), n=1)
        assert found is None and ran == [] and [g["said"] for g in got] == ["failed"] and "reader 2" in got[0]["error"]


@pytest.mark.parametrize(("found", "status"), [
    ({"verdict": "none", "rule": "", "why": "", "test": "", "error": ""}, "no_model"),
    (wrote("not json"), "invalid"),
    (wrote(model(rules=[{"when": {"width": 3}, "expect": {"colour": 1}, "quote": QUOTE}])), "invalid"),
])
def test_reader_1_with_no_model_or_an_invalid_one_asks_no_reader_2_and_runs_nothing(tmp_path, found, status):
    caller = Caller(rules(PADDED))
    got, _, ran, saved, _ = _review(tmp_path, found, caller)
    assert saved["status"] == status and caller.calls == [] and ran == [] and [g["said"] for g in got] == ["none", "none"]


def test_reader_2_rows_must_name_reader_1s_factors_and_observations(tmp_path):
    got = r6.formal(wrote(model()), TITLE, TEXT, Caller(rules({"when": {"height": 1}, "expect": {"length": 3}, "quote": QUOTE})))
    assert got["status"] == "invalid" and "reader 2" in got["status_reason"] and got["test"] == ""


def test_tables_with_no_cell_in_common_have_no_agreed_case(tmp_path):
    got, _, ran, found, _ = _review(tmp_path, wrote(model()), Caller(rules(SAME)))
    assert found["status"] == "no_agreed_case" and ran == [] and found["comparison"]["cells_both"] == 0


def test_dropped_rows_are_counted_and_a_table_left_empty_has_no_agreed_case():
    elsewhere = {**PADDED, "quote": "words the issue never says"}
    got = r6.formal(wrote(model(rules=[elsewhere])), TITLE, TEXT, Caller(rules(PADDED, elsewhere)))
    assert got["dropped"] == {"reader_1": 1, "reader_2": 1} and got["status"] == "no_agreed_case"


def test_a_saved_model_runs_again_with_no_call(tmp_path):
    _review(tmp_path, wrote(model()), Caller(rules(PADDED)), n=1)
    for path in (tmp_path / "loop" / r6.FOLDER).glob("*.json"):
        path.unlink()                                 # the patch's verdict goes; the bug's record stays
    steps, caller = [], Caller(rules(PADDED))

    def step(*args, **kwargs):
        steps.append(args)
        return wrote(model()), None, None

    got = r6.review_bug(SimpleNamespace(archive=SimpleNamespace(root=tmp_path)), tmp_path, we.task_by_id("black-5634"),
                        [{"task": "black-5634", "run": "run0"}], TITLE, TEXT, step=step, caller=caller,
                        run=lambda ctx, task, test, patch: (FAILS, FAILS))
    assert steps == [] and caller.calls == [] and [g["said"] for g in got] == ["reproduced"]


# --- the counts -------------------------------------------------------------------------------------------------------------
def test_the_summary_counts_each_bugs_status_and_whether_the_readers_disagreed(tmp_path):
    def keep(task, found):
        path = r3.bug_record(tmp_path, task, r6.FORMAL)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task": task, "instruction": r6.FORMAL.digest, "found": found}), "utf-8")

    keep("a-1", r6.formal(wrote(model()), TITLE, TEXT, Caller(rules(PADDED))))
    keep("b-2", r6.formal(wrote(model()), TITLE, TEXT, Caller(rules({**PADDED, "expect": {"length": 4}}))))
    keep("c-3", r6.formal(wrote(model()), TITLE, TEXT, Caller(rules(SAME))))
    keep("d-4", {"verdict": "none", "status": "no_model"})
    got = r6.summary(tmp_path, ["a-1", "b-2", "c-3", "d-4", "e-5"])
    assert got["bugs"] == [{"task": "a-1", "status": "stands", "disagreed": False, "masked": 0},
                           {"task": "b-2", "status": "ambiguous", "disagreed": True, "masked": 0},
                           {"task": "c-3", "status": "no_agreed_case", "disagreed": False, "masked": 0},
                           {"task": "d-4", "status": "no_model", "disagreed": None, "masked": None},
                           {"task": "e-5", "status": "no record", "disagreed": None, "masked": None}]
    assert got["by_status"] == {"ambiguous": 1, "no record": 1, "no_agreed_case": 1, "no_model": 1, "stands": 1}
    assert got["compared"] == 3 and got["disagreed"] == 1
    assert QUOTE not in json.dumps(got) and PROBE not in json.dumps(got)


def test_the_formal_model_is_counted_and_paired_with_the_others_which_are_unchanged():
    assert rc.REVIEWERS[:6] == ((rc.FROZEN, fr.FOLDER), (rc.REPRODUCE, fr.REPRODUCE), (rc.PRODUCT, rc.pr.FOLDER),
                                (rc.RULE, r3.FOLDER), (rc.SYMPTOM, r4.FOLDER), (rc.RELATION, r5.FOLDER))
    assert rc.REVIEWERS[6:] == ((rc.FORMAL, r6.FOLDER),) and rc.FORMAL == "formal model"
    assert rc.PAIRED == (rc.REPRODUCE, rc.RULE, rc.SYMPTOM, rc.RELATION, rc.FORMAL)
    assert len({folder for _, folder in rc.REVIEWERS}) == len(rc.REVIEWERS)


# --- the probe, in a process of its own that is never given an expectation ---------------------------------------------------
def _pytest(folder: Path, source: str) -> we.ProbeRun:
    """The compiled file run by real pytest in a process of its own, in ``folder``, as ``run_on`` runs it in a container."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "test_formal.py").write_text(source, "utf-8")
    (folder / "pytest.ini").write_text("[pytest]\n", "utf-8")
    report = folder / "report.xml"
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-m", "pytest", "test_formal.py", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
                           "--rootdir", str(folder), f"--junitxml={report}", "-o", "junit_family=xunit2"],
                          cwd=folder, env=env, capture_output=True, timeout=120)
    xml = report.read_text("utf-8") if report.is_file() else ""
    return we.ProbeRun(done.returncode, we.parse_junit(xml) if xml else {}, {}, done.stdout.decode("utf-8", "replace"))


def test_a_probe_that_reaches_for_the_expectations_finds_none_and_changes_none():
    """Review 2 finding 2: a probe run in the assertion's process reached its caller's frame and rewrote what each cell
    expects. The probe runs in a child process given the cases alone, so its frames hold no expectation."""
    tamper = ("import sys\nframe = sys._getframe()\nwhile frame is not None:\n"
              "    for cell in frame.f_globals.get('_G6_CELLS', []):\n        cell['want'] = '0'\n"
              "    frame = frame.f_back\n"
              "def observe(case):\n    return {'length': 0, 'size': 0, 'out': ''}\n")
    got = r6.formal(wrote(model(probe=tamper)), TITLE, TEXT, Caller(rules(PADDED)))
    space = _load(got["test"])
    for cell in space["_G6_CELLS"]:
        with pytest.raises(AssertionError):
            space["test_formal"](cell)                # length 0 where the tables agree on 3
    assert [cell["want"] for cell in space["_G6_CELLS"]] == [r6.canon(3)] * 2


def test_the_probe_runs_as_the_main_code_of_a_process_of_its_own():
    probe = (f"import os\nPARENT = {os.getpid()}\n"
             "def observe(case):\n"
             "    apart = __name__ == '__main__' and os.getpid() != PARENT\n"
             "    return {'length': 3 if apart else 0, 'size': 0, 'out': ''}\n")
    space = _load(r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))["test"])
    for cell in space["_G6_CELLS"]:
        space["test_formal"](cell)


def test_a_project_module_of_any_name_imports_as_it_does_for_any_test(tmp_path):
    """Review 2 finding 3: the probe was registered as ``formal_probe``, which hid a project module of that name. The probe
    imports on pytest's own path (the follow-up), so the file runs in real pytest in the project's folder, as ``run_on`` runs it
    there, where pytest's path has that folder."""
    (tmp_path / "formal_probe.py").write_text("def result():\n    return 3\n", "utf-8")
    probe = "from formal_probe import result\ndef observe(case):\n    return {'length': result(), 'size': 0, 'out': ''}\n"
    ran = _pytest(tmp_path, r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))["test"])
    assert list(ran.results.values()) == ["passed", "passed"], ran.tail


def test_a_probe_that_overruns_its_bound_is_killed_and_every_cell_skips():
    probe = "import time\ntime.sleep(5)\n" + PROBE
    space = _load(r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))["test"])
    space["_G6_SECONDS"] = 1
    for cell in space["_G6_CELLS"]:
        with pytest.raises(pytest.skip.Exception, match="past 1 seconds"):
            space["test_formal"](cell)


@pytest.mark.parametrize("probe", ["import os\nos._exit(0)\n", "import os\ndef observe(case):\n    os._exit(0)\n"])
def test_a_probe_process_that_gives_no_answer_skips_every_cell_and_fails_none(tmp_path, probe):
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))
    assert list(_pytest(tmp_path, got["test"]).results.values()) == ["skipped", "skipped"]


def test_what_the_probe_prints_spoils_no_answer(tmp_path):
    probe = ("import os, sys\nprint('{}')\nos.write(1, b'not json')\n"
             "def observe(case):\n    print('[]')\n    sys.stdout.flush()\n    os.write(1, b'{')\n"
             "    out = str(case['n']).rjust(case['width'])\n    return {'length': len(out), 'size': len(out), 'out': out}\n")
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, Caller(rules(PADDED)))
    assert list(_pytest(tmp_path, got["test"]).results.values()) == ["passed", "passed"]


# --- one exact value, from the readers to the probe --------------------------------------------------------------------------
@pytest.mark.parametrize(("first", "second", "says"), [
    (model().replace('"expect": {"length": 3}', '"expect": {"length": 4, "len\\u0067th": 3}'), rules(PADDED), "the model holds"),
    (model().replace('"when": {"width": 3}', '"when": {"width": 4, "width": 3}'), rules(PADDED), "the model holds"),
    (model().replace('{"factors"', '{"probe": "x", "factors"', 1), rules(PADDED), "the model holds"),
    (model(), rules(PADDED).replace('"expect": {"length": 3}', '"expect": {"length": 4, "length": 3}'), "reader 2"),
])
def test_an_object_that_names_one_key_twice_at_any_depth_makes_the_bug_invalid(first, second, says):
    """Review 2 finding 4: Python's reader keeps the last of two values under one name, so the first was never checked."""
    got = r6.formal(wrote(first), TITLE, TEXT, Caller(second))
    assert got["status"] == "invalid" and got["test"] == "" and says in got["status_reason"]
    assert "twice" in got["status_reason"]


@pytest.mark.parametrize(("first", "second", "says"), [
    (model(probe="# \ud800\n" + PROBE), rules(PADDED), "the model holds"),
    (model(factors={"n": [1, "\udfff"], "width": [3, 4]}), rules(PADDED), "the model holds"),
    (model(), rules({**PADDED, "quote": QUOTE + " \ud800"}), "reader 2"),
])
def test_a_string_that_is_no_unicode_text_makes_the_bug_invalid(first, second, says):
    """Review 2 finding 7: a lone surrogate is valid JSON and no UTF-8 text, so it aborted reader 2's request."""
    got = r6.formal(wrote(first), TITLE, TEXT, Caller(second))
    assert got["status"] == "invalid" and got["test"] == "" and says in got["status_reason"]
    assert "Unicode" in got["status_reason"]


@pytest.mark.parametrize("number", ["9007199254740993.0", "0.1000000000000000055511151231257827", "1.00000000000000001"])
def test_a_factor_number_no_float_holds_as_written_makes_the_bug_invalid(number):
    """Review 2 finding 1: the probe is given a float, so two values the tables tell apart were one input to it."""
    got = r6.formal(wrote(model(factors={"n": [1, "A"], "width": [3, 4]}).replace('"A"', number)), TITLE, TEXT,
                    Caller(rules(PADDED)))
    assert got["status"] == "invalid" and got["test"] == "" and "exactly" in got["status_reason"]
    nested = r6.formal(wrote(model(factors={"n": [1, ["A"]], "width": [3]}).replace('"A"', number)), TITLE, TEXT,
                       Caller(rules(PADDED)))
    assert nested["status"] == "invalid" and "exactly" in nested["status_reason"]


def test_a_factor_number_a_float_holds_as_written_is_given_to_the_probe_as_that_float():
    text = model(factors={"n": ["A", "B", "C", "D", 7], "width": [3]})
    for name, number in zip("ABCD", ("0.1", "2.5", "1E+2", "9007199254740992.0")):
        text = text.replace(f'"{name}"', number)
    got = r6.formal(wrote(text), TITLE, TEXT, Caller(rules(PADDED)))
    assert got["status"] == "stands"
    given = [cell["case"]["n"] for cell in _load(got["test"])["_G6_CELLS"]]
    assert given == [0.1, 2.5, 100.0, 9007199254740992.0, 7] and [type(n) for n in given] == [float] * 4 + [int]
    shown = json.loads(r6.reader_2_text(ISSUE, r6.parse_model(text)[0]).split("<factors>\n")[1].split("\n</factors>")[0])
    assert shown["n"] == given                        # reader 2 is shown the values the probe is given


def _agreed(*rows) -> dict:
    """Both readers giving these rows, each row's expectation of ``length`` written as the JSON text beside it."""
    text = rules(*({**row, "expect": {"length": f"W{n}"}} for n, (row, _) in enumerate(rows)))
    for n, (_, want) in enumerate(rows):
        text = text.replace(f'"W{n}"', want)
    return r6.formal(wrote(model(rules=[]).replace('"rules": []', text[1:-1])), TITLE, TEXT, Caller(text))


def test_an_agreed_expectation_no_float_holds_as_written_is_dropped_and_counted_and_never_compiled():
    """An agreed expected number that no float holds exactly can never equal what observe returns, so its cell would fail on
    the parent and on every patch alike, and the gate flag every patch: it is dropped after the comparison, and counted."""
    one = {"when": {"width": 3, "n": 1}, "quote": QUOTE}
    two = {"when": {"width": 3, "n": 22}, "quote": QUOTE}
    got = _agreed((one, "9007199254740993.0"), (two, "0.5"))
    assert got["status"] == "stands" and got["comparison"]["cells_agree"] == 2 and got["comparison"]["cells_unholdable"] == 1
    cells = _load(got["test"])["_G6_CELLS"]
    assert [(cell["case"]["n"], cell["want"]) for cell in cells] == [(22, r6.canon(0.5))]
    got = _agreed((one, "9007199254740993"), (two, "[9007199254740993.0]"))         # an int is exact; a list holds its numbers
    assert got["status"] == "stands" and got["comparison"]["cells_unholdable"] == 1
    assert [cell["want"] for cell in _load(got["test"])["_G6_CELLS"]] == [r6.canon(9007199254740993)]
    got = _agreed((one, "9007199254740993.0"))
    assert got["status"] == "no_agreed_case" and got["test"] == "" and got["comparison"]["cells_unholdable"] == 1
    assert "no float" in got["status_reason"]


def test_readers_who_differ_on_an_expectation_no_float_holds_stay_ambiguous():
    first = model(rules=[{**PADDED, "expect": {"length": "A"}}]).replace('"A"', "9007199254740992.0")
    second = rules({**PADDED, "expect": {"length": "B"}}).replace('"B"', "9007199254740993.0")
    got = r6.formal(wrote(first), TITLE, TEXT, Caller(second))
    assert got["status"] == "ambiguous" and got["test"] == "" and got["comparison"]["cells_differ"] == 2


def test_a_reader_2_whose_call_cannot_encode_or_decode_its_text_is_no_answer():
    got = r6.formal(wrote(model()), TITLE, TEXT, Caller(UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")))
    assert got["status"] == "no_answer" and "reader 2" in got["error"] and r3.no_answer(got)


# --- a reader's failure, kept for the counts ---------------------------------------------------------------------------------
def test_each_attempt_that_ends_in_no_answer_is_kept_apart_counted_and_asked_again(tmp_path):
    """Review 2 finding 5: a reader that failed left no record, so the counts called an attempted bug "no record"."""
    rows, task, asked = _rows(tmp_path, 1), we.task_by_id("black-5634"), []

    def step(*args, **kwargs):
        asked.append(1)
        return wrote(model()), None, None

    for _ in range(2):
        got = r6.review_bug(SimpleNamespace(), tmp_path, task, rows, TITLE, TEXT, step=step,
                            caller=Caller(LLMError("G6_E_LLM_HOST_FAILED: claude exited 1")), run=lambda *a: pytest.fail("ran"))
        assert [g["said"] for g in got] == ["failed"]
    assert len(asked) == 2 and fr.read(r3.bug_record(tmp_path, task.id, r6.FORMAL)) is None     # never read as an answer
    kept = [json.loads(path.read_text("utf-8")) for path in r6.no_answers(tmp_path, task.id)]
    assert len(kept) == 2 and all(one["status"] == "no_answer" and "reader 2" in one["status_reason"] for one in kept)
    assert all(path.parent == r3.bug_record(tmp_path, task.id, r6.FORMAL).parent for path in r6.no_answers(tmp_path, task.id))
    assert r6.summary(tmp_path, [task.id])["by_status"] == {"no_answer": 1}
    r6.review_bug(SimpleNamespace(), tmp_path, task, rows, TITLE, TEXT, step=step, caller=Caller(rules(PADDED)),
                  run=lambda *a: (FAILS, FAILS))
    assert len(asked) == 3 and r6.summary(tmp_path, [task.id])["by_status"] == {"stands": 1}


# --- a saved verdict, reused only with its identity ----------------------------------------------------------------------------
def _finished(root: Path, row: dict[str, str]) -> None:
    (root / "loop" / f"L9-{row['task']}-0.json").write_text(json.dumps({**row, "status": "finished"}), "utf-8")


def test_a_saved_verdict_is_reused_only_for_its_own_bug_under_its_own_gate(tmp_path):
    """Review 2 finding 8: replay 5's gate changed with its digest kept, so a verdict made without it was reused."""
    [row] = _rows(tmp_path, 1)
    _finished(tmp_path, row)
    sha = hashlib.sha256((tmp_path / "run0" / "final.patch").read_bytes()).hexdigest()
    for ins in (r3.rule(), r5.RELATION, r6.FORMAL):
        gate = {"same_case": True} if ins.same_case else {}
        for record, reused in (({}, not ins.same_case), ({"same_case": True}, ins.same_case), ({**gate, "task": "other-1"}, False)):
            fr.keep(tmp_path, row, {"said": "reproduced", "verdict": "defect", **record}, ins.folder, sha=sha)
            assert (r3.waiting(tmp_path, [row["task"]], ins) == []) == reused, (ins.folder, record)
            assert fr.waiting(tmp_path, [row["task"]], ins.folder) == []                # the frozen review's rule is as it was


def test_a_verdict_names_its_gate_where_one_is_on_and_replay_3s_and_4s_records_are_as_they_were(tmp_path):
    for ins, n in ((r3.rule(), 0), (r4.SYMPTOM, 1), (r6.FORMAL, 2)):
        root = tmp_path / str(n)
        root.mkdir()
        rows = _rows(root, 1)
        r3.review_bug(SimpleNamespace(), root, we.task_by_id("black-5634"), rows, "t", "x",
                      ask=lambda *a, **k: (wrote("def test_a():\n    assert 0\n"), None, None), run=lambda *a: (FAILS, FAILS), ins=ins)
        record = fr.saved(root, rows[0], ins.folder)
        assert ("same_case" in record) == ins.same_case and record.get("same_case", True) is True


def test_a_patch_whose_bytes_change_while_its_bug_is_asked_has_no_verdict_under_the_new_bytes(tmp_path):
    """Review 2 finding 10: a patch swapped for another bug's while its bug was asked had its verdict saved under that bug's
    digest. A verdict is saved only under the digest its patch had when its bug's review began."""
    rows = _rows(tmp_path, 1)
    path = tmp_path / "run0" / "final.patch"
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    def step(*args, **kwargs):
        path.write_text("another bug's patch\n", "utf-8")
        return wrote(model()), None, None

    got = r6.review_bug(SimpleNamespace(), tmp_path, we.task_by_id("black-5634"), rows, TITLE, TEXT, step=step,
                        caller=Caller(rules(PADDED)), run=lambda ctx, task, test, patch: (FAILS, FAILS))
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert not (tmp_path / "loop" / r6.FOLDER / f"{after}.json").exists()
    kept = json.loads((tmp_path / "loop" / r6.FOLDER / f"{before}.json").read_text("utf-8"))
    assert got[0]["said"] == kept["said"] == "failed" and "changed" in kept["error"] and not fr.counts(kept)


# --- review 3's findings, each removed by its class ----------------------------------------------------------------------------
def test_the_counts_take_a_saved_verdict_only_where_its_scheduler_reuses_it(tmp_path, monkeypatch):
    """Review 3 finding 2: a verdict the scheduler leaves waiting, made for another bug or under the other gate, was counted.
    The counts read each replay's saved verdict by the rule its scheduler reuses one by: ``replay3.reused`` for the replays on
    replay 3's machinery, ``final_review.counts`` for the others."""
    [row] = _rows(tmp_path, 1)
    row = {**row, "arm": "L9", "repeat": 0}
    _finished(tmp_path, row)
    sha = hashlib.sha256((tmp_path / "run0" / "final.patch").read_bytes()).hexdigest()
    monkeypatch.setattr(rc.compare, "saved", lambda root: {("L9", row["task"], 0): {"amended": True, "maintainers": True}})
    names = {folder: name for name, folder in rc.REVIEWERS}
    for ins in (r3.rule(), r4.SYMPTOM, r5.RELATION, r6.FORMAL):
        gate = {"same_case": True} if ins.same_case else {}
        for record in ({}, {"same_case": True}, {**gate, "task": "other-1"}):
            fr.keep(tmp_path, row, {"said": "none", "verdict": "none", **record}, ins.folder, sha=sha)
            waits = r3.waiting(tmp_path, [row["task"]], ins) != []
            assert waits == (record != gate), (ins.folder, record)
            rows = rc.patches(tmp_path, [row["task"]])
            assert rc.tally(rows, names[ins.folder], "amended")["n"] == (not waits), (ins.folder, record)
            assert (rows[sha][names[ins.folder]] is None) == waits
    for name, folder in rc.REVIEWERS[:3]:              # the final review's rule, which names no bug, is as it was
        fr.keep(tmp_path, row, {"said": "none", "verdict": "none", "task": "other-1"}, folder, sha=sha)
        assert fr.waiting(tmp_path, [row["task"]], folder) == []
        assert rc.tally(rc.patches(tmp_path, [row["task"]]), name, "amended")["n"] == 1


def test_reader_2_is_shown_the_probe_with_no_comment_or_docstring_and_the_file_runs_it_as_written():
    """Review 3 finding 3: reader 1's table reached reader 2 in a comment of the probe. Reader 2 is shown the probe with every
    comment, as Python's tokenizer reads one, and every docstring or other statement that is a string alone, as its parser
    reads one, removed; the compiled file runs the probe as reader 1 wrote it."""
    probe = ('"""R1_MODULE_DOC"""\nimport os\r# R1_AFTER_A_LONE_CR\r\n'
             'class Ünï:  "R1_CLASS_DOC é"\n'
             'def observe(case):  # R1_TRAILING\n'
             '    """R1_FUNCTION_DOC\n    over two lines"""\n'
             '    note = "# R1_KEPT_STRING"\n'
             '    out = str(case["n"]).rjust(case["width"])\n'
             '    ("R1_BARE_STRING"  # R1_INNER\n     "R1_MORE")\n'
             '    return {"length": len(out), "size": len(out), "out": out}\n')
    caller = Caller(rules(PADDED))
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, caller)
    [(_, user)] = caller.calls
    for text in ("R1_MODULE_DOC", "R1_AFTER_A_LONE_CR", "R1_CLASS_DOC", "R1_TRAILING", "R1_FUNCTION_DOC", "over two lines",
                 "R1_BARE_STRING", "R1_INNER", "R1_MORE"):
        assert text not in user, text
    assert 'note = "# R1_KEPT_STRING"' in user and 'out = str(case["n"]).rjust(case["width"])' in user and "class Ünï:" in user
    assert got["status"] == "stands" and repr(probe) in got["test"]           # the probe as reader 1 wrote it, as one text
    space = _load(got["test"])
    for cell in space["_G6_CELLS"]:
        space["test_formal"](cell)                    # and it runs as written: each agreed case is padded
    unparsed = Caller(rules(PADDED))                  # one the tokenizer reads and the parser cannot is shown nothing (follow-up)
    got = r6.formal(wrote(model(probe="# R1_COMMENT\ndef observe(case) return {}\n")), TITLE, TEXT, unparsed)
    assert got["status"] == "invalid" and "parser" in got["status_reason"] and unparsed.calls == []


@pytest.mark.parametrize("probe", ['def observe(case):\n    return """\n', "if 1:\n    x = 1\n  y = 2\n", "x = (1,\n", "x = 1\x00\n"])
def test_a_probe_the_tokenizer_cannot_read_asks_no_reader_2_and_makes_the_bug_invalid(probe):
    caller = Caller(rules(PADDED))
    got = r6.formal(wrote(model(probe=probe)), TITLE, TEXT, caller)
    assert got["status"] == "invalid" and got["test"] == "" and caller.calls == [] and "tokenizer" in got["status_reason"]


@pytest.mark.parametrize("ending", ["_exit = os._exit\nos._exit = lambda code: _exit(7)\n",
                                    "def _end(code):\n    raise SystemExit(7)\nos._exit = _end\n"])
def test_a_probe_process_that_exits_non_zero_skips_every_cell_whatever_it_answered(ending):
    """Review 3 finding 4: the probe's process wrote its answer and then exited non-zero, and the answer was read. An answer
    is read only from a process that exited 0."""
    space = _load(r6.formal(wrote(model(probe="import os\n" + ending + PROBE)), TITLE, TEXT, Caller(rules(PADDED)))["test"])
    for cell in space["_G6_CELLS"]:
        with pytest.raises(pytest.skip.Exception, match="exited 7"):
            space["test_formal"](cell)


@pytest.mark.parametrize(("value", "expected"), [("b'abc'", "b'abc'"), ("{3}", "{3}"), ("(3, 3)", [3, 3]), ("{1: 3}", {"1": 3}),
                                                  ("float('nan')", "nan"), ("[[1]] * 1 + [object()]", "x")])
def test_an_observation_that_is_no_json_value_makes_its_case_unobservable_and_its_cells_skip(value, expected):
    """Review 3 finding 5: the probe's process wrote a value that is no JSON value by its repr, so bytes whose repr is the
    expected string passed. Each case's observations are written as JSON strictly and must read back as they were; any other
    makes the case unobservable, and its cells skip."""
    row = {"when": {}, "expect": {"out": expected}, "quote": QUOTE}
    probe = f"def observe(case):\n    return {{'length': 3, 'size': 3, 'out': {value}}}\n"
    space = _load(r6.formal(wrote(model(probe=probe, rules=[row])), TITLE, TEXT, Caller(rules(row)))["test"])
    assert len(space["_G6_CELLS"]) == 4
    for cell in space["_G6_CELLS"]:
        with pytest.raises(pytest.skip.Exception, match="no JSON value"):
            space["test_formal"](cell)
