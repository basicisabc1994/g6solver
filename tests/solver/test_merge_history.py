# SPDX-License-Identifier: Apache-2.0
import pytest

from g6.solver import ConstraintSpec, SolverHistory, SolverInput, SolverOutput, STEP_REGISTRY, solve
from g6.solver import history
from g6.solver.schema import NamedCheck


def observed(answer="42", *, checked=True):
    constraints = [ConstraintSpec(raw="answer", kind="contains", value="42")] if checked else []
    return solve(SolverInput(goal="Return the number", constraints=constraints),
                 {name: (lambda ctx: {"answer": answer}) for name in STEP_REGISTRY.names()})


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))


def test_solver_checked_history_and_named_series_survive_reopening():
    # Removal: history.py INSERT loses the solver's own observed series (capability/SOLVER_ACCURACY-02).
    store = SolverHistory()
    for _ in range(30):
        assert store.record(observed(), series="arithmetic", dry_run=False).decision == "recorded"
    result = SolverHistory().check_regression(series="arithmetic", threshold=0.8)
    assert result.decision == "pass" and result.completion_state == "qualified-draft"
    assert result.metrics["n"] == 30 and result.metrics["mean"] == 1
    assert result.metrics["observed_by"] == "g6.solver.solve"
    assert "Clopper-Pearson" in result.to_envelope()["evidence"][0]["method"]
    assert SolverHistory().trend(series="other").completion_state == "blocked-escalated"


def test_a_pass_needs_the_exact_lower_bound_at_the_threshold():
    # Removal: history.py `if lower < threshold:` (review below the exact lower bound) passes 30 of 30 at 0.9.
    store = SolverHistory()
    for _ in range(10):
        store.record(observed(), dry_run=False)
    ten = store.check_regression(threshold=0.8)  # 10 of 10: exact two-sided 95% lower bound 0.025 ** 0.1 = 0.69
    assert ten.decision == "review_required" and ten.completion_state == "blocked-escalated"
    assert abs(ten.metrics["interval"][0] - 0.025 ** 0.1) < 1e-9 and ten.metrics["interval"][1] == 1
    for _ in range(20):
        store.record(observed(), dry_run=False)
    thirty = store.check_regression(threshold=0.9)  # 30 of 30: lower bound 0.025 ** (1/30) = 0.884
    assert thirty.decision == "review_required" and thirty.completion_state == "blocked-escalated"
    assert store.check_regression(threshold=0.88).decision == "pass"


def test_a_significant_decline_above_the_threshold_is_flagged():
    # Removal: history.py `if found.metrics["decline_significant"]:` misses a fall from 100% to 85% (D1).
    store = SolverHistory()
    for answer in ["42"] * 100 + ["wrong"] * 15 + ["42"] * 85:
        store.record(observed(answer), dry_run=False)
    result = store.check_regression(threshold=0.5, window=100)
    assert result.metrics["mean"] == 0.85 and result.metrics["interval"][0] > 0.5
    assert result.metrics["decline_significant"] is True and abs(result.metrics["delta"] + 0.15) < 1e-12
    assert result.decision == "regression" and result.code == "G6_E_SOLVER_HISTORY_DECLINE"
    assert result.completion_state == "blocked-escalated"


@pytest.mark.parametrize("bad", [{"min_samples": 2, "max_ci_width": 1}, {"min_samples": 9}])
def test_min_samples_has_a_floor_of_ten(bad):
    # Removal: history.py `min_samples < 10` lets a caller pass on two samples.
    store = SolverHistory()
    for _ in range(2):
        store.record(observed(), dry_run=False)
    result = store.check_regression(threshold=0.1, **bad)
    assert result.decision == "refused" and result.completion_state == "blocked-escalated"


def test_forged_or_modified_solver_checks_cannot_enter_history():
    # Removal: history.py `_receipt_matches(output)` permits caller quality claims (SOLVER_ACCURACY-01).
    forged = SolverOutput(status="completed", final_answer="42",
                          checks=[NamedCheck(name="invented", kind="deterministic", verdict="pass")])
    assert SolverHistory().record(forged, dry_run=False).completion_state == "blocked-escalated"
    real = observed()
    assert SolverHistory().record(real.model_copy(), dry_run=False).completion_state == "blocked-escalated"
    real.final_answer = "forged"
    assert SolverHistory().record(real, dry_run=False).completion_state == "blocked-escalated"


