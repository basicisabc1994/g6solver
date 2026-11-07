# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The report reads what the issuer stated, never a rendering of it (G6 0.3, slice 2, repair 1; review 1's finding 9).

Why the issuer does not count a requirement reaches the report as the issuer's own codes
(``requirement_evidence.codes``: a caveat's receipts' results, its grounding rule's reasons, the records its ledger did
not hold), mapped to G6's words by one table (``patch_report.NOT_COUNTED``). The report never searches the issuer's gap
text, or a check's name, for a substring: the inventory lists every substring search in the report from the code, and
fails on one over issuer or check text.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from g6.evidence import certificate as issuer
from g6.harness import patch_report, requirement_evidence

NODE = "test_advisory_check_failed.py::test_ok"


def _evidence(node: str, *, written: bool, again: str = "passed", at_base: str = "failed") -> dict:
    evaluated = [{"id": "R1", "text": "new behaviour", "kind": "requirement", "provenance": "stated",
                  "checks": [{"id": node, "provenance": "stated", "verdict": "passed"}]}]
    evidence = requirement_evidence.assess(
        evaluated, [{"check": requirement_evidence.AT_BASE, "outcomes": {node: at_base}}], artifact="a" * 64,
        base_commit="0" * 40, check_sha={node: "c" * 64}, written=lambda _: written, environment={"python": "3.12"},
        again={"verdicts": {node: again}})
    return {"outcome": "insufficient_evidence", "requirements": evaluated, "requirement_evidence": evidence,
            "completion": {"demonstrated": [{"requirement": "R1", "checks": [node]}]}}


# --- review 1's finding, brought in as this change's own --------------------------------------------------------------


def test_finding_9_the_report_does_not_parse_a_check_name_as_an_advisory_failure():
    assessment = _evidence(NODE, written=True)
    statement = assessment["requirement_evidence"]["requirements"][0]
    assert not statement["checked"] and statement["caveats"] == []
    assert "self_authored" in statement["gap"]
    report = patch_report.render(assessment)
    assert "the failure is a caveat" not in report, (
        "the report invented a caveat by matching text inside the check's name", report)
    assert "not counted yet: the patch's author wrote the check" in report


def test_each_check_name_that_looks_like_an_issuer_code_changes_nothing_the_report_says():
    plain = patch_report.render(_evidence("tests/test_x.py::test_ok", written=True))
    for code, _ in patch_report.NOT_COUNTED:
        named = patch_report.render(_evidence(f"tests/test_{code}.py::test_{code}", written=True))
        assert named.count("not counted yet: the patch's author wrote the check") == plain.count(
            "not counted yet: the patch's author wrote the check"), code


def test_the_codes_are_the_issuer_s_from_its_structured_fields():
    caveat = _evidence("tests/test_x.py::test_ok", written=False, again="failed")
    found = caveat["requirement_evidence"]["requirements"][0]
    assert found["caveats"] and found["codes"][0] == requirement_evidence.ADVISORY
    own = _evidence("tests/test_x.py::test_ok", written=True)["requirement_evidence"]["requirements"][0]
    assert {"self_authored", "frozen_after_artifact"} <= set(own["codes"])
    counted = _evidence("tests/test_x.py::test_ok", written=False)["requirement_evidence"]["requirements"][0]
    assert counted["checked"] and counted["codes"] == []


def test_codes_given_as_text_are_never_searched():
    # malformed state: the codes as one text, as a gap is, say nothing; the report falls back to its plain words
    base = {"outcome": "insufficient_evidence", "reasons": [], "requirements": [],
            "completion": {"not_established": [{"requirement": "R1", "text": "t", "reason": "r", "settle_by": "s"}]}}
    statement = {"requirement": "R1", "kinds": {"c": "independent_executable"}, "checked": False, "support": False,
                 "refute": False, "gap": "advisory_check_failed:c", "codes": "advisory_check_failed"}
    report = patch_report.render({**base, "requirement_evidence": {"requirements": [statement]}})
    assert "the failure is a caveat" not in report and "no check G6 can count ran" in report


def test_every_code_the_adapter_can_give_has_one_row_in_the_report_s_table():
    keys = [key for key, _ in patch_report.NOT_COUNTED]
    assert len(keys) == len(set(keys))
    grounding = ast.parse(inspect.getsource(issuer.grounding))
    reasons: set[str] = set()   # each reason the rule appends, and each it names as a key of what it found
    for node in ast.walk(grounding):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append":
            reasons |= {arg.value for arg in node.args if isinstance(arg, ast.Constant)}
        elif isinstance(node, ast.Dict):
            reasons |= {key.value for key in node.keys if isinstance(key, ast.Constant)}
    assert {"self_authored", "no_negative_control", "control_not_killed"} <= reasons   # the scan reads the rule
    adapter = {requirement_evidence.ADVISORY, requirement_evidence.UNKNOWN, requirement_evidence.NOT_IN_LEDGER}
    assert sorted((reasons | adapter) - set(keys)) == []


