# SPDX-License-Identifier: Apache-2.0
"""The second engine: DoWhy (``pip install 'g6[causal]'``), imported only when an op runs.

G6 hands DoWhy a networkx graph it builds itself and a DataFrame it builds from the caller's rows, never text: DoWhy
reads a string graph as a file path or through pydot, and neither is used. Every DoWhy call runs inside
:func:`session`, which

* records every Python warning DoWhy raises, for the card, except the two it raises on every call: pandas'
  ``Pandas4Warning`` that the ``copy`` keyword is deprecated, and DoWhy's ``UserWarning`` that graph nodes missing from
  the data are assumed unobserved (G6 marks unobserved nodes itself). Those two are filtered here, only around the
  calls, with :func:`warnings.catch_warnings`, never globally;
* keeps DoWhy's log warnings off the console and in the evidence;
* when given a seed, seeds numpy's global generator for DoWhy's bootstrap and restores the generator's state afterwards,
  so a result can be repeated without the call changing the caller's random state.

Neither ``catch_warnings`` nor the global generator is thread-safe; concurrent DoWhy calls in one process may see each
other's warnings and seeds.
"""
from __future__ import annotations

import contextlib
import importlib
import logging
import warnings
from collections.abc import Iterator
from pathlib import PurePath
from typing import Any

from g6.causal.envelope import ENGINE_UNAVAILABLE, INSTALL, Refusal
from g6.causal.graph import CausalGraph

_COPY_KEYWORD = r"The copy keyword is deprecated"
_ASSUMED_UNOBSERVED = r"\d+ variables are assumed unobserved"


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - ImportError, or a dependency failing as it loads
        raise Refusal(ENGINE_UNAVAILABLE, f"DoWhy is not importable here ({type(exc).__name__}: {exc}"[:300]
                      + f"); install it with {INSTALL}") from None


def load() -> Any:
    """The ``dowhy`` module; a refusal with ``G6_E_CAUSAL_ENGINE_UNAVAILABLE`` when it cannot be imported. Call it
    inside :func:`session`, so warnings DoWhy raises while it is first imported are recorded too."""
    return _module("dowhy")


def available() -> bool:
    try:
        load()
    except Refusal:
        return False
    return True


def version() -> str:
    return str(getattr(load(), "__version__", "unknown"))


def _where(filename: str) -> str:
    """A warning's file without the machine's own directories: from the package folder down."""
    parts = PurePath(filename).parts
    if "site-packages" in parts:
        return "/".join(parts[parts.index("site-packages") + 1:])
    return parts[-1] if parts else ""


class _Records(logging.Handler):
    def __init__(self, into: list[dict[str, Any]]) -> None:
        super().__init__(level=logging.WARNING)
        self.into = into

    def emit(self, record: logging.LogRecord) -> None:
        self.into.append({"level": record.levelname, "logger": record.name, "message": record.getMessage()[:300]})


@contextlib.contextmanager
def session(seed: int | None = None, *, seen: list[dict[str, Any]] | None = None,
            logged: list[dict[str, Any]] | None = None) -> Iterator[list[dict[str, Any]]]:
    """Run DoWhy calls with their warnings recorded into ``seen`` (deduplicated, counted) and their log warnings into
    ``logged``; the two known warnings are filtered; numpy's global generator is seeded with ``seed`` and restored."""
    seen = [] if seen is None else seen
    logged = [] if logged is None else logged
    logger = logging.getLogger("dowhy")
    handler, propagate = _Records(logged), logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    numpy, state = None, None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.filterwarnings("ignore", message=_ASSUMED_UNOBSERVED, category=UserWarning)
        warnings.filterwarnings("ignore", message=_COPY_KEYWORD, category=DeprecationWarning)
        try:
            if seed is not None:
                numpy = importlib.import_module("numpy")
                state = numpy.random.get_state()
                numpy.random.seed(seed)
            yield seen
        finally:
            if numpy is not None and state is not None:
                numpy.random.set_state(state)
            logger.removeHandler(handler)
            logger.propagate = propagate
            counted: dict[tuple[str, str, str], int] = {}
            for w in caught:
                key = (w.category.__name__, str(w.message)[:300], _where(w.filename))
                counted[key] = counted.get(key, 0) + 1
            seen.extend({"category": c, "message": m, "where": f, "count": n} for (c, m, f), n in counted.items())


