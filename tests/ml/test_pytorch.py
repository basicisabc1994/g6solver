# SPDX-License-Identifier: Apache-2.0
"""adapt_pytorch (adapt_keras merged in): a network from G6's curated layer list, trained out of G6's process on the
extension's own training split, seeded, stopped on a broken loss, and scored by G6 on held-out rows the training
never saw the answers for (training_loop_bug). Kept weights are G6's own, read back only with weights_only=True."""
from __future__ import annotations

import importlib.util
import json
import math
import os
import random
import re
import socket
import string
import subprocess
import sys
from pathlib import Path

import pytest

import g6.ml.adapt_pytorch as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.ml import _fit, _types
from g6.ml.adapt_pytorch import block, encoding, kept, layers, torch_child

SRC = Path(package.__file__).parent
needs_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None,
                                 reason="PyTorch (the torch extra) is not installed")
SMALL = [{"type": "dense", "units": 8, "activation": "relu"}]


def churn(n: int = 120, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        churned = rng.random() < 0.4
        rows.append({"tenure": round(rng.gauss(10 if churned else 30, 8), 2),
                     "plan": rng.choice(["basic", "plus"] if churned else ["plus", "pro"]),
                     "churned": "yes" if churned else "no"})
    return rows


def prices(n: int = 100, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    return [{"size": round(s, 1), "rooms": r, "price": round(2.5 * s + 10 * r + rng.gauss(0, 30))}
            for s, r in ((rng.uniform(40, 200), rng.randint(1, 6)) for _ in range(n))]


CHURN = {"target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7}
PRICES = {"target": "price", "features": ["size", "rooms"], "task": "regression", "seed": 3}


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_pytorch", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_pytorch", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


@pytest.fixture
def no_fit(monkeypatch: pytest.MonkeyPatch):
    def started(*args, **kwargs):
        raise AssertionError("training started")

    monkeypatch.setattr(_fit, "run", started)


@pytest.fixture(autouse=True)
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    return tmp_path


# -- D1: a loss that breaks stops training and is refused, never scored ------------------------------------------------

@needs_torch
@pytest.mark.parametrize("ask", [
    {"rows": churn(), **CHURN, "learning_rate": 1.0, "optimiser": "sgd", "epochs": 30,
     "layers": [{"type": "dense", "units": 64, "activation": "relu"}] * 3},
    {"rows": prices(), **PRICES, "learning_rate": 1.0, "optimiser": "sgd", "epochs": 30,
     "layers": [{"type": "dense", "units": 64, "activation": "linear"}] * 3},
])
def test_d1_a_loss_that_becomes_infinite_or_not_a_number_stops_training_and_is_refused(ask):
    out = _invoke("train_eval", **ask)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.NON_FINITE_LOSS, out.get("reason")
    assert "nothing was scored" in out["reason"] and "headline" not in out
    assert next(e for e in out["evidence"] if e["kind"] == "nn_training")["stopped"] == "non-finite loss"


# -- ADAPT_PYTORCH-01 / ADAPT_KERAS-02: the headline is held out, never the training accuracy --------------------------

@needs_torch
def test_p1_a_network_that_memorises_its_training_rows_is_headlined_by_its_held_out_score():
    rng = random.Random(5)
    rows = [{"a": rng.random(), "b": rng.random(), "y": rng.choice(["p", "q"])} for _ in range(120)]
    out = _invoke("train_eval", rows=rows, target="y", features=["a", "b"], task="classification", seed=2,
                  layers=[{"type": "dense", "units": 128, "activation": "relu"}] * 2, epochs=300, patience=0,
                  learning_rate=0.01)
    assert out["completion_state"] == "qualified-draft" and check_envelope(out) == []
    h = out["headline"]
    assert h["train"] >= 0.9 and h["held_out"] < 0.75 and h["held_out_rows"] == out["split"]["held_out_rows"]
    assert out["warning_card"]["code"] == _types.BELOW_BASELINE
    message = out["warning_card"]["message"]
    assert message.index("held-out rows") < message.index("on its training rows") and "far better" in message
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


@needs_torch
def test_regression_is_scored_by_r_squared_and_the_same_seed_gives_the_same_network(monkeypatch):
    given = []
    real = _fit.run

    def spy(payload, timeout_s, **kw):
        given.append(payload)
        return real(payload, timeout_s, **kw)

    monkeypatch.setattr(_fit, "run", spy)
    ask = {"rows": prices(), **PRICES, "layers": [{"type": "dense", "units": 16, "activation": "relu"}],
           "epochs": 150, "learning_rate": 0.01, "predict_rows": [{"size": 120, "rooms": 3}]}
    one, two = _invoke("train_eval", **ask), _invoke("train_eval", **ask)
    assert one["warning_card"]["code"] == _types.MEASURED and one["headline"]["metric"] == "r2"
    assert one["headline"]["held_out"] > 0.6 and one["headline"]["clearly_better"]
    assert one["history"] == two["history"] and one["predictions"] == two["predictions"]
    payload = given[0]
    assert "y_held_out" not in json.dumps(payload) and len(payload["X_held_out"]) == one["split"]["held_out_rows"]
    assert payload["valid"] and set(payload["valid"]) <= set(range(len(payload["X_train"])))
    training = next(e for e in one["evidence"] if e["kind"] == "nn_training")
    assert training["validation_rows"] == len(payload["valid"]) and training["validation_from"].startswith(
        "the training rows only")


# -- ADAPT_KERAS-01: one load path, G6's own file by G6's id, its hash checked -----------------------------------------

@needs_torch
def test_p1_a_kept_model_is_loaded_only_by_its_id_from_g6s_folder_and_only_unchanged(home):
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL, epochs=40, learning_rate=0.01, keep=True,
                  predict_rows=[{"tenure": 3, "plan": "basic"}, {"tenure": 40, "plan": "pro"}])
    model_id = out["model_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", model_id) and [m["model_id"] for m in _invoke("models")["models"]] == [model_id]
    again = _invoke("predict", model_id=model_id, predict_rows=[{"tenure": 3, "plan": "basic"},
                                                               {"tenure": 40, "plan": "pro"}])
    assert again["warning_card"]["code"] == _types.PREDICTED and again["predictions"] == out["predictions"]
    for bad in ("../" + model_id, str(home / "ml" / "adapt_pytorch" / "models" / f"{model_id}.pt"), "x" * 32):
        refused = _invoke("predict", model_id=bad, predict_rows=[{"tenure": 3, "plan": "basic"}])
        assert refused["code"] == _types.INPUT_INVALID, bad
    weights = home.joinpath(*kept.FOLDER, f"{model_id}.pt")
    weights.write_bytes(weights.read_bytes()[:-8] + b"tampered")
    refused = _invoke("predict", model_id=model_id, predict_rows=[{"tenure": 3, "plan": "basic"}])
    assert refused["code"] == _types.UNTRUSTED_CODE and "never loaded" in refused["reason"]


# -- one set of kind rules: rows to predict are held to the family's rules, as the training rows were ----------------

@needs_torch
def test_a_boolean_feature_is_taken_by_predict_as_training_took_it():
    rng = random.Random(1)
    rows = [{**row, "promo": rng.random() < 0.5} for row in churn()]
    features = ["tenure", "plan", "promo"]
    ask = [{f: row[f] for f in features} for row in rows[:6]]
    out = _invoke("train_eval", rows=rows, **{**CHURN, "features": features}, layers=SMALL, epochs=10,
                  learning_rate=0.01, keep=True, predict_rows=ask)
    again = _invoke("predict", model_id=out["model_id"], predict_rows=ask)
    assert again["warning_card"]["code"] == _types.PREDICTED, again.get("reason")
    assert again["predictions"] == out["predictions"]
    for cell in ({"tenure": "inf"}, {"tenure": "abc"}):
        refused = _invoke("predict", model_id=out["model_id"], predict_rows=[{**ask[0], **cell}])
        assert refused["code"] == _types.INPUT_INVALID and "row 0 to predict" in refused["reason"], cell


@pytest.mark.parametrize("cell", [{"tenure": "inf"}, {"tenure": "abc"}, {"tenure": "12"}, {"tenure": math.inf},
                                  {"plan": 5}, {"plan": True}])
def test_a_row_to_predict_is_held_to_the_kinds_the_training_rows_set(cell, no_fit):
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL,
                  predict_rows=[{"tenure": 3, "plan": "basic"}, {"tenure": 3, "plan": "basic", **cell}])
    assert out["code"] == _types.INPUT_INVALID and "row 1 to predict" in out["reason"], out.get("reason")


@needs_torch
def test_an_unseen_category_or_a_missing_value_to_predict_is_counted_in_the_evidence():
    ask = [{"tenure": 3, "plan": "gold"}, {"tenure": None, "plan": "basic"}, {"tenure": 30, "plan": "pro"}]
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL, epochs=5, keep=True, predict_rows=ask)
    note = next(e for e in out["evidence"] if e["kind"] == "nn_predict_rows")
    assert (note["rows"], note["unseen_categories"], note["missing_imputed"]) == (3, 1, 1)
    again = _invoke("predict", model_id=out["model_id"], predict_rows=ask)
    assert next(e for e in again["evidence"] if e["kind"] == "nn_predict_rows") == note


