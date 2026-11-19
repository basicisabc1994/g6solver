# SPDX-License-Identifier: Apache-2.0
"""Decision seams are synthetic; the confined cases exercise the real runner."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.distill.detectors import run_template
from g6.distill.facts import extract_facts
from g6.workforce import mutation_check, repo_tests

FIXED = "def answer(x):\n    return x + 1\n"
BASE = "def answer(x):\n    return x - 1\n"
BEFORE = {"tests/test_answer.py": "from subject import answer\ndef test_answer():\n    assert abs(answer(1) - 2) <= 0\n"}
WEAK = {"tests/test_answer.py": BEFORE["tests/test_answer.py"].replace("<= 0", "<= 3")}
HELPER = {"tests/test_answer.py": "from subject import answer\ndef check(value):\n    assert abs(value - 2) <= 0\ndef test_answer():\n    check(answer(1))\n"}
BINDINGS = {"limit": 2, "lines": "changed_by_patch", "min_settled": 1, "reruns": 2}
BUDGET = {"wall_s": 180, "probe_runs": 20, "model_calls": 0}


def invoke(tmp_path, after=WEAK, *, template="kill_set.v2", bindings=None, budget=None, **extra):
    return run_template(template, BEFORE, after, split="training", bindings=bindings or BINDINGS,
                        probe_input={"files": {"subject.py": FIXED}, "target": "subject.py",
                                     "changed_lines": [2], **extra}, workspace=tmp_path,
                        cost_bound=budget or BUDGET)


@pytest.fixture
def runner(monkeypatch):
    calls, overrides = [], {}
    sample = mutation_check.mutants(FIXED, limit=2, lines=[2])
    def run(repo, tests, **kwargs):
        from g6.core import confine
        assert confine.owned(repo)
        assert kwargs["require_isolation"] is True
        assert kwargs["copied"] is True
        assert kwargs["network"] is False
        source = (repo / "subject.py").read_text(encoding="utf-8")
        suite = {path: (repo / path).read_text(encoding="utf-8") for path in tests}
        calls.append((source, suite, kwargs))
        default = "passed" if source == FIXED or suite == WEAK else "failed"
        return {"result": overrides.get((source, digest(suite)), default),
                "isolation": {"backend": "synthetic-test-double", "network": False}}
    monkeypatch.setattr(repo_tests, "_run", run)
    monkeypatch.setattr(repo_tests, "established", lambda raw, **kw: SimpleNamespace(of=lambda: raw["result"]))
    return calls, overrides, sample


def test_weakened_tolerance_loses_a_kill(tmp_path, runner):
    result = invoke(tmp_path)
    assert result.verdict == "concern"
    assert result.outcome.lost_kills
    assert result.outcome.provenance.generator == "g6.workforce.mutation_check.mutants"
    assert len(runner[0]) == result.outcome.runs


def test_extract_helper_has_no_lost_kill(tmp_path, runner):
    result = invoke(tmp_path, HELPER)
    assert result.verdict == "no_concern"
    assert not result.outcome.lost_kills


def test_failing_fixed_suite_abstains(tmp_path, runner):
    runner[1][FIXED, digest(BEFORE)] = "failed"
    result = invoke(tmp_path)
    assert result.verdict == "abstain"
    assert result.outcome.reason == "fixed_suite_did_not_pass"


def test_refused_without_isolation(tmp_path, runner, monkeypatch):
    monkeypatch.setattr(repo_tests, "_run", lambda *a, **kw: {"isolation": {}})
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(tmp_path)
    assert error.value.code == ISOLATION_UNAVAILABLE


def test_run_cost_bound_refuses_before_running(tmp_path, runner):
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(tmp_path, budget={**BUDGET, "probe_runs": 1})
    assert error.value.code == "G6_E_PROBE_BUDGET"
    assert not runner[0]


def test_probe_outcomes_bind_to_fact_sources(tmp_path, runner):
    result = invoke(tmp_path)
    facts = extract_facts(BEFORE, WEAK, split="training", probe_outcomes={"kill_set.v2": result.outcome})
    row = facts["probe_outcomes"]["kill_set.v2"]
    assert row["outcome_id"] == result.outcome.outcome_id
    assert row["provenance"]["before_sha256"] == digest(BEFORE)
    assert row["provenance"]["after_sha256"] == digest(WEAK)
    with pytest.raises(ValueError, match="source"):
        extract_facts(BEFORE, HELPER, split="dev", probe_outcomes={"kill_set.v2": result.outcome})
    row["verdict"] = "no_concern"
    with pytest.raises(ValueError):
        extract_facts(BEFORE, WEAK, split="dev", probe_outcomes={"kill_set.v2": row})


@pytest.mark.parametrize("patch", [{"limit": 0}, {"limit": True}, {"min_settled": 3},
                                   {"lines": "all"}, {"reruns": 0}, {"source": "print(1)"}])
def test_bindings_are_bounded_data(tmp_path, runner, patch):
    with pytest.raises(ValueError):
        invoke(tmp_path, bindings={**BINDINGS, **patch})
    assert not runner[0]


@pytest.mark.parametrize("status", ["unknown", "skipped", "crashed"])
def test_unsettled_mutant_never_reports_clean(tmp_path, runner, status):
    runner[1][runner[2][0].source, digest(BEFORE)] = status
    assert invoke(tmp_path).verdict == "abstain"


def test_flaky_lost_kill_abstains(tmp_path, runner, monkeypatch):
    original = repo_tests._run
    def run(*a, **kw):
        raw = original(*a, **kw)
        if len(runner[0]) > 2 + 2 * len(runner[2]):
            raw["result"] = "passed"
        return raw
    monkeypatch.setattr(repo_tests, "_run", run)
    assert invoke(tmp_path).verdict == "abstain"


def test_wall_overrun_refuses_even_after_last_run(tmp_path, runner, monkeypatch):
    from g6.distill import probes
    now = [probes.time.monotonic()]
    original = repo_tests._run
    def run(*a, **kw):
        raw = original(*a, **kw)
        now[0] += 100
        return raw
    monkeypatch.setattr(probes.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(repo_tests, "_run", run)
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(tmp_path)
    assert error.value.code == "G6_E_PROBE_BUDGET"


@pytest.mark.parametrize("base_status, expected", [("passed", "concern"), ("failed", "no_concern"),
                                                  ("unknown", "abstain")])
def test_discriminates_at_base_uses_only_explicit_new_tests(tmp_path, runner, base_status, expected):
    runner[1][BASE, digest(WEAK)] = base_status
    result = invoke(tmp_path, template="discriminates_at_base.v2", bindings={"reruns": 2},
                    base_implementation=BASE, new_tests=WEAK)
    assert result.verdict == expected


def test_discriminates_empty_new_tests_abstains(tmp_path, runner):
    result = invoke(tmp_path, template="discriminates_at_base.v2", bindings={"reruns": 2},
                    base_implementation=BASE, new_tests={})
    assert result.verdict == "abstain"
    assert not runner[0]


@pytest.mark.parametrize("after, verdict", [(WEAK, "concern"), (HELPER, "no_concern")])
def test_real_confined_tolerance_and_helper(tmp_path, after, verdict):
    result = run_template("kill_set.v2", BEFORE, after, split="dev", bindings=BINDINGS,
                          probe_input={"files": {"subject.py": FIXED}, "target": "subject.py", "changed_lines": [2]},
                          workspace=tmp_path, cost_bound=BUDGET)
    assert result.verdict == verdict


def test_held_out_split_rejected_before_input_access():
    with pytest.raises(ValueError, match="split"):
        run_template("kill_set.v2", {}, {}, split="held_out", bindings=BINDINGS)


def test_v2_templates_have_registered_binding_schemas():
    from g6.distill.detectors import PROBE_TEMPLATES
    assert "kill_set.v2" in PROBE_TEMPLATES
    assert "discriminates_at_base.v2" in PROBE_TEMPLATES
    from g6.distill.probes import ProbeTemplateInstance
    instance = ProbeTemplateInstance(template="kill_set.v2", bindings=BINDINGS)
    assert instance.bindings.limit == 2
    with pytest.raises(ValueError):
        ProbeTemplateInstance(template="kill_set.v2", bindings={**BINDINGS, "limit": True})


def test_budget_refusal_needs_no_staging(monkeypatch):
    monkeypatch.setattr(repo_tests, "_run", lambda *a, **kw: pytest.fail("over-budget probe ran"))
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(Path(".build"), budget={**BUDGET, "probe_runs": 1})
    assert error.value.code == "G6_E_PROBE_BUDGET"
