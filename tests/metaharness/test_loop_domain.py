# SPDX-License-Identifier: Apache-2.0
"""The loop's domain-neutral seam: what a domain gives it, and what it refuses."""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

import g6
from g6.core.vault import AccessDenied
from g6.metaharness import Model, Suite, SuiteCase
from g6.metaharness.domain import (
    NO_JUDGE,
    NO_SEALED,
    PAID_MODEL,
    SEALED_OVERLAP,
    Domain,
    FailureRecord,
    Judge,
    cut_pool,
    domain_problems,
    reason_coded,
    sealed_closed,
)
from g6.metaharness.propose import DevFailure, ProposerView
from g6.metaharness.recovery import partition

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)


def _codes(domain) -> set[str]:
    return {code for code, _ in domain_problems(domain)}


def _view(*failures: DevFailure) -> ProposerView:
    return ProposerView("w", "{input}", (), ("answer",), ("local",), (), {}, (), failures, 0)


@pytest.mark.parametrize("make", [domains.WordProblems, domains.BugFixes])
def test_both_adapters_are_domains_and_neither_has_a_problem(tmp_path, make):
    domain = make(tmp_path)
    assert isinstance(domain, Domain)
    assert domain_problems(domain) == []


def test_a_domain_with_no_judge_is_refused_because_self_grading_is_not_improvement(tmp_path):
    problems = domain_problems(domains.WordProblems(tmp_path, judged=False))
    assert [code for code, _ in problems] == [NO_JUDGE]
    assert "self-grading is not improvement" in problems[0][1]


def test_a_judge_whose_answers_a_model_drafted_or_the_caller_wrote_is_refused(tmp_path):
    domain = domains.WordProblems(tmp_path)
    for author in ("model-drafted", "caller", "nobody"):
        domain.judge = lambda who=author: Judge(domains.GSM.oracle, answers_by=who)
        assert NO_JUDGE in _codes(domain)


def always_yes(output: str, expected: object) -> bool:
    return True


def test_a_judge_that_passes_a_non_answer_is_no_judge(tmp_path):
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(always_yes, answers_by="independent")
    assert NO_JUDGE in _codes(domain)


def test_a_judge_with_no_source_file_cannot_be_pinned(tmp_path):
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(max, answers_by="independent")
    assert NO_JUDGE in _codes(domain)


def test_a_domain_with_no_sealed_split_is_refused(tmp_path):
    assert _codes(domains.WordProblems(tmp_path, sealed=False)) == {NO_SEALED}


def test_a_development_case_that_is_also_sealed_is_refused(tmp_path):
    domain = domains.WordProblems(tmp_path)
    domain.pool.append(domain.sealed_cases[0])
    assert SEALED_OVERLAP in _codes(domain)


def test_a_paid_model_is_refused_because_the_loop_never_spends_api_credits(tmp_path):
    domain = domains.WordProblems(tmp_path)
    free = domain.execution()
    domain.execution = lambda: dataclasses.replace(free, models={"local": Model(domain.llm, paid=True)})
    assert PAID_MODEL in _codes(domain)


def test_the_cut_is_seeded_disjoint_and_complete():
    cases = tuple(SuiteCase(f"c{i}", f"input {i}", "x") for i in range(400))
    cut = cut_pool(cases, seed=1)
    parts = [cut.tune, cut.selection, cut.held_out, cut.monitor]
    assert sum(len(p) for p in parts) == 400 and all(parts)
    assert len({c.case_id for p in parts for c in p}) == 400
    assert cut_pool(cases, seed=1) == cut and cut_pool(cases, seed=2) != cut


def test_a_case_keeps_its_side_when_the_pool_grows():
    cases = tuple(SuiteCase(f"c{i}", f"input {i}", "x") for i in range(200))
    before, after = cut_pool(cases, seed=1), cut_pool((*cases, SuiteCase("new", "new", "x")), seed=1)
    assert set(c.case_id for c in before.held_out) <= set(c.case_id for c in after.held_out)