# -- numbers that overflow: refused before the child, and a network that breaks is never scored ------------------------

@needs_torch
@pytest.mark.parametrize("big", [1e39, 1e100])
def test_a_number_beyond_float32_to_predict_is_refused_by_kept_models_with_no_predictions(big):
    kept_models = {
        "classification": (_invoke("train_eval", rows=churn(), **CHURN, layers=SMALL, epochs=5, keep=True)["model_id"],
                           {"tenure": big, "plan": "basic"}),
        "regression": (_invoke("train_eval", rows=prices(), **PRICES, layers=SMALL, epochs=5, keep=True)["model_id"],
                       {"size": big, "rooms": 3})}
    for task, (model_id, row) in kept_models.items():
        out = _invoke("predict", model_id=model_id, predict_rows=[row])
        assert out["code"] == _types.INPUT_OVERFLOW and "predictions" not in out, (task, out.get("reason"))
        assert "32-bit" in out["reason"] and "learning_rate" not in out["warning_card"]["suggested_action"]


def test_a_number_beyond_float32_among_the_rows_to_predict_is_never_blamed_on_the_training(monkeypatch, no_fit):
    monkeypatch.setattr(block, "_installed", lambda: None)
    out = _invoke("train_eval", rows=prices(), **PRICES, layers=SMALL, predict_rows=[{"size": 1e39, "rooms": 3}])
    assert out["code"] == _types.INPUT_OVERFLOW and "row 0 to predict" in out["reason"], out.get("reason")