# --- the inventory: every substring search in the report, from the code ----------------------------------------------

REPORT = Path(patch_report.__file__)
_SEARCHES = frozenset({"startswith", "endswith", "find", "rfind", "index", "rindex", "count", "partition",
                       "rpartition", "split", "rsplit", "replace", "search", "match", "fullmatch", "findall",
                       "finditer", "sub"})


def _literal(node: ast.AST, constants: frozenset[str]) -> bool:
    """A collection written out (a tuple, list, set or dict display, or a module constant holding one)."""
    return isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)) or (
        isinstance(node, ast.Name) and node.id in constants)


def searches(tree: ast.Module) -> set[tuple[str, str]]:
    """(function, source) of every ``in`` test against something that is not a collection written out, and of every
    call of a text method or ``re`` function that looks inside text."""
    constants = frozenset(target.id for node in tree.body if isinstance(node, ast.Assign)
                          and isinstance(node.value, (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Call))
                          for target in node.targets if isinstance(target, ast.Name))
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Compare):
                for op, right in zip(child.ops, child.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and not _literal(right, constants):
                        found.add((where, ast.unparse(child)))
            elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                  and child.func.attr in _SEARCHES):
                found.add((where, ast.unparse(child.func)))
            visit(child, where)

    visit(tree, "<module>")
    return found


OWN = "the report's own text"
MEMBERSHIP = "membership in a collection of values, never a search inside text"
#: Every search the report makes, and what it searches: none is over the issuer's text or a check's.
PLACES: dict[tuple[str, str], tuple[str, str]] = {
    ("_issuer", "key in held"): ("membership", "the frozenset of the issuer's codes, one table row's code at a time"),
    ("_words", "text.replace"): ("search", "G6's own wording of a run or a skip, its tier names put plainly"),
    ("code", "re.findall"): ("search", "runs of backticks in the value, to fence it; nothing is read for meaning"),
    ("code", "str(value).split"): ("search", "whitespace, to put the value on one line; nothing is read for meaning"),
    ("_findings", "str(reproduction.get('at_base', 'not run')).startswith"): (
        "search", "G6's own wording of a proposed check's run at the base commit (verification_search)"),
    ("_decide", "line.startswith"): ("search", f"{OWN}: the 'and N more' line its own cut wrote"),
    ("_steps", "'not run' in str(s.get('reason', ''))"): (
        "search", "G6's own reason for a skip (the review's checks_skipped), never the issuer's or a check's"),
    ("_steps", "str(r.get('stage', '')).startswith"): ("search", "the stage G6 gave one of the review's own reasons"),
    ("_steps", "outcome.replace"): ("search", "the review's own outcome code, its underscores put as spaces"),
    ("_latest", "file in run['results']"): ("membership", MEMBERSHIP),
    # G6 0.3, slice 3: which of example.v1's outcome fields a confirmed question's outcome states (a key of a mapping)
    ("_outcome_words", "'raises' in outcome"): ("membership", "a key of the outcome a question G6 made states"),
    ("_outcome_words", "'effect' in outcome"): ("membership", "a key of the outcome a question G6 made states"),
}


def test_every_search_in_the_report_is_listed_and_none_is_over_issuer_or_check_text():
    found = searches(ast.parse(REPORT.read_text(encoding="utf-8")))
    assert sorted(found - set(PLACES)) == [], "unlisted searches: read the issuer's structured fields instead"
    assert sorted(set(PLACES) - found) == [], "listed searches the scan no longer finds: remove them"
    assert all(why.strip() for _, why in PLACES.values())


def test_the_issuer_s_reading_has_no_search_and_never_reads_its_gap():
    tree = ast.parse(REPORT.read_text(encoding="utf-8"))
    body = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_issuer")
    assert {source for where, source in searches(tree) if where == "_issuer"} == {"key in held"}
    assert not [node for node in ast.walk(body) if isinstance(node, ast.Constant) and node.value == "gap"]


def test_the_scan_finds_a_planted_search_over_the_gap():
    # survival control: the search finding 9 reproduced is found, and is not allowed
    planted = ast.parse("def _issuer(found):\n    gap = str(found.get('gap'))\n"
                        "    return [why for key, why in NOT_COUNTED if key in gap]\n")
    assert searches(planted) == {("_issuer", "key in gap")}
    assert ("_issuer", "key in gap") not in PLACES
