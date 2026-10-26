# SPDX-License-Identifier: Apache-2.0
"""The search backends adapt_automl runs, registered by name, and the one it ships: FLAML's tuner.

A :class:`SearchBackend` is given a :class:`SearchJob`, the training part of the extension's own split (features,
answers and the group of each row) and the held-out rows' features without their answers, with the candidates, the
metric, the folds, the seed, a time budget and a trial cap; it returns a :class:`SearchOutcome`: predictions for the
held-out and training rows from the configuration it chose, its leaderboard of cross-validated scores on the training
part, and what ended the search. The block scores the predictions itself (``g6.ml._report``), so a backend cannot
grade its own work, and checks the outcome before it reports anything.

:data:`search_backends` is the registry. A name not registered, or registered but not ready, is refused with
``G6_E_ML_BACKEND_NOT_INSTALLED``, never swapped for another. :class:`FlamlBackend` runs FLAML's cost-frugal search
(CFO, seeded) over the candidates' ranges in a bounded child of G6's own interpreter (``g6.ml._fit.run``), with the
extension's own pipeline and folds; it needs the ``automl`` extra.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from g6.ml import _fit
from g6.ml._types import missing_extra

EXTRA = ("automl", ("flaml", "sklearn", "numpy"), "FLAML and scikit-learn")
#: Search engines the archive offered that G6 does not ship, and why (each is refused by name with this reason).
NOT_SHIPPED = {
    "autosklearn": "auto-sklearn is not part of G6: its only release (0.15.0) is source-only, its setup refuses every "
                   "system but Linux, and it pins scikit-learn below 0.25, which has no build for Python 3.12",
    "auto_sklearn": "auto-sklearn is not part of G6 (see autosklearn)",
    "mljar": "MLJAR is not part of G6 yet: its dependencies (xgboost, lightgbm, catboost, shap, matplotlib and more) "
             "are heavy, and xgboost's Linux build pulls NVIDIA's NCCL; a backend can be registered for it later",
    "h2o": "H2O is not part of G6: it starts a Java server of its own; a backend over its HTTP interface can be "
           "registered later, with Java as an outside program",
    "autogluon": "AutoGluon is not part of G6: it pulls PyTorch and a large tree of dependencies",
}
_BACKEND_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


@dataclass(frozen=True)
class SearchJob:
    """What a backend is given: the job payload (``g6.ml._fit.job``: the training part with its answers, the held-out
    features without theirs) and the deadline for the whole search."""

    payload: dict[str, Any]
    timeout_s: float


@dataclass
class SearchOutcome:
    """What a backend returns."""

    held_out: list[Any]
    train: list[Any]
    leaderboard: list[dict[str, Any]]
    chosen: str
    chosen_config: dict[str, Any]
    ended_by: str
    predict: list[Any] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SearchBackend(Protocol):
    """A search engine: its name, the extra that installs it, whether it is ready (``""`` when it is, otherwise why
    and how to install it), and :meth:`search`."""

    name: str
    extra: str

    def available(self) -> str: ...

    def search(self, job: SearchJob) -> SearchOutcome: ...


class SearchBackends:
    """The search backends registered by name."""

    def __init__(self) -> None:
        self._backends: dict[str, SearchBackend] = {}

    def register(self, backend: SearchBackend, *, replace: bool = False) -> None:
        if not isinstance(backend, SearchBackend):
            raise TypeError(f"{backend!r} is not a search backend (name, extra, available, search)")
        if not _BACKEND_NAME.fullmatch(str(backend.name)):
            raise ValueError(f"{backend.name!r} is not a backend name (lower-case letters, digits and '_')")
        if backend.name in self._backends and not replace:
            raise ValueError(f"a search backend named {backend.name!r} is already registered")
        self._backends[backend.name] = backend

    def unregister(self, name: str) -> None:
        self._backends.pop(name, None)

    def get(self, name: str) -> SearchBackend | None:
        return self._backends.get(name)

    def names(self) -> list[str]:
        return sorted(self._backends)

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": b.name, "extra": b.extra, "available": b.available()}
                for b in (self._backends[n] for n in self.names())]


#: The child script FLAML's search runs: G6's own, fixed.
FLAML_SCRIPT = "from g6.ml.adapt_automl.flaml_child import child_main\nchild_main()\n"
#: Seconds the child is given beyond the search's time budget, for starting, the last trial and the final refit.
MARGIN_S = 60.0


class FlamlBackend:
    """FLAML's cost-frugal search (CFO), seeded, over each candidate's ranges in turn, every trial cross-validated
    by the extension's own pipeline and folds inside the training part."""

    name = "flaml"
    extra = "automl"

    def available(self) -> str:
        return missing_extra(*EXTRA)

    def search(self, job: SearchJob) -> SearchOutcome:
        result = _fit.run(job.payload, job.timeout_s, script=FLAML_SCRIPT)
        return SearchOutcome(held_out=result.get("held_out", []), train=result.get("train", []),
                             leaderboard=result.get("leaderboard", []), chosen=result.get("chosen", ""),
                             chosen_config=result.get("chosen_config", {}), ended_by=result.get("ended_by", ""),
                             predict=result.get("predict", []),
                             details={k: result.get(k) for k in ("versions", "seconds", "warnings", "limits",
                                                                 "categories", "folds")})


search_backends = SearchBackends()
search_backends.register(FlamlBackend())

__all__ = ["EXTRA", "FLAML_SCRIPT", "MARGIN_S", "NOT_SHIPPED", "FlamlBackend", "SearchBackend", "SearchBackends",
           "SearchJob", "SearchOutcome", "search_backends"]
