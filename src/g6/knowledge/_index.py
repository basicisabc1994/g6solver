# SPDX-License-Identifier: Apache-2.0
"""What every index of the knowledge family shares: the hit a search returns, the search's typed outcome, and the
index backends registered by name. ``_``-prefixed, so it never registers as a component.

* :class:`Hit` is one passage a search found (the family's :class:`~g6.knowledge._types.Passage`, with its source),
  its ``score`` and the ``score_scale`` that score is on. A score is comparable only with others on the same scale
  from the same search (:data:`SCALES` says what each scale is); hits from two backends are combined by rank, never
  by raw score.
* :class:`Searched` is what a search found: ``ok`` (hits), ``empty`` (the backend answered and nothing matched),
  ``unavailable`` (the backend is not installed, not configured or not reachable) or ``failed`` (it answered with an
  error, or its stored records no longer check out). An unavailable or failed backend is never reported as "no
  match". ``searched`` is how many passages it searched, and ``total`` how many matches it reported, when it says.
* :class:`Index` is the interface every backend implements: ``add`` passages, ``find`` (a :class:`Searched`),
  ``remove`` and ``status``, plus ``search``, which makes every index a ``g6.grounding`` retriever (a list of
  ``g6.grounding`` passages, the best first). ``search`` raises :class:`IndexUnavailable` rather than return an empty
  list for a backend that did not answer, so a retriever's silence always means "nothing matched".
* :class:`IndexBackend` describes a backend (what it is, whether it reaches the network, keeps what is added, takes
  writes, and is ready) and opens an index on it. :data:`index_backends` is the family's registry: a member that
  provides a backend registers it on import and says so in its package (``__g6_index_backends__``, a literal tuple of
  names), and the registry imports such members the first time it is asked, so no member imports another to find
  its backend.

Search results are retrieved text: untrusted data, never instructions.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from g6.knowledge._types import Passage

#: The scales a score can be on, and what a number on each means.
SCALES: dict[str, str] = {
    "fts5_bm25": "SQLite FTS5's BM25 of the passage for the query, negated so that higher is better; comparable only "
                 "with other hits of the same search of the same index",
    "es_bm25": "Elasticsearch's _score for the query (BM25 unless the index says otherwise); comparable only with "
               "other hits of the same query on the same index",
    "colbert_maxsim": "ColBERT late interaction: for each query token, its best cosine with any token of the passage, "
                      "summed over the query; comparable only with other hits of the same search",
    "cosine": "the cosine of the query's and the passage's sentence embeddings, from -1 to 1; comparable only with "
              "other hits of the same search by the same model",
    "bm25": "g6.grounding's BM25 over the passages of one search; comparable only with other hits of that search",
}
UNAVAILABLE = "G6_E_KNOWLEDGE_INDEX_UNAVAILABLE"
FAILED = "G6_E_KNOWLEDGE_INDEX_FAILED"
OVER_BUDGET = "G6_E_KNOWLEDGE_OVER_BUDGET"
READ_ONLY = "G6_E_KNOWLEDGE_INDEX_READ_ONLY"
UNKNOWN_BACKEND = "G6_E_KNOWLEDGE_INDEX_BACKEND"
BAD_QUERY = "G6_E_KNOWLEDGE_INDEX_QUERY"
TIMED_OUT = "G6_E_KNOWLEDGE_INDEX_TIMEOUT"
#: What a family member assigns, as a literal tuple of names, in its ``__init__.py`` when it registers index backends.
BACKENDS_DECLARATION = "__g6_index_backends__"
Outcome = Literal["ok", "empty", "unavailable", "failed"]
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


class IndexRefused(Exception):
    """An index that will not do what was asked, with its stable code, the reason and what it measured."""

    def __init__(self, code: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason, self.evidence = code, reason, dict(evidence or {})


class Hit(BaseModel):
    """One passage a search found, its score and the scale the score is on, and its rank (1 is the best)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passage: Passage
    score: float = Field(allow_inf_nan=False)
    score_scale: str = Field(min_length=1, max_length=40)
    rank: int = Field(ge=1)

    def to_grounding(self) -> Any:
        """This hit as a ``g6.grounding`` passage, its score and scale kept."""
        return self.passage.to_grounding(score=self.score, score_scale=self.score_scale)


