---
name: g6-adapt-pytorch
description: Use when you need a small neural network trained on rows of data without writing the training loop yourself, and need its score measured on rows held out by the tool itself, against a baseline, with the run seeded and stopped the moment its loss breaks, so a training accuracy, a diverged loss or a leaky feature never passes for a result.
---

# G6 adapt_pytorch

`g6.ml.adapt_pytorch` builds a small network from G6's curated layer list, trains it in minibatches in a bounded
child process (a deadline; a memory cap of 1,536 MB, on the data segment on Linux so PyTorch's large libraries can
load while what it allocates stays capped, and none on macOS, which refuses it; and a guard that lets Python's sockets reach only this
machine: not an operating-system sandbox), and measures it on rows it held out itself. The child gets the training
answers, never the held-out ones; G6 scores what comes back against a baseline that ignores the features, on the
same rows. It is an extension in the ml family, behind the `torch` extra. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) are asked whether a neural network does better than a simple baseline on a table, and need
  a number you can defend rather than a training loop you wrote and scored yourself.
- You want the setup and loop mistakes taken out: no hand-written loop, a seed set once, early stopping on rows the
  test never sees, a stop on a loss that is infinite or not a number.
- You want to keep the trained network and ask it for predictions later.

## When not to use it

- Images, sequences or text models: the rows are a table; `lstm`, `conv2d` and `flatten` are refused.
- Large networks or data: at most 8 layers, 512 units a layer, 1,000,000 weights, 20,000 rows and 200 features,
  and 20,000,000 encoded values (the rows to train on, hold out and predict, times the encoded columns: one a
  number, two with a missing value, and up to 51 a text feature).
- When a plain model will do: try adapt_sklearn first; a network on a small table rarely beats it.
- Exporting a model to run elsewhere: there is no export; a kept model is used only through `predict`.

## Ops

- `train_eval`: `rows`, `target`, `features`, `task`, `seed`, and `layers` (default one dense layer of 64 units),
  `epochs` (1 to 500, default 200), `batch_size` (default 32), `learning_rate` (default 0.001), `weight_decay`,
  `optimiser` (`adam`, `adamw` or `sgd`), `patience` (0 to 100, default 20; 0 trains every epoch with no early
  stopping); optional `predict_rows`, `keep`, `held_out_fraction` and `timeout_s` (default 180, at most 600).
- `predict`: `model_id` (from `train_eval` with `keep`) and `predict_rows`.
- `models`: the kept models, newest first, at most 20; when 20 are kept, `keep` is refused before training, and
  nothing is removed to make room.
- `forget`: `model_id`; deletes that kept model's weights and record and checks both are gone (sensitive).
- `layers`: the curated layer types, their settings and ranges, and the keys refused as code.

A layer is data: `{"type": "dense", "units": 32, "activation": "relu"}` or `{"type": "dropout", "rate": 0.2}`; the
activations are `relu`, `tanh`, `sigmoid`, `gelu`, `elu`, `leaky_relu` and `linear`. G6 adds the output layer. Numbers
are imputed by the fitting rows' median (with a missing-value column) and standardised; text is one-hot encoded
with at most 50 values a column; both are fitted on the fitting rows only. Rows to predict are held to the kinds the
training rows set (a number or a boolean for a numeric feature, text for a text one; anything else, an infinite
number among them, is refused with `G6_E_ML_INPUT_INVALID`), and the evidence (`nn_predict_rows`) counts the values
filled in: missing ones, and text never seen among the fitting rows' categories.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_pytorch", "train_eval", '{"rows": [...], "target": "churned", ...}')`, then read
`result.headline`. `forget` deletes a kept model, so over MCP it runs only when the person who runs the server lists
`adapt_pytorch.forget` in `G6_MCP_SENSITIVE`; a model cannot delete what someone kept by asking. The other ops are
not sensitive: training starts a child of G6's own interpreter through `g6.core.sandbox`, and `keep` writes only
G6's own file under `G6_HOME`. From Python: `get_registry().invoke("adapt_pytorch", op, params)`.