def test_checked_decline_is_reported_and_blocks():
    # Removal: history.py `if mean < threshold:` misses an observed decline (D1).
    store = SolverHistory()
    for answer in ["42"] * 10 + ["wrong"] * 10:
        store.record(observed(answer), dry_run=False)
    result = store.check_regression(threshold=0.8, window=10)
    assert result.decision == "regression" and result.completion_state == "blocked-escalated"
    assert result.metrics["delta"] == -1 and result.metrics["mean"] == 0


def test_low_sample_and_wide_interval_do_not_pass():
    # Removal: history.py `if n < min_samples or interval is None or ...` permits unsupported pass claims.
    store = SolverHistory()
    store.record(observed(), dry_run=False)
    assert store.check_regression(threshold=0.1).decision == "review_required"
    for answer in ["42"] * 4 + ["wrong"] * 5:
        store.record(observed(answer), dry_run=False)
    result = store.check_regression(threshold=0.1)
    assert result.decision == "review_required" and result.completion_state == "blocked-escalated"
    assert result.metrics["interval"][1] - result.metrics["interval"][0] > 0.3


def test_dry_run_writes_nothing_and_duplicate_run_earns_no_sample(tmp_path):
    # Removal: history.py `if dry_run:` persists by default; unique key prevents repeated credit.
    store, out = SolverHistory(), observed()
    assert store.record(out).decision == "dry_run" and list(tmp_path.iterdir()) == []
    assert store.record(out, dry_run=False).decision == "recorded"
    assert store.record(out, dry_run=False).completion_state == "blocked-escalated"
    assert store.trend().metrics["n"] == 1


def test_unchecked_runs_are_counted_but_never_pass_samples():
    # Removal: history.py `if row[0] != "unchecked"` turns unchecked answers into accuracy evidence.
    store = SolverHistory()
    store.record(observed(checked=False), dry_run=False)
    result = store.trend()
    assert result.metrics["n"] == 0 and result.metrics["unchecked"] == 1
    assert result.completion_state == "blocked-escalated"


@pytest.mark.parametrize("dependency", ["write", "read"])
def test_history_dependency_failure_is_blocked(monkeypatch, dependency):
    # Removal: history.py exception-to-refusal branches allow dependency failures to escape as usable history.
    def broken(*a, **kw):
        raise OSError("history dependency down")
    store, out = SolverHistory(), observed()
    if dependency == "write":
        monkeypatch.setattr(history, "open_db", broken)
    else:
        store.record(out, dry_run=False)
        monkeypatch.setattr(history.sqlite3, "connect", broken)
    result = store.trend() if dependency == "read" else store.record(out, dry_run=False)
    assert result.completion_state == "blocked-escalated"
    assert "history dependency down" in result.reason


@pytest.mark.parametrize("gate", ["raises", "holds"])
def test_history_write_is_a_sensitive_op_never_action_gated(monkeypatch, gate):
    # Removal: history.py `conn.execute("INSERT ...")` loses the write; it also fails while history.py calls evaluate_action.
    from types import SimpleNamespace
    import g6.action_gating
    def raises(*a, **kw):
        raise OSError("action gate consulted")
    def holds(*a, **kw):
        return SimpleNamespace(gate_decision=SimpleNamespace(decision="block"))
    monkeypatch.setattr(g6.action_gating, "evaluate_action", raises if gate == "raises" else holds)
    out = SolverHistory().record(observed(), dry_run=False)
    assert out.decision == "recorded" and out.completion_state == "qualified-draft", out
    assert SolverHistory().trend().metrics["n"] == 1


def test_storage_readback_mismatch_rolls_back(monkeypatch):
    # Removal: history.py `if tuple(found) != ...` permits an unconfirmed stored observation.
    original = history.open_db
    class BadReadback:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, params=()):
            if sql.startswith("SELECT outcome"):
                return self
            return self.conn.execute(sql, params)
        def fetchone(self):
            return ("failed", "g6.solver.solve")
        def __enter__(self):
            self.conn.__enter__()
            return self
        def __exit__(self, *args):
            return self.conn.__exit__(*args)
        def close(self):
            self.conn.close()
    monkeypatch.setattr(history, "open_db", lambda path: BadReadback(original(path)))
    store = SolverHistory()
    assert store.record(observed(), dry_run=False).completion_state == "blocked-escalated"
    assert store.trend().metrics["n"] == 0
