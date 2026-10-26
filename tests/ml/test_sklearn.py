# SPDX-License-Identifier: Apache-2.0
"""adapt_sklearn: a scikit-learn estimator from G6's own list, fitted out of G6's process on the extension's own
training split and scored by G6 on held-out rows the fit never saw the answers for (train_test_leakage,
ill_suited_model). Nothing is ever loaded from a caller: no pickle, no model file, no class."""
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

import g6.ml.adapt_sklearn as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.ml import _fit, _types
from g6.ml.adapt_sklearn import estimators

SRC = Path(package.__file__).parent
needs_sklearn = pytest.mark.skipif(importlib.util.find_spec("sklearn") is None,
                                   reason="scikit-learn (the ml extra) is not installed")


def churn(n: int = 120, seed: int = 0, minority: float = 0.4) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        churned = rng.random() < minority
        rows.append({"tenure": round(rng.gauss(10 if churned else 30, 8), 2),
                     "plan": rng.choice(["basic", "plus"] if churned else ["plus", "pro"]),
                     "churned": "yes" if churned else "no"})
    return rows


def prices(n: int = 100, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        size, rooms = rng.uniform(40, 200), rng.randint(1, 6)
        rows.append({"size": round(size, 1), "rooms": rooms, "price": round(2.5 * size + 10 * rooms + rng.gauss(0, 30))})
    return rows


CHURN = {"target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7}


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_sklearn", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_sklearn", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


@pytest.fixture
def no_fit(monkeypatch: pytest.MonkeyPatch):
    """Fails the test if a fit starts."""
    def started(*args, **kwargs):
        raise AssertionError("a fit started")

    monkeypatch.setattr(_fit, "run", started)


# -- D1: leakage is refused before anything is fitted ----------------------------------------------------------------

def test_d1_a_feature_that_is_the_target_shifted_by_a_constant_is_refused_before_any_fit(no_fit):
    rows = prices()
    for row in rows:
        row["listed_price"] = row["price"] + 25
    out = _invoke("fit_eval", rows=rows, target="price", features=["size", "rooms", "listed_price"],
                  task="regression", seed=3, estimator="ridge")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.LEAKAGE
    assert "listed_price" in out["reason"]
    rank = _invoke("rank", rows=rows, target="price", features=["size", "rooms", "listed_price"], task="regression",
                   seed=3)
    assert rank["code"] == _types.LEAKAGE


@needs_sklearn
def test_d1_a_target_two_features_add_up_to_is_refused_as_a_leak_never_measured():
    rng = random.Random(6)
    rows = []
    for _ in range(120):
        a, b = round(rng.uniform(0, 100), 2), round(rng.uniform(0, 100), 2)
        rows.append({"a": a, "b": b, "y": round(a + b, 2)})          # neither alone carries y; together they are y
    probes = _types.prepare(_types.DatasetSpec(rows=rows, target="y", features=["a", "b"], task="regression", seed=3))
    assert next(e for e in probes.evidence if e["kind"] == "ml_leakage_probes")["suspected"] == []
    out = _invoke("fit_eval", rows=rows, target="y", features=["a", "b"], task="regression", seed=3,
                  estimator="linear_regression")
    assert out["warning_card"]["code"] != _types.MEASURED, out["warning_card"]["message"]
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.LEAKAGE
    assert "together" in out["reason"] and "'a'" in out["reason"] and "'b'" in out["reason"]


# -- ADAPT_SKLEARN-01 / -03: the headline is held out, never a training score, and never verified --------------------

@needs_sklearn
def test_p0_a_training_score_is_never_the_headline_nor_described_as_success():
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="decision_tree",
                  predict_rows=[{"tenure": 3, "plan": "basic"}, {"tenure": 40, "plan": "pro"}])
    assert out["completion_state"] == "qualified-draft" and check_envelope(out) == []
    h = out["headline"]
    assert h["train"] == 1.0 and h["held_out"] < 1.0 and h["held_out_rows"] == out["split"]["held_out_rows"]
    assert out["scores"]["train"]["accuracy"] == 1.0                        # reported, as the training rows' score
    message = out["warning_card"]["message"]
    assert message.index("held-out rows") < message.index("on its training rows")
    assert h["gap"] == round(1.0 - h["held_out"], 6) and "correctly classifies" not in message
    assert [p["prediction"] for p in out["predictions"]] == ["yes", "no"]
    fit = next(e for e in out["evidence"] if e["kind"] == "ml_fit")
    assert fit["model_kept"] is False and fit["class"] == "sklearn.tree.DecisionTreeClassifier"
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


@needs_sklearn
def test_p0_another_seed_scores_the_same_held_out_rows_and_the_reply_counts_each_scoring(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    given = []
    real = _fit.run

    def spy(payload, timeout_s):
        given.append(payload["X_held_out"])
        return real(payload, timeout_s)

    monkeypatch.setattr(_fit, "run", spy)
    rows = churn()
    one = _invoke("fit_eval", rows=rows, **CHURN, estimator="naive_bayes")
    two = _invoke("fit_eval", rows=rows, **{**CHURN, "seed": 8}, estimator="naive_bayes")
    assert given[0] == given[1] and one["split"]["held_out_sha256"] == two["split"]["held_out_sha256"]
    assert "scored 0 times before" in one["warning_card"]["message"]
    assert "scored 1 time before" in two["warning_card"]["message"]
    use = two["held_out_use"]
    assert use["scored_before"] == 1 and use["earlier_ops"] == {"fit_eval": 1} and "never a gate" in use["gate"]
    kept = (tmp_path / "ml").iterdir()
    assert all(rows[0]["plan"].encode() not in path.read_bytes() for path in kept)   # hashes and times, no rows


@needs_sklearn
def test_a_model_that_learns_nothing_is_said_not_to_beat_the_baseline():
    rng = random.Random(4)
    rows = [{"a": rng.random(), "b": rng.random(), "y": rng.choice(["p", "q"])} for _ in range(120)]
    out = _invoke("fit_eval", rows=rows, target="y", features=["a", "b"], task="classification", seed=1,
                  estimator="random_forest")
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == _types.BELOW_BASELINE
    assert out["headline"]["clearly_better"] is False and "does not clearly beat the baseline" in out[
        "warning_card"]["message"]


@needs_sklearn
def test_regression_is_scored_by_r_squared_against_the_mean():
    out = _invoke("fit_eval", rows=prices(), target="price", features=["size", "rooms"], task="regression", seed=3,
                  estimator="ridge", params={"alpha": 1.0})
    h = out["headline"]
    assert h["metric"] == "r2" and h["held_out"] > 0.8 and h["baseline"] <= 0 and h["clearly_better"]
    assert set(out["scores"]["held_out"]) == {"r2", "mae", "rmse"}
    assert out["warning_card"]["code"] == _types.MEASURED


@needs_sklearn
def test_imbalanced_classes_change_the_headline_to_balanced_accuracy():
    out = _invoke("fit_eval", rows=churn(300, seed=2, minority=0.07), **CHURN, estimator="logistic_regression")
    assert out["headline"]["metric"] == "balanced_accuracy"
    assert "the classes are imbalanced" in out["warning_card"]["message"]
    balance = next(e for e in out["evidence"] if e["kind"] == "ml_class_balance")
    assert balance["imbalanced"] and balance["threshold"] == 0.10


# -- rank: adapt_learning merged in, candidates compared inside the training rows only --------------------------------

@needs_sklearn
def test_rank_cross_validates_inside_the_training_rows_and_scores_the_choice_once(monkeypatch):
    seen = []
    real = _fit.run

    def spy(payload, timeout_s):
        seen.append(payload)
        return real(payload, timeout_s)

    monkeypatch.setattr(_fit, "run", spy)
    out = _invoke("rank", rows=churn(), **CHURN, candidates=["logistic_regression", "naive_bayes"], folds=4)
    assert out["completion_state"] == "qualified-draft" and out["chosen"] == out["ranking"][0]["name"]
    payload = seen[0]
    assert len(payload["y_train"]) == out["split"]["train_rows"] and "y_held_out" not in json.dumps(payload)
    cv = next(e for e in out["evidence"] if e["kind"] == "ml_cross_validation")
    assert cv["on"].startswith("the training rows only") and sum(f["valid"] for f in cv["fold_sizes"]) == len(
        payload["y_train"])
    meta = next(e for e in out["evidence"] if e["kind"] == "ml_meta_features")
    assert meta["sparsity"] is not None and meta["used_for"].startswith("description only")
    assert all(len(r["fold_scores"]) == 4 for r in out["ranking"])


@needs_sklearn
def test_d5_an_estimator_registered_from_outside_joins_and_one_that_cannot_be_validated_is_excluded():
    broken = estimators.Estimator("broken_logistic", {"classification": "sklearn.linear_model:LogisticRegression"},
                                  "a logistic regression with an impossible penalty", fixed={"C": -1.0})
    estimators.register_estimator(broken)
    try:
        out = _invoke("rank", rows=churn(), **CHURN, candidates=["broken_logistic", "naive_bayes"])
        assert out["chosen"] == "naive_bayes" and [r["name"] for r in out["ranking"]] == ["naive_bayes"]
        assert out["excluded"][0]["name"] == "broken_logistic" and out["excluded"][0]["why"]
    finally:
        estimators.unregister_estimator("broken_logistic")
    with pytest.raises(ValueError, match="scikit-learn's own"):
        estimators.register_estimator(estimators.Estimator("shell", {"classification": "os:system"}, "no"))


# -- ADAPT_SKLEARN-02: nothing a caller names is loaded or imported ---------------------------------------------------

@pytest.mark.parametrize("name", ["sklearn.base.BaseEstimator", "__import__('os').system('calc')", "../model.pkl",
                                  "joblib:load", "my_module:MyEstimator"])
def test_p0_a_class_a_module_or_a_model_file_is_refused_as_code_never_imported(name, no_fit):
    before = set(sys.modules)
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator=name)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.UNTRUSTED_CODE
    assert {"my_module", "joblib"} & (set(sys.modules) - before) == set()


@pytest.mark.parametrize("field", [{"artifact_path": "m.joblib"}, {"model": "gASVAAAA"}, {"model_path": "x.pkl"},
                                   {"params": {"C": {"__reduce__": "os.system"}}}])
def test_p0_there_is_no_way_to_hand_in_a_model(field):
    assert "G6_E_INPUT_VALIDATION" in _refused("fit_eval", rows=churn(), **CHURN, estimator="ridge", **field)


def test_unknown_estimators_and_parameters_are_refused_with_the_list(no_fit):
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="svm")
    assert out["code"] == _types.UNKNOWN_ESTIMATOR and "random_forest" in out["reason"]
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="random_forest", params={"n_estimators": 10_000})
    assert out["code"] == _types.INPUT_INVALID and "between 1 and 500" in out["reason"]
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="random_forest", params={"max_features": 1})
    assert out["code"] == _types.INPUT_INVALID                          # 1 is one feature, 1.0 is all of them