Composed:

- adapt_pandas `load` gives rows typed by one rule; its `profile` names the mixed columns this refuses.
- adapt_sklearn `rank` on the same rows first: the held-out rows are the same (drawn from the rows' own hash), so
  the two headlines compare like for like, and each reply counts how often those rows were scored.

## Failure modes it catches

- `training_loop_bug` (setup or training-loop bugs in hand-written model code):
  - the loop is G6's own, seeded once (`seed` drives the weights, the minibatch order and the validation part, with
    deterministic algorithms), so the same request gives the same network;
  - a loss that becomes infinite or not a number stops training at once and is refused with
    `G6_E_ML_NON_FINITE_LOSS`; nothing is scored;
  - after training every weight must be finite (`G6_E_ML_NON_FINITE_WEIGHTS`: a last step can break them with no
    loss left to show it), and every output, a class's logits or a regression value, before it is decoded
    (`G6_E_ML_NON_FINITE_OUTPUT`); a network that broke is never scored, kept or used to predict;
  - a number beyond the range of the 32-bit floats the network computes in, as given or once standardised, is
    refused with `G6_E_ML_INPUT_OVERFLOW` before anything starts, and never blamed on the training;
  - early stopping watches a validation part of the training rows only (whole groups of identical rows, within each
    class), keeps the best epoch's weights, and never sees the held-out rows;
  - the headline is always the held-out score against a baseline; the training score is reported beside it, with the
    gap, and a network that memorises its rows is shown not to beat the baseline (`G6_E_ML_BELOW_BASELINE`);
  - the layers are a curated list: a key such as `lambda`, `module` or `custom_objects`, or text such as `__import__`,
    is refused with `G6_E_ML_UNTRUSTED_CODE` before anything starts;
  - gradient norms at the first and last step are reported, with any vanishing (below 1e-7) or exploding (above
    1e3) named in the message;
  - the family's checks run first: a missing seed, leakage, identical rows across the split and too few held-out
    rows are refused with their codes, as in adapt_sklearn;
  - a kept model is read back only by the id G6 gave, from G6's folder, with its hash checked and
    `torch.load(weights_only=True)`; changed weights are refused, never loaded; a full store refuses `keep`
    (`G6_E_ML_TOO_LARGE`), so no kept model is ever removed but by `forget`.

## Reading the result

- `headline`: the metric, `held_out`, `baseline`, `lift`, `train`, `gap`, `interval` and `clearly_better`.
- `history`: each epoch's training loss and validation loss.
- `model_id`: with `keep`, the id `predict` takes.
- `evidence`: the split, the leakage probes, the training (layers, weights, optimiser, epochs run, early stop, seed,
  versions, limits), the gradient norms, the scores and the rules applied with their sources.

## Blind spots

- `unevaluated_model_shipped`: a held-out score is one measurement on one split of these rows; a kept model is still
  unevaluated for new data, drift or other populations.
- A network that trains without breaking can still be poorly tuned: the result says whether it beats the baseline,
  not whether another setting would do better.
- The checks on the data are the family's, with their blind spots: a proxy known only after the outcome, a time order
  broken by a random split, nearly identical rows.
- Gradient norms are a note, never a refusal.

## What it needs

- `pip install "g6[torch]"` (PyTorch 2.6 or later: before 2.6, `torch.load(weights_only=True)`, which reads a kept
  model back, could be made to run code). Without it `train_eval` and `predict` are refused with
  `G6_E_ML_BACKEND_NOT_INSTALLED` and the install line; the checks before training, `layers` and `models` still
  answer. On Linux PyTorch's wheels bring triton (MIT) and NVIDIA's CUDA libraries, under NVIDIA's own licence.
- No model, no network access, no outside program; training runs in a child of G6's own Python, on the CPU.

## Examples

```g6-example op=layers
{}
```

