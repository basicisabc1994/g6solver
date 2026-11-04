# SPDX-License-Identifier: Apache-2.0
"""Replay 6 (PLAN.md, 2026-11-04, evening): a model of the issue's rule as data, a domain of named factors, a probe and a rule
table from reader 1 and a second table from reader 2, checked by G6 in plain code on every case, compiled to one pytest file and
run on replay 3's machinery. Fakes stand in for the container, Codex and Claude: nothing here runs a model or a container."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
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
        "json", "sys", "types", "Decimal", "pytest"}            # G6's own; the probe's are in its text
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
    got, _ = r6.parse_model(text)
    assert len(r6.cases(got["factors"])) == 2                    # two values, read exactly


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
    assert got["status"] == "stands"
    space, held = _load(got["test"]), {}
    for cell in space["_G6_CELLS"]:
        try:
            space["test_formal"](cell)
            held[cell["case"]["kind"], cell["observation"]] = True
        except AssertionError:
            held[cell["case"]["kind"], cell["observation"]] = False
    assert held == {("float", "length"): True, ("bool", "length"): False, ("big", "length"): False,
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
                                   PROBE + "import pytest\npytest.fail('as it runs')\n", "def observe(case) return {}\n"])
def test_a_probe_that_raises_as_it_runs_skips_every_cell_and_fails_none(probe):
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
    assert got["bugs"] == [{"task": "a-1", "status": "stands", "disagreed": False},
                           {"task": "b-2", "status": "ambiguous", "disagreed": True},
                           {"task": "c-3", "status": "no_agreed_case", "disagreed": False},
                           {"task": "d-4", "status": "no_model", "disagreed": None},
                           {"task": "e-5", "status": "no record", "disagreed": None}]
    assert got["by_status"] == {"ambiguous": 1, "no record": 1, "no_agreed_case": 1, "no_model": 1, "stands": 1}
    assert got["compared"] == 3 and got["disagreed"] == 1
    assert QUOTE not in json.dumps(got) and PROBE not in json.dumps(got)


def test_the_formal_model_is_counted_and_paired_with_the_others_which_are_unchanged():
    assert rc.REVIEWERS[:6] == ((rc.FROZEN, fr.FOLDER), (rc.REPRODUCE, fr.REPRODUCE), (rc.PRODUCT, rc.pr.FOLDER),
                                (rc.RULE, r3.FOLDER), (rc.SYMPTOM, r4.FOLDER), (rc.RELATION, r5.FOLDER))
    assert rc.REVIEWERS[6:] == ((rc.FORMAL, r6.FOLDER),) and rc.FORMAL == "formal model"
    assert rc.PAIRED == (rc.REPRODUCE, rc.RULE, rc.SYMPTOM, rc.RELATION, rc.FORMAL)
    assert len({folder for _, folder in rc.REVIEWERS}) == len(rc.REVIEWERS)