@needs_torch
@pytest.mark.parametrize(("task", "seed", "scale", "activation", "caught"), [
    ("classification", 1, 1e20, "relu", "non_finite_output"),   # finite weights, logits past float32
    ("regression", 2, 1.3e20, "linear", "non_finite_weights")])  # the last step's gradient overflows the weights
def test_a_last_step_that_breaks_the_network_is_refused_never_scored(task, seed, scale, activation, caught, tmp_path,
                                                                     monkeypatch):
    monkeypatch.chdir(tmp_path)
    rng = random.Random(seed)
    signs = [[rng.choice((-1.0, 1.0)), rng.choice((-1.0, 1.0))] for _ in range(20)]
    y = [rng.randrange(2) for _ in range(20)] if task == "classification" else [0.0] * 20
    X = [[a * scale, b * scale] for a, b in signs]
    outputs = 2 if task == "classification" else 1
    out = torch_child.train({"kind": "train", "task": task, "seed": seed, "outputs": outputs,
                             "layers": [{"type": "dense", "units": 8, "activation": activation}], "optimiser": "sgd",
                             "learning_rate": 1.0, "weight_decay": 0.0, "epochs": 1, "batch_size": 20, "patience": 0,
                             "valid": [], "X_train": X, "y_train": y, "X_held_out": X[:10], "X_predict": []})
    assert caught in out and "held_out" not in out and "non_finite" not in out, sorted(out)


