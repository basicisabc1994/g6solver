# SPDX-License-Identifier: Apache-2.0
"""adapt_automl: a bounded, seeded search over scikit-learn estimators and their settings by cross-validation inside
the training rows only, the choice scored by G6 on rows the extension held out itself (unsystematic_tuning,
silent_fallback, train_test_leakage). A backend that is not there is refused, never swapped; nothing is loaded."""
from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import g6.ml.adapt_automl as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.ml import _fit, _types
from g6.ml._types import DatasetSpec, prepare
from g6.ml.adapt_automl import backends, spaces

SRC = Path(package.__file__).parent
needs_flaml = pytest.mark.skipif(importlib.util.find_spec("flaml") is None or importlib.util.find_spec("sklearn") is None,
                                 reason="FLAML and scikit-learn (the automl extra) are not installed")


def churn(n: int = 120, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        churned = rng.random() < 0.4
        rows.append({"tenure": round(rng.gauss(10 if churned else 30, 8), 2),
                     "plan": rng.choice(["basic", "plus"] if churned else ["plus", "pro"]),
                     "churned": "yes" if churned else "no"})
    return rows


CHURN = {"target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7}
FAST = {"time_budget_s": 20, "trials": 8, "candidates": ["logistic_regression", "random_forest"]}


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_automl", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_automl", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


@pytest.fixture
def no_search(monkeypatch: pytest.MonkeyPatch):
    def started(*args, **kwargs):
        raise AssertionError("a search started")

    monkeypatch.setattr(_fit, "run", started)


class Majority:
    """A backend of the test's own: it predicts the most frequent training class, and records what it was given."""

    name, extra = "majority_test", ""

    def __init__(self) -> None:
        self.given: list[dict] = []

    def available(self) -> str:
        return ""

    def search(self, job: backends.SearchJob) -> backends.SearchOutcome:
        self.given.append(job.payload)
        y = job.payload["y_train"]
        top = max(sorted(set(y)), key=y.count)
        return backends.SearchOutcome(held_out=[top] * len(job.payload["X_held_out"]), train=[top] * len(y),
                                      leaderboard=[{"name": "logistic_regression", "trials": 1, "best_score": 0.6}],
                                      chosen="logistic_regression", chosen_config={}, ended_by="trial cap")


@pytest.fixture
def majority():
    found = Majority()
    backends.search_backends.register(found)
    yield found
    backends.search_backends.unregister(found.name)


# -- D1 --------------------------------------------------------------------------------------------------------------

def test_d1_a_feature_that_is_the_target_shifted_by_a_constant_is_refused_before_any_search(no_search):
    rng = random.Random(2)
    rows = [{"size": rng.uniform(40, 200), "rooms": rng.randint(1, 6)} for _ in range(80)]
    for row in rows:
        row["price"] = 2.5 * row["size"] + 10 * row["rooms"] + rng.gauss(0, 30)
        row["sold_for"] = row["price"] + 25
    out = _invoke("search", rows=rows, target="price", features=["size", "rooms", "sold_for"], task="regression",
                  seed=1)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.LEAKAGE
    assert "sold_for" in out["reason"]


# -- ADAPT_AUTOML-01: the label waits for the held-out score, and a model below the baseline says so ----------------

@needs_flaml
def test_p0_a_search_that_learns_nothing_is_said_not_to_beat_the_baseline():
    rng = random.Random(4)
    rows = [{"a": rng.random(), "b": rng.random(), "y": rng.choice(["p", "q"])} for _ in range(120)]
    out = _invoke("search", rows=rows, target="y", features=["a", "b"], task="classification", seed=1, **FAST)
    assert out["completion_state"] == "qualified-draft" and check_envelope(out) == []
    assert out["warning_card"]["code"] == _types.BELOW_BASELINE and not out["headline"]["clearly_better"]
    searched = next(e for e in out["evidence"] if e["kind"] == "ml_search")
    assert "optimistic" in searched["leaderboard_means"] and searched["on"].startswith("the training rows only")
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


@needs_flaml
def test_a_seeded_search_finds_a_real_signal_and_repeats():
    one = _invoke("search", rows=churn(), **CHURN, **FAST, predict_rows=[{"tenure": 2, "plan": "basic"}])
    two = _invoke("search", rows=churn(), **CHURN, **FAST)
    assert one["warning_card"]["code"] == _types.MEASURED and one["headline"]["clearly_better"]
    assert (one["chosen"], one["chosen_config"], one["headline"]) == (two["chosen"], two["chosen_config"],
                                                                     two["headline"])
    searched = next(e for e in one["evidence"] if e["kind"] == "ml_search")
    assert searched["ended_by"] == "trial cap" and sum(r["trials"] for r in searched["leaderboard"]) == 8
    assert one["predictions"] == [{"row": 0, "prediction": "yes"}]


# -- ADAPT_AUTOML-03 / -04: every fitted step inside the fold; a candidate that cannot be validated is excluded -------

def _job(rows: list[dict], candidates: list[str], trials: int = 4) -> dict:
    prepared = prepare(DatasetSpec(rows=rows, **CHURN))
    encoded = _fit.encode(prepared)
    return _fit.job("search", prepared, encoded, candidates=spaces.resolve(candidates, "classification"), folds=3,
                    metric="accuracy", time_budget_s=20, trials=trials, X_predict=[])


@needs_flaml
def test_p1_every_fit_in_the_search_sees_only_its_folds_training_rows(monkeypatch):
    from g6.ml.adapt_automl import flaml_child

    sizes = []
    real = _fit._pipeline

    def counted(spec, job):
        pipe = real(spec, job)
        fit = pipe.fit

        def recording(X, y):
            sizes.append(len(X))
            return fit(X, y)

        pipe.fit = recording
        return pipe

    monkeypatch.setattr(_fit, "_pipeline", counted)
    job = _job(churn(), ["logistic_regression"], trials=3)
    out = flaml_child.search(job)
    train = len(job["y_train"])
    assert out["chosen"] == "logistic_regression" and sizes[-1] == train   # the final refit: all training rows
    assert sizes[:-1] and all(s < train for s in sizes[:-1])              # every trial: one fold's training rows
    assert "y_held_out" not in json.dumps(job)


@needs_flaml
def test_p1_a_candidate_none_of_whose_trials_validates_is_excluded_never_scored_on_training_rows():
    from g6.ml.adapt_automl import flaml_child

    spaces.register_space(spaces.Space("broken_logistic",
                                       {"classification": "sklearn.linear_model:LogisticRegression"},
                                       {"tol": ("loguniform", 1e-6, 1e-2)}, fixed={"C": -1.0}))
    try:
        out = flaml_child.search(_job(churn(), ["broken_logistic", "logistic_regression"], trials=4))
    finally:
        spaces.unregister_space("broken_logistic")
    broken = next(r for r in out["leaderboard"] if r["name"] == "broken_logistic")
    assert "excluded" in broken and "best_score" not in broken and broken["failed"] == broken["trials"] > 0
    assert out["chosen"] == "logistic_regression"


# -- ADAPT_AUTOML-02, ADAPT_AUTOSKLEARN-01/-02: nothing loaded, a missing engine refused, never swapped ---------------

@pytest.mark.parametrize("name", ["mljar", "autosklearn", "h2o", "autogluon", "no_such_engine"])
def test_p0_an_engine_that_is_not_registered_is_refused_never_swapped(name, no_search):
    out = _invoke("search", rows=churn(), **CHURN, backend=name)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.BACKEND_NOT_INSTALLED
    assert "no other backend was used instead" in out["reason"] and "registered: flaml" in out["reason"]


@pytest.mark.parametrize("name", ["sklearn.svm:SVC", "__import__('os')", "../model.joblib"])
def test_p0_a_class_or_a_model_file_named_as_a_candidate_is_refused_as_code(name, no_search):
    out = _invoke("search", rows=churn(), **CHURN, candidates=[name])
    assert out["code"] == _types.UNTRUSTED_CODE


@pytest.mark.parametrize("field", [{"import_path": "m.joblib"}, {"model": "gASV"}, {"completion_state": "verified"},
                                   {"business_objective": "classify"}, {"deployment_intent": "prod"},
                                   {"approved": True}, {"held_out_score": 0.99}, {"allow_fallback": True}])
def test_g3_claims_model_files_and_the_archives_governance_strings_are_refused(field):
    assert "G6_E_INPUT_VALIDATION" in _refused("search", rows=churn(), **CHURN, **field)


# -- D5, G4: a backend from outside is used, scored by G6, and its outcome checked -------------------------------------

def test_d5_a_backend_registered_from_outside_is_given_no_held_out_answer_and_scored_by_g6(majority):
    out = _invoke("search", rows=churn(), **CHURN, backend="majority_test", candidates=["logistic_regression"])
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == _types.BELOW_BASELINE
    assert out["headline"]["held_out"] == out["headline"]["baseline"]   # it predicted what the baseline predicts
    payload = majority.given[0]
    assert len(payload["y_train"]) == out["split"]["train_rows"] and "y_held_out" not in json.dumps(payload)
    assert len(payload["X_held_out"]) == out["split"]["held_out_rows"]


@pytest.mark.parametrize("change, said", [({"chosen": "random_forest"}, "nothing is reported from a disagreement"),
                                          ({"held_out": ["yes"]}, "held-out and"),
                                          ({"leaderboard": [{"name": "logistic_regression", "excluded": "x"}]},
                                           "no candidate finished")])
def test_g4_a_backends_outcome_is_checked_before_anything_is_reported(majority, monkeypatch, change, said):
    real = majority.search

    def altered(job):
        outcome = real(job)
        for key, value in change.items():
            setattr(outcome, key, value)
        return outcome

    monkeypatch.setattr(majority, "search", altered)
    out = _invoke("search", rows=churn(), **CHURN, backend="majority_test", candidates=["logistic_regression"])
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.FIT_FAILED and said in out["reason"]


def test_g4_a_backend_that_raises_is_a_refusal_with_its_reason(majority, monkeypatch):
    def broken(job):
        raise RuntimeError("the engine broke")

    monkeypatch.setattr(majority, "search", broken)
    out = _invoke("search", rows=churn(), **CHURN, backend="majority_test", candidates=["logistic_regression"])
    assert out["completion_state"] == "blocked-escalated" and "the engine broke" in out["reason"]


# -- G5, G6/G7, D2, D6, D9 ------------------------------------------------------------------------------------------

@needs_flaml
def test_g5_no_op_is_sensitive_and_the_search_reaches_no_network(monkeypatch):
    assert package.__g6_sensitive_ops__ == () and package.__g6_outside__ == ()

    def refused(*args, **kwargs):
        raise AssertionError("adapt_automl opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refused)
    out = _invoke("search", rows=churn(), **CHURN, time_budget_s=10, trials=2, candidates=["logistic_regression"])
    assert out["completion_state"] == "qualified-draft"


def test_g6_g7_the_package_reads_no_key_calls_no_model_and_holds_no_stand_in():
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"getenv|os\.environ|API_KEY|litellm|openai|anthropic|g6\.core\.llm", text), path.name
        assert not re.search(r"(?i)mock|fake|simulat|import (pickle|joblib)|pickle\.loads?\(|joblib\.load|torch\.load",
                             text), path.name


def test_d2_it_declares_what_it_guards_against_and_left_planned():
    assert package.__g6_mitigates__ == ("unsystematic_tuning", "silent_fallback", "train_test_leakage")
    assert package.__g6_introduces__ == ("unevaluated_model_shipped",)
    assert "g6.ml.adapt_automl" not in FailureModeRegistry.planned()


def test_d6_found_by_its_failure_mode_described_as_listed_on_its_certificate_and_invoked_over_mcp():
    title = FailureModeRegistry.title("unsystematic_tuning")
    assert "adapt_automl" in [r["component"] for r in discovery.find(title, limit=5)]
    info = describe_reply("adapt_automl")
    assert info["family"] == "ml" and info["skill"] and sorted(info["ops"]) == ["backends", "search"]
    assert info["vetting"]["status"] == "reviewed"   # its certificate is current (DECISIONS #39: no person's review)
    reply = invoke_reply("adapt_automl", "backends", "{}")
    assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    assert [b["name"] for b in reply["result"]["backends"]] == ["flaml"] and "mljar" in reply["result"]["not_shipped"]


_WITHOUT = """
import json, random, sys
sys.modules["flaml"] = None
from g6.core.registry import get_registry
reg = get_registry()
rng = random.Random(0)
rows = [{"x": rng.random(), "y": rng.choice(["a", "b"])} for _ in range(60)]
base = {"rows": rows, "target": "y", "features": ["x"], "task": "classification", "seed": 1}
found = reg.invoke("adapt_automl", "search", base).unwrap()
listed = reg.invoke("adapt_automl", "backends", {}).unwrap()
leak = reg.invoke("adapt_automl", "search", {**base, "features": ["x", "y"]}).unwrap()
print(json.dumps({"search": [found["code"], found["reason"]], "listed": listed["warning_card"]["message"],
                  "leak": leak["code"]}))
"""


def test_d9_without_the_extra_a_search_says_what_to_install_and_the_checks_still_run(tmp_path):
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          cwd=tmp_path, env={**os.environ, "G6_HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["search"][0] == _types.BACKEND_NOT_INSTALLED and "pip install 'g6[automl]'" in found["search"][1]
    assert "not installed" in found["listed"] and found["leak"] == _types.LEAKAGE