```g6-example op=train_eval
{"rows": [{"tenure":5,"plan":"plus","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":38,"plan":"plus","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":26,"plan":"plus","churned":"no"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":14,"plan":"plus","churned":"yes"},{"tenure":35,"plan":"pro","churned":"no"},{"tenure":23,"plan":"pro","churned":"no"},{"tenure":40,"plan":"plus","churned":"no"},{"tenure":23,"plan":"plus","churned":"no"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":39,"plan":"plus","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":12,"plan":"plus","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":4,"plan":"plus","churned":"yes"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":10,"plan":"plus","churned":"yes"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":27,"plan":"pro","churned":"no"},{"tenure":20,"plan":"basic","churned":"yes"},{"tenure":36,"plan":"pro","churned":"no"},{"tenure":31,"plan":"pro","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":39,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":29,"plan":"plus","churned":"no"},{"tenure":6,"plan":"plus","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":33,"plan":"plus","churned":"no"},{"tenure":30,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":20,"plan":"plus","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"yes"},{"tenure":3,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"pro","churned":"no"},{"tenure":33,"plan":"pro","churned":"no"}], "target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7, "layers": [{"type": "dense", "units": 8, "activation": "relu"}], "epochs": 40, "learning_rate": 0.01, "predict_rows": [{"tenure": 3, "plan": "basic"}]}
```

```g6-example op=models
{}
```

`predict` takes only an id `train_eval` gave with `keep`; any other is refused with why (here, no such kept model):

```g6-example op=predict
{"model_id": "00000000000000000000000000000000", "predict_rows": [{"tenure": 3, "plan": "basic"}]}
```

From Python: a learning rate far too high breaks the loss, and training is refused, not scored.

```python
import random

from g6.core.registry import get_registry

reg = get_registry()
rng = random.Random(3)
rows = [{"size": round(s, 1), "price": round(2.5 * s + rng.gauss(0, 20))} for s in (rng.uniform(40, 200) for _ in range(80))]
ask = {"rows": rows, "target": "price", "features": ["size"], "task": "regression", "seed": 3, "epochs": 30,
       "layers": [{"type": "dense", "units": 64, "activation": "linear"}] * 3}
broken = reg.invoke("adapt_pytorch", "train_eval", {**ask, "optimiser": "sgd", "learning_rate": 1.0}).unwrap()
print(broken["warning_card"]["message"])
assert broken["code"] in ("G6_E_ML_NON_FINITE_LOSS", "G6_E_ML_BACKEND_NOT_INSTALLED")   # pip install "g6[torch]"

# keep a small network, then forget it: from Python, or over MCP when the server allows forget
small = {**ask, "layers": [{"type": "dense", "units": 8, "activation": "relu"}], "learning_rate": 0.01, "keep": True}
model_id = reg.invoke("adapt_pytorch", "train_eval", small).unwrap().get("model_id")
if model_id:
    gone = reg.invoke("adapt_pytorch", "forget", {"model_id": model_id}).unwrap()
    assert gone["forgotten"] == model_id
    assert model_id not in [m["model_id"] for m in reg.invoke("adapt_pytorch", "models", {}).unwrap()["models"]]
```

## The four open problems

- **Learning signals**: every result records the rows' hash, the held-out set's hash, the seed, the loss history
  and the held-out score with its baseline; a kept model's record keeps that score. Not yet: nothing learns from
  them.
- **Evaluators the model cannot influence**: the held-out answers never reach the training process; G6's own code
  computes every score, the baseline and the intervals; early stopping uses training rows only.
- **Transfer by failure mode**: the same checks (leakage, identical rows, imbalance, seed, held-out size) run here as
  for adapt_sklearn and adapt_automl, with the same codes, and the same held-out rows for the same data.
- **Enough held-out cases**: fewer than 10 independent held-out rows, or a class missing from either side, is
  refused, and every score carries its interval. Not yet: there is no fixed set of datasets with known scores; the
  tests use built data with known noise, leaks and diverging settings.