def test_a_failure_record_has_one_cause_per_case_read_from_reason_codes():
    failures = (DevFailure("wrong", "i", "o", "e", (), "fail", ()),
                DevFailure("rejected", "i", "o", "e", ("number",), "unknown", (), code="G6_E_CHECK"),
                DevFailure("held", "i", "o", "e", (), "unknown", (), code="G6_E_BUDGET"),
                DevFailure("intake", "i", "o", "e", (), "unknown", (), code="G6_E_PIPELINE_RISKY"),
                DevFailure("silent", "i", "o", "e", (), "unknown", ()))
    records = {r.case_id: r for r in reason_coded(_view(*failures))}
    assert {k: r.cause for k, r in records.items()} == {
        "wrong": "wrong_answer", "rejected": "check_rejected", "held": "harness_held",
        "intake": "intake_blocked", "silent": "oracle_silent"}
    assert all(r.independently_observed for r in records.values())
    assert {k for k, r in records.items() if not r.changeable} == {"intake", "silent"}   # only a person can
    assert sum(len(v) for v in partition(failures).values()) == len(failures)


def test_an_undeclared_failure_record_is_not_independently_observed():
    assert FailureRecord("c", "wrong_answer").independently_observed is False


def test_what_a_domain_gives_the_suite_is_only_what_the_metaharness_accepts(tmp_path):
    domain = domains.WordProblems(tmp_path)
    cut = cut_pool(domain.development(), seed=1)
    suite = Suite(dev=cut.tune, selection=cut.selection, held_out=cut.held_out,
                  provenance={"dev": "caller", "held_out": "independent"})
    assert suite.problems() == []


# The sealed split ---------------------------------------------------------------------------------------------------

def test_while_developing_nothing_in_this_process_can_open_a_sealed_file(tmp_path):
    domain = domains.WordProblems(tmp_path)
    vault = domain.sealed().vault
    assert domain.sealed_file.read_text(encoding="utf-8")                 # survival: it is readable before ...
    with sealed_closed(vault) as breaches:
        with pytest.raises(AccessDenied):
            domain.sealed_file.read_text(encoding="utf-8")
        with pytest.raises(AccessDenied):
            open(domain.sealed_file, "rb")
        assert (tmp_path / "ordinary.txt").write_text("ok", encoding="utf-8")  # survival: other files still open
    assert len(breaches) == 2
    assert domain.sealed_file.read_text(encoding="utf-8")                 # ... and after: the guard is scoped


def test_the_vault_still_checks_its_digest_outside_the_guard(tmp_path):
    domain = domains.WordProblems(tmp_path)
    vault = domain.sealed().vault
    assert vault.verify_unchanged() == ()
    domain.sealed_file.write_text("changed", encoding="utf-8")
    assert vault.verify_unchanged()


# Nothing in the loop may import or assume coding ----------------------------------------------------------------------
SRC = Path(g6.__file__).parent
LOOP_FILES = ("metaharness/domain.py", "metaharness/shadow.py", "metaharness/loop.py", "self_maintenance/pacing.py",
              "self_maintenance/envelope.py")
CODING_IMPORTS = ("g6.metaharness.coding", "g6.code", "g6.coding_agent_gate", "g6.workforce", "g6.agents",
                  "g6.harness.review", "g6.self_maintenance.controller")
CODING_WORDS = ("patch", "pytest", "repository", "pull request", "commit", "source file", "docker")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found |= {node.module} | {f"{node.module}.{a.name}" for a in node.names}
    return found


@pytest.mark.parametrize("name", LOOP_FILES)
def test_no_file_of_the_loop_imports_coding(name):
    path = SRC / name
    if not path.is_file():
        pytest.skip("written in a later commit")
    assert not [i for i in _imports(path) if any(i == c or i.startswith(c + ".") for c in CODING_IMPORTS)]


@pytest.mark.parametrize("name", LOOP_FILES)
def test_no_file_of_the_loop_assumes_coding_in_its_code(name):
    path = SRC / name
    if not path.is_file():
        pytest.skip("written in a later commit")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not [n for n in names if any(word.replace(" ", "_") in n.lower() for word in CODING_WORDS)]
