# SPDX-License-Identifier: Apache-2.0
"""The loop's domain-neutral seam: what a domain gives it, and what it refuses."""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import itertools
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import g6
from g6.core.llm import EmptyRetry, FakeLLM, FallbackCaller, TimingLLMCaller
from g6.core.vault import AccessDenied
from g6.metaharness import Model, Suite, SuiteCase
from g6.metaharness.domain import (
    FALLBACK_MODEL,
    FEWEST,
    NO_JUDGE,
    NO_SEALED,
    PAID_MODEL,
    SEALED_OVERLAP,
    Domain,
    FailureRecord,
    Judge,
    _point,
    backends,
    cut_pool,
    domain_problems,
    reason_coded,
    sealed_closed,
)
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.monitor import MIN_PAIRS
from g6.metaharness.propose import DevFailure, ProposerView
from g6.metaharness.recovery import partition
from g6.self_maintenance.pacing import ProviderWindow

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


def _two_backends(domain) -> FallbackCaller:
    return FallbackCaller((domain.llm, FakeLLM()))


@pytest.mark.parametrize("wrap", [lambda caller: caller, EmptyRetry, lambda caller: TimingLLMCaller(caller, print),
                                  lambda caller: EmptyRetry(FallbackCaller((caller,)))])
def test_a_model_with_a_fallback_is_refused_since_a_comparison_would_mix_backends(tmp_path, wrap):
    domain = domains.WordProblems(tmp_path)
    free = domain.execution()
    domain.execution = lambda: dataclasses.replace(free, models={"local": Model(wrap(_two_backends(domain)))})
    assert FALLBACK_MODEL in _codes(domain)


@pytest.mark.parametrize("wrap", [lambda caller: caller, EmptyRetry, lambda caller: FallbackCaller((caller,))])
def test_one_backend_behind_a_wrapper_is_one_backend(tmp_path, wrap):
    domain = domains.WordProblems(tmp_path)
    assert backends(wrap(domain.llm)) == 1


def test_a_loop_with_a_fallback_model_or_proposer_enrols_nothing_and_runs_nothing(tmp_path):
    domain = domains.WordProblems(tmp_path / "a")
    free = domain.execution()
    domain.execution = lambda: dataclasses.replace(free, models={"local": Model(_two_backends(domain))})
    settings = LoopSettings(windows=(ProviderWindow("claude", 1000, datetime(2026, 10, 26, tzinfo=UTC)),))
    loop = ShadowLoop(domain, tmp_path / "a" / "loop", settings, proposer=domains.proposes({"prompt": domains.WORKING}))
    got = loop.enrol("Ada")
    assert got["warning_card"]["code"] == FALLBACK_MODEL and got["completion_state"] == "blocked-escalated"
    assert loop.tick()["warning_card"]["code"] == FALLBACK_MODEL and domain.llm.calls == []
    plain = domains.WordProblems(tmp_path / "b")
    proposer = ShadowLoop(plain, tmp_path / "b" / "loop", settings, proposer=_two_backends(plain))
    assert proposer.enrol("Ada")["warning_card"]["code"] == FALLBACK_MODEL


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


def _sizes(cut) -> tuple[int, ...]:
    return len(cut.tune), len(cut.selection), len(cut.held_out), len(cut.monitor)


def _plain_pool(count: int) -> tuple[SuiteCase, ...]:
    return tuple(SuiteCase(f"c{i}", f"input {i}", "x") for i in range(count))


def _grouped_pool(count: int) -> tuple[SuiteCase, ...]:
    """Groups of one to five cases, as a bug's patches are, adding up to ``count``."""
    rng, cases = random.Random(7), []
    while len(cases) < count:
        group = f"g{len(cases)}"
        cases += [SuiteCase(f"{group}-{i}", f"{group} input {i}", "x", meta={"group": group})
                  for i in range(min(rng.randint(1, 5), count - len(cases)))]
    return tuple(cases)


@pytest.mark.parametrize("seed", range(200))
def test_a_pool_of_86_meets_every_minimum_whatever_the_seed(seed):
    tune, selection, held_out, monitor = _sizes(cut_pool(_plain_pool(86), seed))
    assert held_out >= 30 and monitor >= MIN_PAIRS and tune >= 1 and selection >= 1


def _by_hash(case: SuiteCase, seed: int) -> str:
    """The side a case's own hash gives it, whatever else is in the pool."""
    point = int.from_bytes(hashlib.sha256(f"{seed}:{case.case_id}".encode()).digest()[:8], "big") / 2 ** 64
    return next(name for name, end in (("tune", 0.25), ("selection", 0.40), ("held_out", 0.75), ("monitor", 2)) if point < end)


