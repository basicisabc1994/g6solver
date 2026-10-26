# SPDX-License-Identifier: Apache-2.0
"""Abstraction engineering for costly black-box search.

Capability label: ``implemented``. Pruning advice is quarantined: it is advisory,
off unless the caller acts on it, and refused for any entry whose evidence was not
re-established in this session.

Abstractions are validated combinator programs (``Quotient``, ``Project``, ``Sufficient``,
``Product``) fitted incrementally on an evaluation trace. Around them: a property-based gate
that fails closed on aborted checks, counterfactual replay that learns only from evaluated
points, a permutation order-sensitivity stress test (a heuristic, not a confidence bound), a
heuristic MDL score, CEGAR refinement, a persistent status-latticed library, guarded pruning
advice (``g6.abstraction.pruning``), the Lift functor, and bounded self-diagnosis.

Not established (audit 001): safe pruning on unseen configurations, calibrated confidence, or a
benefit over standard optimisers. The optional MCP server is ``g6.abstraction.server``
(``pip install g6[abstraction,mcp]``).

The public names are imported on first use (PEP 562), so ``import g6.abstraction`` works with only the core
installed and discovery answers; without numpy or Hypothesis a name raises an ``ImportError`` that says to
``pip install "g6[abstraction]"``.
"""
from importlib import import_module
from typing import Any

#: Each public name and the submodule (and attribute there) it comes from.
_LAZY: dict[str, tuple[str, str]] = {
    **{n: ("abstractions", n) for n in ("Abstraction", "Product", "Project", "Quotient", "SpecError", "Sufficient",
                                        "from_spec")},
    **{n: ("cegar", n) for n in ("TightenResult", "find_counterexamples", "refine_until", "tighten_until")},
    **{n: ("diagnostics", n) for n in ("Diagnosis", "Finding", "diagnose")},
    **{n: ("domain", n) for n in ("ConcreteDomain", "ParamSpec", "Record")},
    **{n: ("gate", n) for n in ("replay", "run_gate")},
    **{n: ("library", n) for n in ("Library", "LibraryEntry")},
    **{n: ("lift", n) for n in ("lift", "liftable", "meta_domain")},
    "mdl_score": ("mdl", "score"),
    **{n: ("robustness", n) for n in ("RobustReport", "bootstrap_replay")},
}
_EXTRA = ("numpy", "hypothesis")


def __getattr__(name: str) -> Any:
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _LAZY[name]
    try:
        value = getattr(import_module(f"{__name__}.{module}"), attribute)
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] not in _EXTRA:
            raise
        raise ImportError(f"g6.abstraction.{name} needs {exc.name}, which is not installed: "
                          f"pip install \"g6[abstraction]\"", name=exc.name) from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


__version__ = "0.3.0"
__all__ = ["Abstraction", "Quotient", "Project", "Sufficient", "Product",
           "SpecError", "from_spec", "ConcreteDomain", "ParamSpec", "Record",
           "run_gate", "replay", "mdl_score", "find_counterexamples",
           "refine_until", "tighten_until", "TightenResult",
           "Library", "LibraryEntry", "bootstrap_replay",
           "RobustReport", "diagnose", "Diagnosis", "Finding", "lift",
           "liftable", "meta_domain"]

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("blind_trial_and_error",)
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"abstraction": ("hypothesis", "numpy")}