def nx_graph(graph: CausalGraph) -> Any:
    """``graph`` as the networkx ``DiGraph`` DoWhy takes, every node included (unobserved ones too)."""
    networkx = _module("networkx")
    built = networkx.DiGraph()
    built.add_nodes_from(graph.nodes)
    built.add_edges_from(graph.edges)
    return built


def model(graph: CausalGraph, frame: Any, treatment: str, outcome: str) -> Any:
    """A DoWhy ``CausalModel`` over ``frame``: its columns are what DoWhy treats as observed."""
    return load().CausalModel(data=frame, treatment=treatment, outcome=outcome, graph=nx_graph(graph))


def summarise(estimand: Any) -> dict[str, Any]:
    """What DoWhy's identified estimand says, as plain data."""
    estimands = dict(getattr(estimand, "estimands", None) or {})
    backdoor_variables = dict(getattr(estimand, "backdoor_variables", None) or {})
    no_path = bool(getattr(estimand, "no_directed_path", False))
    found = {key: value is not None for key, value in estimands.items()}
    sets = [sorted(v) for k, v in backdoor_variables.items() if k != "backdoor" and v is not None]
    default = backdoor_variables.get("backdoor")
    return {
        "no_directed_path": no_path,
        "identifiable": no_path or any(found.get(k) for k in ("backdoor", "iv", "frontdoor", "general_adjustment")),
        "backdoor_found": bool(found.get("backdoor")),
        "backdoor_sets": [list(s) for s in dict.fromkeys(tuple(s) for s in sets)],
        "default_backdoor": sorted(default) if found.get("backdoor") and default is not None else None,
        "instruments": sorted(getattr(estimand, "instrumental_variables", None) or []),
        "frontdoor": sorted(getattr(estimand, "frontdoor_variables", None) or []),
        "general_adjustment": bool(found.get("general_adjustment")),
    }


def identification(graph: CausalGraph, treatment: str, outcome: str) -> dict[str, Any]:
    """DoWhy's own identification (``CausalModel.identify_effect``) of ``graph``, with no data: the frame is empty and
    has a column for each observed node."""
    pandas = _module("pandas")
    built = model(graph, pandas.DataFrame(columns=list(graph.observed)), treatment, outcome)
    return summarise(built.identify_effect())


def _graph_module() -> Any:
    return _module("dowhy.graph")


def d_separated(graph: CausalGraph, xs: list[str], ys: list[str], given: list[str]) -> bool:
    return bool(_graph_module().check_dseparation(nx_graph(graph), list(xs), list(ys), list(given)))


def backdoor_paths(graph: CausalGraph, treatment: str, outcome: str) -> list[list[str]]:
    return [list(p) for p in _graph_module().get_backdoor_paths(nx_graph(graph), [treatment], [outcome])]


def is_blocked(graph: CausalGraph, path: list[str], given: list[str]) -> bool:
    return bool(_graph_module().is_blocked(nx_graph(graph), list(path), set(given)))


def valid_backdoor(graph: CausalGraph, treatment: str, outcome: str, adjustment: list[str]) -> bool:
    """DoWhy's check of the backdoor set's blocking (its d-separation once the treatment's outgoing edges are cut)."""
    check = _graph_module().check_valid_backdoor_set(nx_graph(graph), [treatment], [outcome], set(adjustment))
    return bool(check["is_dseparated"])


def instruments(graph: CausalGraph, treatment: str, outcome: str) -> list[str]:
    return sorted(_graph_module().get_instruments(nx_graph(graph), [treatment], [outcome]))


__all__ = ["available", "backdoor_paths", "d_separated", "identification", "instruments", "is_blocked", "load",
           "model", "nx_graph", "session", "summarise", "valid_backdoor", "version"]
