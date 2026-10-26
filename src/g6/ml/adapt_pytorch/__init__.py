# SPDX-License-Identifier: Apache-2.0
"""g6.ml.adapt_pytorch — train a small neural network on rows it holds out itself, seeded, and stopped on a broken loss.

An extension in the ml family, behind the ``torch`` extra (PyTorch). Without it ``train_eval`` and ``predict`` say
PyTorch is not installed and how to install it; the checks before training, ``layers`` and ``models`` need nothing.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_pytorch", "train_eval", {
        "rows": rows, "target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7,
        "layers": [{"type": "dense", "units": 16, "activation": "relu"}]}).unwrap()
    out["headline"]["held_out"], out["headline"]["baseline"]   # measured on rows the training never saw answers for

What it does:

* ``train_eval`` builds a network from G6's curated layer list (``layers``: the archive's adapt_keras builder, merged
  in, with its scan for keys and text that could carry code), encodes the rows from the fitting rows only, and trains
  it in minibatches in G6's own child process, seeded, with early stopping on a validation part of the training rows;
  G6 then scores its predictions for the held-out rows against a baseline on the same rows, with the lift, the
  train-held-out gap and 95% intervals; gradient norms at the first and last step are reported;
* a loss that becomes infinite or not a number stops training at once and is refused (``G6_E_ML_NON_FINITE_LOSS``);
* with ``keep``, the weights are stored under ``G6_HOME`` with their hash, and ``predict`` uses them by the id G6
  gave, reading them back only with ``torch.load(..., weights_only=True)``; ``models`` lists them, and ``forget``
  (sensitive) deletes one; a full store refuses ``keep``, so a kept model is never removed silently.

The family's checks run first (``g6.ml._types.prepare``): a seed is required; the columns, the split and its size;
identical rows across it; target leakage. Results are ``qualified-draft``: a measurement on these rows, never
``verified``.

Not ported from the archive's adapt_pytorch, and why: the headline training accuracy (the held-out score heads every
result); the unseeded split; the stateless duplicate block; the Optuna tuner; transfer learning; TorchScript and ONNX
export (an exported file leaves G6's checks, and ONNX needs a second exporter package); the gradient check on random
inputs (the norms come from the real training steps instead); the SQLite store, the hosted-tier notes, the model
planner, the servers and the scaffold. From adapt_keras: the Keras backend, its loaders of caller files, its tuner,
KerasHub and its exports.
"""
from g6.ml.adapt_pytorch.block import SENSITIVE_OPS, AdaptPytorchBlock, PytorchInput
from g6.ml.adapt_pytorch.layers import LAYERS, NOT_FOR_TABLES

__g6_component__ = True
__g6_block__ = "AdaptPytorchBlock"
__g6_family__ = "ml"
# forget deletes a kept model (stored data), so it is sensitive. Training starts a child of G6's own interpreter through
# g6.core.sandbox (bounded, nothing fetched); keep writes only G6's own state, the weights G6's child wrote, under
# G6_HOME, and is refused when the store is full rather than removing anything; predict reads back only those weights,
# by the id G6 issued and with their hash checked.
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("training_loop_bug",)
__g6_introduces__: tuple[str, ...] = ("unevaluated_model_shipped",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"torch": ("torch",)}

__all__ = ["LAYERS", "NOT_FOR_TABLES", "SENSITIVE_OPS", "AdaptPytorchBlock", "PytorchInput"]