@pytest.mark.parametrize(("child", "code", "said"), [
    ({"non_finite_weights": ["1.weight"]}, _types.NON_FINITE_WEIGHTS, "1.weight"),
    ({"non_finite_output": {"part": "held_out", "rows": 3, "of": 10}}, _types.NON_FINITE_OUTPUT,
     "3 of the 10 held-out")])
def test_a_broken_network_the_child_reports_is_refused_with_its_own_code(child, code, said, monkeypatch):
    monkeypatch.setattr(block, "_installed", lambda: None)
    monkeypatch.setattr(_fit, "run", lambda *args, **kwargs: {**child, "history": [{"epoch": 1, "train_loss": 0.5}]})
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL)
    assert out["code"] == code and said in out["reason"] and "headline" not in out, out.get("reason")


# -- keep never removes a model silently; forget removes one by its id, and is sensitive ------------------------------

def _kept(n: int) -> list[str]:
    """``n`` models in G6's own store (stand-in weights: nothing here loads them), oldest first."""
    return [kept.keep(f"weights {k}".encode(), {"task": "regression", "features": ["size"]}) for k in range(n)]


def test_a_full_store_refuses_keep_and_every_kept_model_is_still_there(no_fit):
    ids = _kept(kept.MAX_KEPT)
    before = sorted(m["model_id"] for m in kept.listed())
    with pytest.raises(_types.MLError) as refused:
        kept.keep(b"one more", {"task": "regression"})
    assert refused.value.code == _types.TOO_LARGE and "forget" in refused.value.reason
    out = _invoke("train_eval", rows=prices(), **PRICES, layers=SMALL, keep=True)
    assert out["code"] == _types.TOO_LARGE and f"at most {kept.MAX_KEPT}" in out["reason"], out.get("reason")
    assert sorted(m["model_id"] for m in kept.listed()) == before == sorted(ids)


def test_g5_forget_is_refused_over_mcp_and_deletes_nothing():
    (model_id,) = _kept(1)
    assert "forget" in package.__g6_sensitive_ops__
    denied = get_registry().invoke("adapt_pytorch", "forget", {"model_id": model_id}, block_sensitive=True)
    assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(denied.failure())
    reply = invoke_reply("adapt_pytorch", "forget", json.dumps({"model_id": model_id}))
    assert not reply["ok"] and reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert [m["model_id"] for m in kept.listed()] == [model_id]


def test_forget_from_python_deletes_exactly_that_model(home):
    first, middle, last = _kept(3)
    out = _invoke("forget", model_id=middle)
    assert out["completion_state"] == "qualified-draft" and out["forgotten"] == middle, out.get("reason")
    assert sorted(m["model_id"] for m in kept.listed()) == sorted([first, last])
    assert not list(home.joinpath(*kept.FOLDER).glob(f"{middle}.*"))
    for bad in (middle, "../" + first, "x" * 32):
        assert _invoke("forget", model_id=bad)["code"] == _types.INPUT_INVALID, bad
    assert sorted(m["model_id"] for m in kept.listed()) == sorted([first, last])


@pytest.mark.parametrize("field", [{"model_path": "m.pt"}, {"weights": "gASV"}, {"checkpoint": "x.pt"},
                                   {"export_path": "out.onnx"}, {"completion_state": "verified"}, {"approved": True},
                                   {"held_out_score": 0.99}, {"split": "given"}])
def test_g3_p1_there_is_no_way_to_hand_in_a_model_a_path_a_split_or_a_claim(field):
    assert "G6_E_INPUT_VALIDATION" in _refused("train_eval", rows=churn(), **CHURN, **field)


# -- G2: the layers are data; anything that could carry code is refused before anything starts -------------------------

@pytest.mark.parametrize("layer", [{"type": "dense", "lambda": "x"}, {"type": "dense", "custom_objects": "Mine"},
                                   {"type": "dense", "activation": "__import__('os').system('calc')"},
                                   {"type": "dense", "class_name": "Lambda"}, {"module": "os", "type": "dense"}])
def test_g2_a_layer_that_could_carry_code_is_refused_and_nothing_starts(layer, no_fit):
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=[layer])
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.UNTRUSTED_CODE


