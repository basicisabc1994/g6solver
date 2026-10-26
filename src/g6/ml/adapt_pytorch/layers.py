# SPDX-License-Identifier: Apache-2.0
"""The layers a network is built from: a curated list, as data, and the scan that refuses anything that could carry
code.

The list is the archive's adapt_keras curated builder (``keras_mcp/model_lifecycle.py``, merged in): ``dense`` and
``dropout`` are built; its ``flatten``, ``lstm`` and ``conv2d`` need images or sequences, which a table of rows is not,
so they are refused with why. A layer is a JSON object such as ``{"type": "dense", "units": 32, "activation":
"relu"}``; G6 adds the output layer itself (one unit per class, or one for regression). Before a layer is read, every
key and text value is scanned (adapt_keras's ``safety.py``): a key such as ``lambda``, ``module`` or
``custom_objects``, or text such as ``__import__`` or ``eval(``, is refused as code, never interpreted.
"""
from __future__ import annotations

from typing import Any

from g6.ml._types import INPUT_INVALID, TOO_LARGE, UNTRUSTED_CODE, MLError

ACTIVATIONS = ("relu", "tanh", "sigmoid", "gelu", "elu", "leaky_relu", "linear")
#: Each layer type, its settings (kind, lowest, highest, default) and what it is in torch.
LAYERS: dict[str, dict[str, Any]] = {
    "dense": {"settings": {"units": ("int", 1, 512, 64), "activation": ("choice", ACTIVATIONS, None, "relu")},
              "torch": "torch.nn.Linear, then the activation (none for linear)"},
    "dropout": {"settings": {"rate": ("float", 0.0, 0.9, 0.1)},
                "torch": "torch.nn.Dropout, active while training only"},
}
#: adapt_keras's other curated layers, not built here, and why.
NOT_FOR_TABLES = {
    "flatten": "flattens an image or a sequence; rows of a table are flat already",
    "lstm": "reads a sequence; this extension takes a table of rows, not sequences",
    "conv2d": "reads an image; this extension takes a table of rows, not images",
}
MAX_LAYERS = 8
MAX_PARAMETERS = 1_000_000
#: Keys and text that name code (adapt_keras's FORBIDDEN_CONFIG_KEYS and its executable-text scan).
FORBIDDEN_KEYS = frozenset({"module", "callable", "function", "lambda", "code", "__class__", "__import__",
                            "custom_objects", "safe_mode", "class_name", "config", "registered_name"})
FORBIDDEN_TEXT = ("__import__", "lambda ", "lambda:", "eval(", "exec(", "compile(", "os.system", "subprocess")
DEFAULT = ({"type": "dense", "units": 64, "activation": "relu"},)


def scan(layers: list[dict[str, Any]]) -> None:
    """Refuse ``layers`` with ``G6_E_ML_UNTRUSTED_CODE`` when a key or a text value could carry code."""
    for i, layer in enumerate(layers):
        for key, value in layer.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise MLError(UNTRUSTED_CODE, f"layer {i} has the key {str(key)[:40]!r}, which names code; a layer is "
                                              "only a type and its settings from op layers")
            if isinstance(value, str) and any(t in value.lower() for t in FORBIDDEN_TEXT):
                raise MLError(UNTRUSTED_CODE, f"layer {i}, {key!r}: its text looks like code, which is never run")


def _setting(i: int, kind: str, name: str, spec: tuple, value: Any) -> Any:
    form, low, high, default = spec
    if value is None:
        return default
    if form == "choice":
        if value not in low:
            raise MLError(INPUT_INVALID, f"layer {i} ({kind}): {name} must be one of {', '.join(low)}")
        return value
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not number or (form == "int" and not float(value).is_integer()) or not low <= value <= high:
        raise MLError(INPUT_INVALID, f"layer {i} ({kind}): {name} must be {'a whole number' if form == 'int' else 'a number'}"
                                     f" between {low} and {high}")
    return int(value) if form == "int" else float(value)


def resolve(layers: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The layers as the child builds them, each with every setting filled in, or a refusal: code first
    (:func:`scan`), then an unknown type, a setting it does not have, or one out of range."""
    given = [dict(layer) for layer in (layers or DEFAULT)]
    scan(given)
    if len(given) > MAX_LAYERS:
        raise MLError(TOO_LARGE, f"{len(given)} layers; at most {MAX_LAYERS}")
    out = []
    for i, layer in enumerate(given):
        kind = str(layer.pop("type", "")).lower()
        if kind in NOT_FOR_TABLES:
            raise MLError(INPUT_INVALID, f"layer {i}: {kind} is not built here: it {NOT_FOR_TABLES[kind]}")
        if kind not in LAYERS:
            raise MLError(INPUT_INVALID, f"layer {i}: its type must be one of {', '.join(LAYERS)} (op layers), not "
                                         f"{kind[:40]!r}")
        settings = LAYERS[kind]["settings"]
        other = sorted(set(layer) - set(settings))
        if other:
            raise MLError(INPUT_INVALID, f"layer {i} ({kind}) has no setting {other[0][:40]!r}; it takes "
                                         f"{', '.join(settings)}")
        out.append({"type": kind, **{n: _setting(i, kind, n, s, layer.get(n)) for n, s in settings.items()}})
    if not any(layer["type"] == "dense" for layer in out):
        raise MLError(INPUT_INVALID, "a network needs at least one dense layer")
    return out


def parameters(layers: list[dict[str, Any]], inputs: int, outputs: int) -> int:
    """How many weights and biases the network holds, with G6's output layer."""
    total, width = 0, inputs
    for layer in layers:
        if layer["type"] == "dense":
            total += width * layer["units"] + layer["units"]
            width = layer["units"]
    return total + width * outputs + outputs


def describe() -> dict[str, Any]:
    """The curated list as the ``layers`` op reports it."""
    return {"layers": {k: {"settings": {n: {"kind": s[0], **({"choices": list(s[1])} if s[0] == "choice" else
                                                              {"lowest": s[1], "highest": s[2]}), "default": s[3]}
                                        for n, s in v["settings"].items()}, "torch": v["torch"]}
                       for k, v in LAYERS.items()},
            "not_built": NOT_FOR_TABLES, "default": list(DEFAULT), "max_layers": MAX_LAYERS,
            "max_parameters": MAX_PARAMETERS,
            "output_layer": "added by G6: one unit per class (softmax inside the cross-entropy loss), or one unit for "
                            "regression",
            "refused_as_code": sorted(FORBIDDEN_KEYS)}


__all__ = ["ACTIVATIONS", "DEFAULT", "FORBIDDEN_KEYS", "LAYERS", "MAX_LAYERS", "MAX_PARAMETERS", "NOT_FOR_TABLES",
           "describe", "parameters", "resolve", "scan"]