def test_a_small_pool_short_of_a_minimum_is_cut_by_counts_along_the_same_seeded_order():
    cases = _plain_pool(86)
    short = next(seed for seed in range(50) if sum(_by_hash(c, seed) == "held_out" for c in cases) < 30)
    assert _sizes(cut_pool(cases, short)) == (22, 12, 30, 22)


def test_a_pool_that_meets_its_minimums_keeps_the_cut_it_always_had():
    """A case keeps its side whatever joins the pool, and re-enrolling with a fresh split stands on that."""
    cases, names = _plain_pool(240), ("tune", "selection", "held_out", "monitor")
    cut = cut_pool(cases, 3)
    assert {name: [c.case_id for c in getattr(cut, name)] for name in names} == {
        name: [c.case_id for c in cases if _by_hash(c, 3) == name] for name in names}


@pytest.mark.parametrize("seed", range(60))
def test_a_group_stays_on_one_side_of_a_small_pool_and_it_still_meets_its_minimum(seed):
    cases = _grouped_pool(86)
    cut = cut_pool(cases, seed)
    sides = [{c.meta["group"] for c in part} for part in (cut.tune, cut.selection, cut.held_out, cut.monitor)]
    assert sum(len(side) for side in sides) == len(set().union(*sides)) == len({c.meta["group"] for c in cases})
    assert sum(_sizes(cut)) == 86 and len(cut.held_out) >= 30


@pytest.mark.parametrize("make", [_plain_pool, _grouped_pool])
def test_the_cut_of_a_small_pool_is_deterministic_and_the_pools_order_does_not_move_a_case(make):
    cases = make(86)
    shuffled = random.Random(1).sample(cases, len(cases))
    for seed in range(40):
        cut, again = cut_pool(cases, seed), cut_pool(shuffled, seed)
        assert cut == cut_pool(cases, seed)
        for name in ("tune", "selection", "held_out", "monitor"):
            assert {c.case_id for c in getattr(cut, name)} == {c.case_id for c in getattr(again, name)}


def test_a_pool_too_small_to_share_out_is_still_cut_and_is_refused_by_the_loop_not_here():
    assert sum(_sizes(cut_pool(_plain_pool(12), 1))) == 12 and cut_pool((), 1).held_out == ()


def _groups_of(sizes, seed: int) -> tuple[SuiteCase, ...]:
    """Groups of these sizes, in the order ``seed`` walks them."""
    names = sorted((f"g{i}" for i in range(len(sizes))), key=lambda name: _point(seed, name))
    return tuple(SuiteCase(f"{name}-{i}", f"{name} input {i}", "x", meta={"group": name})
                 for name, size in zip(names, sizes) for i in range(size))


def test_a_large_group_does_not_empty_a_side_that_intact_groups_can_fill():
    assert _sizes(cut_pool(_groups_of((25, 30, 10, 21), 7), 7)) == (25, 10, 30, 21)


@pytest.mark.parametrize("seed", range(150))
def test_a_grouped_pool_meets_every_minimum_whenever_intact_groups_can(seed):
    rng = random.Random(seed)
    sizes = [rng.choice((1, 2, 3, 5, 8, 12, 20, 30, 40)) for _ in range(rng.randint(3, 7))]
    cut = cut_pool(_groups_of(sizes, seed), seed)
    least = tuple(FEWEST.values())
    possible = any(all(sum(size for size, side in zip(sizes, sides) if side == place) >= least[place] for place in range(4))
                   for sides in itertools.product(range(4), repeat=len(sizes)))
    assert all(found >= most for found, most in zip(_sizes(cut), least)) == possible, sizes


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
    assert not [i for i in _imports(SRC / name) if any(i == c or i.startswith(c + ".") for c in CODING_IMPORTS)]


@pytest.mark.parametrize("name", LOOP_FILES)
def test_no_file_of_the_loop_can_reach_outward(name):
    outward = ("subprocess", "socket", "urllib", "http", "requests", "httpx", "git", "ssl", "ftplib", "smtplib")
    assert not [i for i in _imports(SRC / name) if any(i == o or i.startswith(o + ".") for o in outward)]


def test_the_loops_files_are_the_ones_these_checks_cover():
    assert all((SRC / name).is_file() for name in LOOP_FILES)


@pytest.mark.parametrize("name", LOOP_FILES)
def test_no_file_of_the_loop_assumes_coding_in_its_code(name):
    path = SRC / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not [n for n in names if any(word.replace(" ", "_") in n.lower() for word in CODING_WORDS)]
