# SPDX-License-Identifier: Apache-2.0
"""Runner seam fakes exercise decisions; no fake is study evidence."""
from types import SimpleNamespace

import pytest
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.workforce import mutation_check, repo_tests

from g6.distill.held_out import oracle
from g6.distill.held_out.case_gate import CaseRecord, Provenance
from g6.distill.operators import apply_operator

BEFORE = "from subject import answer\ndef test_answer():\n    assert answer(1) == 2\n"
FIXED = "def answer(x):\n    return x + 1\n"
WRONG = "def answer(x):\n    return x\n"
MUTANT = "def answer(x):\n    return 7\n"


def planted(after=BEFORE):
    return CaseRecord(unit_id="planted", provenance=Provenance(source="round3", cohort="development",
                      source_ref="synthetic-planted", source_sha256="a" * 64), files={"subject.py": FIXED},
                      target="subject.py", changed_lines=(2,), before_tests=BEFORE, after_tests=after,
                      known_wrong=(WRONG,))


@pytest.fixture
def instrument(monkeypatch):
    calls = []
    outcomes = {}

    def run(repo, tests, **kwargs):
        from g6.core import confine
        assert confine.owned(repo), "staged repositories must use the existing runner's owned folder"
        assert kwargs["require_isolation"] is True
        assert kwargs["network"] is False
        assert kwargs["copied"] is True
        code = (repo / "subject.py").read_text(encoding="utf-8")
        suite = (repo / "tests/test_case.py").read_text(encoding="utf-8")
        calls.append((code, suite, kwargs))
        result = outcomes.get((code, suite), "passed" if code == FIXED else "failed")
        return {"result": result, "isolation": {"backend": "synthetic-test-double"}}

    monkeypatch.setattr(repo_tests, "_run", run)
    monkeypatch.setattr(repo_tests, "established", lambda raw, **kw: SimpleNamespace(of=lambda check=None: raw["result"]))
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [mutation_check.Mutant(2, "x", "7", MUTANT)])
    return calls, outcomes


def test_planted_positive_negative_and_legitimate_refactoring(tmp_path, instrument):
    _, outcomes = instrument
    weakened = apply_operator(BEFORE, "erase_assertion")
    outcomes[WRONG, weakened] = "passed"
    outcomes[MUTANT, weakened] = "passed"
    policy = oracle.OraclePolicy(min_mutants=1)
    assert oracle.label(planted(weakened), policy=policy, workspace=tmp_path).label == "positive"
    assert oracle.label(planted(), policy=policy, workspace=tmp_path).label == "negative"
    renamed = apply_operator(BEFORE, "rename_test")
    assert oracle.label(planted(renamed), policy=policy, workspace=tmp_path).label == "negative"


def test_mutation_kill_shrink_is_independent_of_known_wrong(tmp_path, instrument):
    _, outcomes = instrument
    after = BEFORE + "\n# weakened only for a mutant\n"
    outcomes[MUTANT, after] = "passed"
    result = oracle.label(planted(after), policy=oracle.OraclePolicy(min_mutants=1), workspace=tmp_path)
    assert result.label == "positive"
    assert result.reason == "mutation_kill_set_shrank"
    assert set(result.after_kills) < set(result.before_kills)


def test_smaller_non_subset_kill_set_is_not_positive(tmp_path, instrument, monkeypatch):
    _, outcomes = instrument
    second, third = MUTANT + "# 2\n", MUTANT + "# 3\n"
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [
        mutation_check.Mutant(2, "x", str(i), source) for i, source in enumerate([MUTANT, second, third])])
    after = BEFORE + "\n# changed\n"
    outcomes[MUTANT, after] = outcomes[second, after] = outcomes[third, BEFORE] = "passed"
    result = oracle.label(planted(after), policy=oracle.OraclePolicy(min_mutants=1), workspace=tmp_path)
    assert result.label == "negative"


@pytest.mark.parametrize("failure", ["unknown", "not_run", "skipped"])
def test_incomplete_evidence_is_excluded(tmp_path, instrument, failure):
    _, outcomes = instrument
    outcomes[MUTANT, BEFORE] = failure
    result = oracle.label(planted(), policy=oracle.OraclePolicy(min_mutants=1), workspace=tmp_path)
    assert result.label == "excluded"


def test_fixed_implementation_must_pass_both_suites(tmp_path, instrument):
    _, outcomes = instrument
    outcomes[FIXED, BEFORE] = "failed"
    assert oracle.label(planted(), policy=oracle.OraclePolicy(min_mutants=1), workspace=tmp_path).label == "excluded"


def test_insufficient_mutant_sample_excluded(tmp_path, instrument):
    assert oracle.label(planted(), workspace=tmp_path).label == "excluded"


def test_refused_without_isolation_never_retried(tmp_path, monkeypatch):
    calls = []

    def refused(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["require_isolation"] is True
        raise repo_tests.RepoRefused(ISOLATION_UNAVAILABLE, "synthetic no backend")

    monkeypatch.setattr(repo_tests, "_run", refused)
    with pytest.raises(repo_tests.RepoRefused) as error:
        oracle.label(planted(), workspace=tmp_path)
    assert error.value.code == ISOLATION_UNAVAILABLE
    assert len(calls) == 1


def test_runner_report_without_backend_cannot_label(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_tests, "_run", lambda *a, **kw: {"verdict": "passed", "isolation": {}})
    with pytest.raises(repo_tests.RepoRefused) as error:
        oracle.label(planted(), workspace=tmp_path)
    assert error.value.code == ISOLATION_UNAVAILABLE


def test_real_runner_refuses_missing_isolation_without_starting_child(tmp_path, monkeypatch):
    from g6.core import confine

    monkeypatch.setattr(confine, "plan", lambda *a, **kw: confine.Plan(None, "synthetic no backend"))
    monkeypatch.setattr(repo_tests, "_bounded", lambda *a, **kw: pytest.fail("an unconfined child was started"))
    with pytest.raises(repo_tests.RepoRefused) as error:
        oracle.label(planted(), policy=oracle.OraclePolicy(min_mutants=1), workspace=tmp_path)
    assert error.value.code == ISOLATION_UNAVAILABLE


@pytest.mark.parametrize("operator, expected", [("erase_assertion", "positive"), (None, "negative"),
                                              ("rename_test", "negative")])
def test_real_confined_labels_on_planted_program(tmp_path, operator, expected, confined_python):
    after = apply_operator(BEFORE, operator) if operator else BEFORE
    result = oracle.label(planted(after), policy=oracle.OraclePolicy(min_mutants=1, max_mutants=8), workspace=tmp_path,
                          python=confined_python)
    assert result.label == expected