def test_layers_outside_the_curated_list_or_its_ranges_are_refused_with_why(no_fit):
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=[{"type": "lstm", "units": 8}])
    assert out["code"] == _types.INPUT_INVALID and "sequence" in out["reason"]
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=[{"type": "dense", "units": 5000}])
    assert out["code"] == _types.INPUT_INVALID and "between 1 and 512" in out["reason"]
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=[{"type": "dense", "units": 512}] * 8)
    assert out["code"] == _types.TOO_LARGE and "1,000,000" in out["reason"]
    assert layers.parameters([{"type": "dense", "units": 8, "activation": "relu"}], 3, 2) == 3 * 8 + 8 + 8 * 2 + 2


# -- the bounded child: torch's libraries load under its cap, and a torch that cannot load is a named refusal ---------

def test_the_torch_child_is_capped_on_its_data_segment_at_the_stated_size(monkeypatch):
    from g6.core import sandbox

    asked = []

    def spy(source, files=None, **kwargs):
        asked.append(kwargs)
        return sandbox.ScriptRun("refused", None, "", "stopped by the test")

    monkeypatch.setattr(block, "_installed", lambda: None)
    monkeypatch.setattr(sandbox, "run_script", spy)
    _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL, epochs=1)
    assert asked and asked[0]["memory"] == "data" and asked[0]["mem_mb"] == _fit.MEMORY_MB == 1536


def test_a_torch_that_cannot_be_loaded_in_the_child_is_a_named_refusal_not_a_stderr_tail(monkeypatch):
    monkeypatch.setattr(block, "_installed", lambda: None)
    monkeypatch.setattr(torch_child, "SCRIPT", "import sys\nsys.modules['torch'] = None\n" + torch_child.SCRIPT)
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL, epochs=1)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.FIT_FAILED, out.get("reason")
    assert "torch could not be loaded in the bounded child" in out["reason"] and "1,536 MB" in out["reason"]
    assert "ended without a result" not in out["reason"]


def test_an_encoding_too_large_for_the_child_is_refused_before_any_row_is_encoded(monkeypatch):
    # 200 text columns of 52 one-character values: within every cap on rows, features, text and weights, but each
    # column encodes to 51 (50 values and the infrequent), so 2,100 rows are 2,100 x 10,200 = 21,420,000 values
    rng = random.Random(4)
    features = [f"c{k}" for k in range(200)]
    rows = [{**{f: rng.choice(string.ascii_letters) for f in features}, "y": rng.choice("ab")} for _ in range(2_100)]

    def refused(*args, **kwargs):
        raise AssertionError("a row was encoded or the child was started")

    monkeypatch.setattr(encoding.Encoding, "row", refused)
    monkeypatch.setattr(_fit, "run", refused)
    out = _invoke("train_eval", rows=rows, target="y", features=features, task="classification", seed=1,
                  layers=SMALL)
    assert out["code"] == _types.TOO_LARGE, out.get("reason")
    assert "21,420,000" in out["reason"] and f"{encoding.MAX_VALUES:,}" == "20,000,000" in out["reason"]


# -- the gates -------------------------------------------------------------------------------------------------------

def test_g4_a_missing_seed_or_leakage_is_refused_before_training(no_fit):
    assert _invoke("train_eval", rows=churn(), **{**CHURN, "seed": None})["code"] == _types.SEED_MISSING
    rows = prices()
    for row in rows:
        row["listed"] = row["price"] + 25
    out = _invoke("train_eval", rows=rows, **{**PRICES, "features": ["size", "rooms", "listed"]})
    assert out["code"] == _types.LEAKAGE and "listed" in out["reason"]


