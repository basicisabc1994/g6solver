# SPDX-License-Identifier: Apache-2.0
"""Policy checks with recorded runner observations; no subprocess or staging."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.distill import probes
from g6.distill.facts import extract_facts
from g6.workforce import repo_tests
from tests.distill.test_probes import BASE, BEFORE, BINDINGS, BUDGET, FIXED, HELPER, WEAK, invoke


@pytest.fixture
def recorded(monkeypatch):
    calls, overrides = [], {}
    def staged(inputs, implementation, suite, **kwargs):
        calls.append((implementation, digest(suite), kwargs))
        status = overrides.get((implementation, digest(suite)),
                               "passed" if implementation == FIXED or suite == WEAK else "failed")
        return {"result": status, "isolation": {"backend": "recorded-policy-double", "network": False}}
    monkeypatch.setattr(probes, "_stage_run", staged)
    monkeypatch.setattr(repo_tests, "established", lambda raw, **kw: SimpleNamespace(of=lambda: raw["result"]))
    return calls, overrides


@pytest.mark.parametrize("after, expected", [(WEAK, "concern"), (HELPER, "no_concern")])
def test_recorded_kill_vectors(after, expected, recorded):
    result = invoke(Path(".build"), after)
    assert result.verdict == expected
    assert bool(result.outcome.lost_kills) == (expected == "concern")
    assert result.outcome.runs == len(recorded[0])
    assert result.outcome.provenance.shared_mechanism


def test_recorded_failing_fixed_suite_abstains(recorded):
    recorded[1][FIXED, digest(BEFORE)] = "failed"
    result = invoke(Path(".build"))
    assert result.verdict == "abstain"
    assert result.reason == "fixed_suite_did_not_pass"


@pytest.mark.parametrize("field, value", [("backend", None), ("network", True)])
def test_unconfined_observations_refused(recorded, monkeypatch, field, value):
    def staged(*a, **kw):
        return {"isolation": {"backend": "recorded", "network": False, field: value}}
    monkeypatch.setattr(probes, "_stage_run", staged)
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(Path(".build"))
    assert error.value.code == ISOLATION_UNAVAILABLE


def test_time_budget_after_return_is_refused(recorded, monkeypatch):
    now = [probes.time.monotonic()]
    original = probes._stage_run
    def staged(*a, **kw):
        now[0] += 181
        return original(*a, **kw)
    monkeypatch.setattr(probes.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probes, "_stage_run", staged)
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(Path(".build"))
    assert error.value.code == "G6_E_PROBE_BUDGET"
    assert len(recorded[0]) == 1


def test_missing_outcome_identity_is_not_a_fact(recorded):
    row = invoke(Path(".build")).outcome.model_dump(mode="json")
    row.pop("outcome_id")
    with pytest.raises(ValueError, match="identity"):
        extract_facts(BEFORE, WEAK, split="dev", probe_outcomes={"kill_set.v2": row})


def test_provenance_roundtrip_and_stale_sources(recorded):
    outcome = invoke(Path(".build")).outcome
    facts = extract_facts(BEFORE, WEAK, split="dev", probe_outcomes={"kill_set.v2": outcome})
    row = facts["probe_outcomes"]["kill_set.v2"]
    assert row["outcome_id"] == outcome.outcome_id
    assert len(row["provenance"]["observations"]) == outcome.runs
    assert probes.ProbeOutcome.model_validate(row) == outcome
    with pytest.raises(ValueError, match="source"):
        extract_facts(BEFORE, HELPER, split="dev", probe_outcomes={"kill_set.v2": outcome})
    row["verdict"] = "no_concern"
    with pytest.raises(ValueError):
        extract_facts(BEFORE, WEAK, split="dev", probe_outcomes={"kill_set.v2": row})


@pytest.mark.parametrize("patch", [{"limit": 0}, {"limit": True}, {"min_settled": 3}, {"lines": "all"},
                                   {"reruns": 0}, {"source": "print(1)"}])
def test_bad_bindings_fail_before_runner(recorded, patch):
    with pytest.raises(ValueError):
        invoke(Path(".build"), bindings={**BINDINGS, **patch})
    assert not recorded[0]


@pytest.mark.parametrize("base_status, verdict", [("passed", "concern"), ("failed", "no_concern"),
                                                 ("unknown", "abstain")])
def test_base_discrimination_observations(recorded, base_status, verdict):
    recorded[1][BASE, digest(WEAK)] = base_status
    result = invoke(Path(".build"), template="discriminates_at_base.v2", bindings={"reruns": 2},
                    base_implementation=BASE, new_tests=WEAK)
    assert result.verdict == verdict
    assert result.outcome.provenance.generator == "explicit_base"
    assert not result.outcome.provenance.shared_mechanism


def test_empty_new_tests_abstains_without_execution(recorded):
    result = invoke(Path(".build"), template="discriminates_at_base.v2", bindings={"reruns": 2},
                    base_implementation=BASE, new_tests={})
    assert result.verdict == "abstain"
    assert not recorded[0]


def test_unknown_mutant_abstains(recorded):
    sample = probes.mutation_check.mutants(FIXED, limit=2, lines=[2])
    recorded[1][sample[0].source, digest(BEFORE)] = "unknown"
    assert invoke(Path(".build")).verdict == "abstain"


def test_unstable_lost_kill_abstains(recorded, monkeypatch):
    original = probes._stage_run
    def staged(*a, **kw):
        raw = original(*a, **kw)
        if len(recorded[0]) > 6:
            raw["result"] = "passed"
        return raw
    monkeypatch.setattr(probes, "_stage_run", staged)
    assert invoke(Path(".build")).verdict == "abstain"


def test_budget_includes_controls_and_repeats(recorded):
    with pytest.raises(repo_tests.RepoRefused) as error:
        invoke(Path(".build"), budget={**BUDGET, "probe_runs": 8})
    assert error.value.code == "G6_E_PROBE_BUDGET"
    assert not recorded[0]


def test_too_few_mutants_abstains(recorded):
    bindings = {**BINDINGS, "limit": 24, "min_settled": 12}
    result = invoke(Path(".build"), bindings=bindings, budget={**BUDGET, "probe_runs": 100})
    assert result.verdict == "abstain"
    assert result.reason == "too_few_settled_mutants"


@pytest.mark.parametrize("patch", [{"target": "../subject.py"}, {"files": {"../subject.py": FIXED}},
                                   {"changed_lines": [999]}, {"changed_lines": [True]}])
def test_input_paths_and_changed_lines_are_bounded(recorded, patch):
    with pytest.raises(ValueError):
        invoke(Path(".build"), **patch)
    assert not recorded[0]


@pytest.mark.parametrize("files", [{"subject.py": FIXED, "Subject.py": BASE},
                                  {"subject.py": FIXED, "NUL.py": BASE}])
def test_windows_path_aliases_are_refused(recorded, files):
    with pytest.raises(ValueError):
        invoke(Path(".build"), files=files)
    assert not recorded[0]