@pytest.mark.parametrize("name", ["random_state", "seed", "random_seed"])
def test_g4_a_seed_among_the_params_is_refused_as_a_second_seed(name, no_fit):
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="random_forest", params={name: 1})
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.SEED_MISSING
    assert name in out["reason"] and "sets the seed" in out["reason"]


# -- the work a caller can ask of G6's own process is bounded before anything is prepared -----------------------------

@pytest.fixture
def no_preparation(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records (and stops) any split, leakage probe or fit, so a test can show a refusal came before them."""
    started: list[str] = []

    def record(name: str):
        def stop(*args, **kwargs):
            started.append(name)
            raise AssertionError(f"{name} started")
        return stop

    for module, name in ((_types, "make_split"), (_types, "_leakage_findings"), (_fit, "run")):
        monkeypatch.setattr(module, name, record(name))
    return started


def test_g4_a_cell_of_a_million_characters_is_refused_before_anything_is_prepared(no_preparation):
    rows = churn()
    rows[3]["plan"] = "x" * 1_000_000
    out = _invoke("fit_eval", rows=rows, **CHURN, estimator="ridge")
    assert out["code"] == _types.TOO_LARGE and "10,000 characters" in out["reason"] and no_preparation == []


def test_g4_a_column_that_is_neither_the_target_nor_a_feature_is_refused_before_anything_is_prepared(no_preparation):
    rows = churn()
    rows[5]["notes"] = "called twice"
    for op in ("fit_eval", "rank"):
        out = _invoke(op, rows=rows, **CHURN, estimator="ridge")
        assert out["code"] == _types.INPUT_INVALID and "'notes'" in out["reason"] and no_preparation == [], op
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="ridge", predict_rows=[{"tenure": 3, "extra": 1}])
    assert out["code"] == _types.INPUT_INVALID and "'extra'" in out["reason"] and no_preparation == []


def test_g4_ten_thousand_classes_are_refused_before_anything_is_prepared(no_preparation):
    rows = [{"x": i % 97, "y": f"class {i // 2}"} for i in range(20_000)]
    out = _invoke("fit_eval", rows=rows, target="y", features=["x"], task="classification", seed=1,
                  estimator="naive_bayes")
    assert out["code"] == _types.TOO_LARGE and "10,000 classes" in out["reason"] and no_preparation == []


# -- the gates -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field", [{"completion_state": "verified"}, {"approved": True}, {"reviewer": "Dr Who"},
                                   {"held_out_score": 0.99}, {"test_rows": [0, 1]}, {"split": "given"},
                                   {"baseline": 0.0}])
def test_g3_claims_and_a_callers_split_are_refused(field):
    assert "G6_E_INPUT_VALIDATION" in _refused("fit_eval", rows=churn(), **CHURN, estimator="ridge", **field)


@needs_sklearn
def test_g4_a_fit_that_fails_or_runs_past_its_deadline_is_a_refusal_with_why():
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="k_neighbors", params={"n_neighbors": 200})
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.FIT_FAILED
    assert "n_neighbors" in out["reason"] or "n_samples" in out["reason"]
    out = _invoke("fit_eval", rows=churn(), **CHURN, estimator="random_forest", timeout_s=0.05)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.DEADLINE


def test_g4_a_missing_seed_or_a_second_one_is_refused(no_fit):
    assert _invoke("fit_eval", rows=churn(), **{**CHURN, "seed": None}, estimator="ridge")["code"] == \
        _types.SEED_MISSING
    assert _invoke("rank", rows=churn(), **CHURN, params={"alpha": 1})["code"] == _types.INPUT_INVALID


@needs_sklearn
def test_g5_no_op_is_sensitive_and_the_fit_reaches_no_network(monkeypatch):
    assert package.__g6_sensitive_ops__ == () and package.__g6_outside__ == ()

    def refused(*args, **kwargs):
        raise AssertionError("adapt_sklearn opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refused)
    out = _invoke("fit_eval", rows=prices(), target="price", features=["size", "rooms"], task="regression", seed=1,
                  estimator="linear_regression")
    assert out["completion_state"] == "qualified-draft"
    stated = _sandbox_limits()["os_sandbox"]  # true only where the operating system confined the run
    assert next(e for e in out["evidence"] if e["kind"] == "ml_fit")["limits"]["os_sandbox"] is stated


def _sandbox_limits() -> dict:
    """The limits g6.core.sandbox states for a run it starts on this machine: confined by the operating system where a
    backend can run (os_sandbox true, no network at all), else the interpreter's own guards."""
    from g6.core import sandbox

    return sandbox.run_script("").limits


def test_g5_the_run_evidence_states_the_sandboxs_own_limits_never_more():
    stated = _sandbox_limits()
    script = "import json\nopen('result.json', 'w').write(json.dumps({'held_out': []}))\n"
    limits = _fit.run({}, 60, script=script)["limits"]
    shared = ("network", "memory_cap", "os_sandbox")
    assert {k: limits.get(k) for k in shared} == {k: stated[k] for k in shared}
    assert "this machine" in limits["network_scope"] and "not an operating-system sandbox" in limits["network_scope"]


def test_g8_both_extras_declare_a_scikit_learn_the_code_runs_on():
    import tomllib

    pyproject = tomllib.loads((SRC.parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    for extra in ("ml", "automl"):
        declared = [re.fullmatch(r"scikit-learn>=(\d+)\.(\d+)[^,]*(,.*)?", d) for d in extras[extra]
                    if d.startswith("scikit-learn")]
        assert len(declared) == 1 and declared[0], (extra, extras[extra])
        assert (int(declared[0].group(1)), int(declared[0].group(2))) >= _fit.SKLEARN_FLOOR, (extra, extras[extra])


def test_g6_g7_the_package_reads_no_key_calls_no_model_and_holds_no_stand_in():
    for path in [*SRC.rglob("*.py"), *(SRC.parent / name for name in ("_fit.py", "_metrics.py", "_report.py",
                                                                      "_sources.py", "_types.py"))]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"getenv|os\.environ|API_KEY|litellm|openai|anthropic|g6\.core\.llm", text), path.name
        assert not re.search(r"(?i)mock|fake|simulat|import (pickle|joblib)|pickle\.loads?\(|joblib\.load|torch\.load",
                             text), path.name


# -- D2, D6, D9, vetting ----------------------------------------------------------------------------------------------

def test_d2_it_declares_what_it_guards_against_and_what_its_use_can_cause():
    assert package.__g6_mitigates__ == ("train_test_leakage", "ill_suited_model")
    assert package.__g6_introduces__ == ("unevaluated_model_shipped",)
    assert "g6.ml.adapt_sklearn" not in FailureModeRegistry.planned()


@needs_sklearn
def test_d6_found_by_its_failure_mode_described_as_listed_on_its_certificate_and_invoked_over_mcp():
    title = FailureModeRegistry.title("train_test_leakage")
    assert "adapt_sklearn" in [r["component"] for r in discovery.find(title, limit=5)]
    info = describe_reply("adapt_sklearn")
    assert info["family"] == "ml" and info["skill"] and info["sensitive_ops"] == []
    assert sorted(info["ops"]) == ["estimators", "fit_eval", "rank"]
    assert info["vetting"]["status"] == "reviewed"   # its certificate is current (DECISIONS #39: no person's review)
    reply = invoke_reply("adapt_sklearn", "fit_eval", json.dumps({"rows": churn(), **CHURN, "estimator": "ridge",
                                                                   "request_id": "q1", "run_id": "u1"}))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    assert (reply["result"]["request_id"], reply["result"]["run_id"]) == ("q1", "u1")


_WITHOUT = """
import json, random, sys
sys.modules["sklearn"] = None
from g6.core.registry import get_registry
reg = get_registry()
rng = random.Random(0)
rows = [{"x": rng.random(), "y": rng.choice(["a", "b"])} for _ in range(60)]
base = {"rows": rows, "target": "y", "features": ["x"], "task": "classification", "seed": 1}
fit = reg.invoke("adapt_sklearn", "fit_eval", {**base, "estimator": "ridge"}).unwrap()
listed = reg.invoke("adapt_sklearn", "estimators", {}).unwrap()
leak = reg.invoke("adapt_sklearn", "fit_eval", {**base, "features": ["x", "y"], "estimator": "ridge"}).unwrap()
print(json.dumps({"fit": [fit["code"], fit["reason"]], "listed": listed["warning_card"]["message"],
                  "leak": leak["code"], "imported": "sklearn" in sys.modules and sys.modules["sklearn"] is not None}))
"""


def test_d9_without_the_extra_a_fit_says_what_to_install_and_the_checks_still_run(tmp_path):
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          cwd=tmp_path, env={**os.environ, "G6_HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["fit"][0] == _types.BACKEND_NOT_INSTALLED and "pip install 'g6[ml]'" in found["fit"][1]
    assert "not installed" in found["listed"] and found["leak"] == _types.LEAKAGE and not found["imported"]