@needs_torch
def test_g4_g5_training_past_its_deadline_is_stopped_and_no_connection_is_opened(monkeypatch):
    assert package.__g6_sensitive_ops__ == ("forget",) and package.__g6_outside__ == ()

    def refused(*args, **kwargs):
        raise AssertionError("adapt_pytorch opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refused)
    out = _invoke("train_eval", rows=churn(), **CHURN, layers=SMALL, timeout_s=0.5)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == _types.DEADLINE


def test_g8_the_torch_extra_declares_a_torch_whose_weights_only_load_holds():
    # CVE-2025-32434: before torch 2.6.0, torch.load(weights_only=True), which predict uses, could be made to run code
    import tomllib

    pyproject = tomllib.loads((SRC.parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    extra = pyproject["project"]["optional-dependencies"]["torch"]
    declared = [re.fullmatch(r"torch>=(\d+)\.(\d+)[^,]*(,.*)?", d) for d in extra if d.startswith("torch")]
    assert len(declared) == 1 and declared[0], extra
    assert (int(declared[0].group(1)), int(declared[0].group(2))) >= torch_child.TORCH_FLOOR == (2, 6), extra


def test_g6_g7_the_package_reads_no_key_calls_no_model_holds_no_stand_in_and_loads_weights_only():
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"getenv|os\.environ|API_KEY|litellm|openai|anthropic|g6\.core\.llm", text), path.name
        assert not re.search(r"(?i)mock|fake|simulat|import (pickle|joblib)|pickle\.loads?\(", text), path.name
        for call in re.findall(r"torch\.load\([^)]*\)", text):
            assert "weights_only=True" in call, (path.name, call)


# -- D2, D6, D9 ------------------------------------------------------------------------------------------------------

def test_d2_it_declares_what_it_guards_against_and_what_its_use_can_cause():
    assert package.__g6_mitigates__ == ("training_loop_bug",)
    assert package.__g6_introduces__ == ("unevaluated_model_shipped",)
    assert "g6.ml.adapt_pytorch" not in FailureModeRegistry.planned()


def test_d8_the_owner_has_ruled_on_the_cuda_wheels_so_nothing_holds_it_off_the_library():
    # owner, 2026-10-21: every extra that pulls PyTorch may carry NVIDIA's CUDA wheels on Linux under a waiver
    from g6.core import vetting

    assert not hasattr(package, "__g6_awaiting_ruling__") and not hasattr(vetting, "awaiting_ruling")
    assert not vetting.awaiting_review("g6.ml.adapt_pytorch") and vetting.status("g6.ml.adapt_pytorch") is None


@needs_torch
def test_d6_found_by_its_failure_mode_described_and_invoked_over_mcp():
    title = FailureModeRegistry.title("training_loop_bug")
    assert "adapt_pytorch" in [r["component"] for r in discovery.find(title, limit=5)]
    info = describe_reply("adapt_pytorch")
    assert info["family"] == "ml" and info["skill"] and info["sensitive_ops"] == ["forget"]
    assert sorted(info["ops"]) == ["forget", "layers", "models", "predict", "train_eval"]
    reply = invoke_reply("adapt_pytorch", "train_eval", json.dumps({"rows": churn(), **CHURN, "layers": SMALL,
                                                                    "epochs": 20, "request_id": "q1", "run_id": "u1"}))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    assert (reply["result"]["request_id"], reply["result"]["run_id"]) == ("q1", "u1")


_WITHOUT = """
import json, random, sys
sys.modules["torch"] = None
from g6.core.registry import get_registry
reg = get_registry()
rng = random.Random(0)
rows = [{"x": rng.random(), "y": rng.choice(["a", "b"])} for _ in range(60)]
base = {"rows": rows, "target": "y", "features": ["x"], "task": "classification", "seed": 1}
fit = reg.invoke("adapt_pytorch", "train_eval", base).unwrap()
listed = reg.invoke("adapt_pytorch", "layers", {}).unwrap()
leak = reg.invoke("adapt_pytorch", "train_eval", {**base, "features": ["x", "y"]}).unwrap()
print(json.dumps({"fit": [fit["code"], fit["reason"]], "listed": listed["warning_card"]["message"],
                  "leak": leak["code"]}))
"""


def test_d9_without_the_extra_training_says_what_to_install_and_the_checks_still_run(tmp_path):
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          cwd=tmp_path, env={**os.environ, "G6_HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["fit"][0] == _types.BACKEND_NOT_INSTALLED and "pip install 'g6[torch]'" in found["fit"][1]
    assert "not installed" in found["listed"] and found["leak"] == _types.LEAKAGE