class Searched(BaseModel):
    """What one search found; see the module docstring for ``outcome``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str
    query: str
    outcome: Outcome
    score_scale: str
    hits: list[Hit] = Field(default_factory=list)
    reason: str = ""
    searched: int | None = None
    total: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def answered(self) -> bool:
        """Whether the backend answered (``ok`` or ``empty``)."""
        return self.outcome in ("ok", "empty")

    @property
    def complete(self) -> bool | None:
        """Whether every match the backend reported came back; ``None`` when it reported no count."""
        return None if self.total is None else len(self.hits) >= self.total


class Added(BaseModel):
    """What an ``add`` kept: passages new to the index, ones it already held, and the size it measured."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    added: int
    known: int
    evidence: dict[str, Any] = Field(default_factory=dict)


class Removed(BaseModel):
    """What a ``remove`` took out, and the ids it did not hold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    removed: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class IndexUnavailable(RuntimeError):
    """A search through ``Index.search`` (the retriever interface) that the backend did not answer."""

    def __init__(self, searched: Searched) -> None:
        super().__init__(f"the {searched.backend} index did not answer ({searched.outcome}): {searched.reason}")
        self.searched = searched


@runtime_checkable
class Index(Protocol):
    """An index of passages: see the module docstring."""

    backend: str
    score_scale: str

    def add(self, passages: Sequence[Passage]) -> Added: ...

    def find(self, query: str, top_k: int = 10) -> Searched: ...

    def search(self, query: str, top_k: int = 3) -> list[Any]: ...

    def remove(self, ids: Sequence[str]) -> Removed: ...

    def status(self) -> dict[str, Any]: ...


class Retrieves:
    """``search`` for an index that has ``find``: the ``g6.grounding`` retriever interface, which raises
    :class:`IndexUnavailable` for a backend that did not answer instead of returning an empty list."""

    def find(self, query: str, top_k: int = 10) -> Searched:  # pragma: no cover - each index has its own
        raise NotImplementedError

    def search(self, query: str, top_k: int = 3) -> list[Any]:
        found = self.find(query, top_k)
        if not found.answered:
            raise IndexUnavailable(found)
        return [hit.to_grounding() for hit in found.hits]


def ranked(scored: Sequence[tuple[Passage, float]], scale: str, top_k: int) -> list[Hit]:
    """The ``top_k`` best of ``scored`` as hits, best first; ties keep their order."""
    order = sorted(range(len(scored)), key=lambda i: -scored[i][1])[:top_k]
    return [Hit(passage=scored[i][0], score=scored[i][1], score_scale=scale, rank=n)
            for n, i in enumerate(order, start=1)]


@dataclass(frozen=True)
class IndexBackend:
    """One index backend: its name and what it is; whether it runs on this machine (``local``), reaches the network
    (``network``), keeps what is added between calls (``persistent``) and takes writes (``writable``); the scale of
    its scores; ``ready``, which says ``""`` when it can run and otherwise why not and how to make it ready (it never
    reaches the network to find out); and ``open``, which opens an index on it with the settings it takes."""

    name: str
    summary: str
    score_scale: str
    open: Callable[..., Index]
    local: bool = True
    network: bool = False
    persistent: bool = True
    writable: bool = True
    ready: Callable[[], str] = lambda: ""
    extra: str = ""

    def describe(self, *, probe: bool = True) -> dict[str, Any]:
        return {"name": self.name, "summary": self.summary, "score_scale": self.score_scale,
                "scale": SCALES.get(self.score_scale, ""), "local": self.local, "network": self.network,
                "persistent": self.persistent, "writable": self.writable, "extra": self.extra,
                "ready": self.ready() if probe else None}


def declares_backends(package: str) -> tuple[str, ...]:
    """The backend names ``package`` declares in ``__g6_index_backends__``, read from its source without importing it;
    ``()`` when it declares none or the source cannot be read."""
    try:
        spec = importlib.util.find_spec(package)
        if spec is None or not (spec.origin or "").endswith("__init__.py"):
            return ()
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (ImportError, OSError, SyntaxError, ValueError):
        return ()
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else getattr(
            node, "target", None)
        if isinstance(target, ast.Name) and target.id == BACKENDS_DECLARATION and node.value is not None:
            try:
                names = ast.literal_eval(node.value)
            except ValueError:
                return ()
            return tuple(n for n in names if isinstance(n, str)) if isinstance(names, tuple) else ()
    return ()


class IndexBackends:
    """The index backends registered by name. A name is taken once; :meth:`register` with ``replace`` swaps it.

    With ``discover``, the first lookup imports each knowledge family member that declares backends
    (:data:`BACKENDS_DECLARATION`), and importing it registers them; a member that fails to import is skipped and its
    error kept in :attr:`discovery_errors`."""

    def __init__(self, *, discover: bool = False) -> None:
        self._backends: dict[str, IndexBackend] = {}
        self._discover = discover
        self.discovery_errors: dict[str, str] = {}

    def discover(self) -> None:
        """Import the knowledge family members that declare index backends (once; later calls do nothing)."""
        if not self._discover:
            return
        self._discover = False
        from g6.core.registry import get_registry

        for ref in list(get_registry().refs.values()):
            if ref.family == "knowledge" and declares_backends(ref.module.split(":", 1)[0]):
                try:
                    importlib.import_module(ref.module.split(":", 1)[0])
                except Exception as exc:  # noqa: BLE001 - one member that fails to import never hides the others
                    self.discovery_errors[ref.name] = f"{type(exc).__name__}: {exc}"[:300]

    def register(self, backend: IndexBackend, *, replace: bool = False) -> None:
        if not isinstance(backend, IndexBackend):
            raise TypeError(f"{backend!r} is not an IndexBackend")
        if not _NAME.fullmatch(backend.name):
            raise ValueError(f"{backend.name!r} is not a backend name (lower-case letters, digits and '_')")
        if backend.score_scale not in SCALES:
            raise ValueError(f"{backend.score_scale!r} is not a known score scale ({', '.join(SCALES)})")
        if backend.name in self._backends and not replace:
            raise ValueError(f"an index backend named {backend.name!r} is already registered")
        self._backends[backend.name] = backend

    def unregister(self, name: str) -> None:
        self._backends.pop(name, None)

    def get(self, name: str) -> IndexBackend | None:
        if name not in self._backends:
            self.discover()
        return self._backends.get(name)

    def names(self) -> list[str]:
        self.discover()
        return sorted(self._backends)

    def describe(self, *, probe: bool = True) -> list[dict[str, Any]]:
        """Each backend: what it is, and (with ``probe``) whether it is ready; ``ready`` never reaches the network."""
        return [self._backends[n].describe(probe=probe) for n in self.names()]

    def open(self, name: str, **settings: Any) -> Index:
        """An index on the backend ``name``; :class:`IndexRefused` when none is registered under that name or it is
        not ready."""
        backend = self.get(name)
        if backend is None:
            raise IndexRefused(UNKNOWN_BACKEND, f"no index backend named {name!r} (registered: "
                                                f"{', '.join(self.names()) or 'none'})")
        why = backend.ready()
        if why:
            raise IndexRefused(UNAVAILABLE, f"the {name} backend is not ready: {why}")
        return backend.open(**settings)


#: The family's registry of index backends; it finds the members that declare backends the first time it is asked.
index_backends = IndexBackends(discover=True)

__all__ = ["BACKENDS_DECLARATION", "BAD_QUERY", "FAILED", "OVER_BUDGET", "READ_ONLY", "SCALES", "TIMED_OUT",
           "UNAVAILABLE", "UNKNOWN_BACKEND", "Added", "Hit", "Index", "IndexBackend", "IndexBackends", "IndexRefused",
           "IndexUnavailable", "Outcome", "Removed", "Retrieves", "Searched", "declares_backends", "index_backends",
           "ranked"]
