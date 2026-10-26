# SPDX-License-Identifier: Apache-2.0
"""The rungs that rank one call's passages, registered by name: ``colbert`` (late interaction, extra ``colbert``),
``embeddings`` (sentence embeddings, extra ``embeddings``) and ``bm25`` (lexical, ``g6.grounding``'s BM25, always
there).

A rung says whether it can run (:meth:`Rung.ready`: ``""`` when it can, otherwise why not and how to make it ready)
without loading anything or reaching the network, and scores texts for a query. A dense rung needs model weights the
person already has: :func:`cached` looks in their Hugging Face cache for the repository's files and accepts only a
complete download (the repository's folder, no unfinished ``*.incomplete`` transfer, and a weights file of at least
the size the weights are known to have, so a cache holding only metadata is not taken for the weights). It accepts
weights only in safetensors form: a pickle (``pytorch_model.bin``, ``*.pt``, ``*.pth``, ``*.ckpt``, ``*.pkl``) runs code
when it is loaded, so a snapshot whose weights are pickled, or with a pickled file in a folder that has no safetensors
copy beside it, is not ready and says how to convert it. G6 never downloads weights: a rung whose weights are not
cached is not ready, and says how to get them. The model is then loaded from the cached folder with
``local_files_only`` and ``use_safetensors``, so the loading library is never asked to fetch, or to unpickle, either.

* ``colbert``: PyLate's ColBERT with ``colbert-ir/colbertv2.0`` (MIT by its model card; the terms of its training data
  are not established). Each query token's best cosine with any token of a passage, summed over the query
  (``colbert_maxsim``), computed here from the token vectors.
* ``embeddings``: sentence-transformers with ``sentence-transformers/all-MiniLM-L6-v2`` (Apache-2.0 by its model card;
  the terms of its training data are not established): the cosine of the query's and each passage's embedding
  (``cosine``).
* ``bm25``: ``g6.grounding``'s BM25 over the passages (``bm25``); a passage holding none of the query's words scores
  nothing and is not a hit.
"""
from __future__ import annotations

import functools
import importlib.util
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_MIB = 1024 * 1024
#: Weights files in pickle form, which run code when they are loaded: never loaded.
PICKLED = frozenset({".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle"})
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
_REVISION = re.compile(r"[A-Za-z0-9._-]{1,128}")


@dataclass(frozen=True)
class Weights:
    """Model weights a rung uses: the Hugging Face repository, their licence as their model card states it, their
    size, and the least size of the weights file that shows the download is complete."""

    repo: str
    licence: str
    size: str
    min_bytes: int

    def how(self) -> str:
        """How the person gets the weights themselves."""
        return (f"hf download {self.repo} (huggingface-hub's command line), or huggingface_hub.snapshot_download("
                f"{self.repo!r}) in your own Python")

    def convert(self) -> str:
        """How the person gets weights that are only pickled in safetensors form."""
        return (f"get {self.repo} in safetensors form: download its model.safetensors if the repository publishes one "
                f"(hf download {self.repo} --include '*.safetensors'), or have the Hugging Face convert space "
                "(https://huggingface.co/spaces/safetensors/convert) open one as a pull request and download that "
                "revision; G6 loads no pickle")


COLBERT_WEIGHTS = Weights("colbert-ir/colbertv2.0", "MIT by its model card; the terms of its training data are not "
                          "established", "about 440 MB", 64 * _MIB)
MINILM_WEIGHTS = Weights("sentence-transformers/all-MiniLM-L6-v2", "Apache-2.0 by its model card; the terms of its "
                         "training data are not established", "about 90 MB", 16 * _MIB)


class RungUnavailable(RuntimeError):
    """A rung asked to score that cannot."""


def hub_cache() -> Path | None:
    """The person's Hugging Face cache folder, as huggingface_hub resolves it; ``None`` when it is not installed."""
    if importlib.util.find_spec("huggingface_hub") is None:
        return None
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def cached(weights: Weights, cache: Path | None = None) -> tuple[Path | None, str]:
    """The folder of ``weights`` in the Hugging Face cache (``cache``, or the person's), and ``""``; or ``None`` and why
    they are not there, with how to get them. Nothing is downloaded."""
    root = cache if cache is not None else hub_cache()
    if root is None:
        return None, "huggingface_hub is not installed, so the Hugging Face cache cannot be read"
    get = (f"G6 never downloads model weights: get {weights.repo} yourself ({weights.size}; {weights.licence}) with "
           f"{weights.how()}")
    repo = root / ("models--" + weights.repo.replace("/", "--"))
    if not repo.is_dir():
        return None, f"{weights.repo} is not in your Hugging Face cache ({root}). {get}"
    if any((repo / "blobs").glob("*.incomplete")):
        return None, f"a download of {weights.repo} in {repo} did not finish. {get}"
    snapshots = repo / "snapshots"
    order: list[Path] = []
    ref = repo / "refs" / "main"
    if ref.is_file():
        revision = ref.read_text(encoding="utf-8", errors="replace").strip()
        if _REVISION.fullmatch(revision) and revision not in (".", ".."):
            order.append(snapshots / revision)
    order += sorted(p for p in (snapshots.iterdir() if snapshots.is_dir() else ()) if p.is_dir() and p not in order)
    for snapshot in order:
        if not snapshot.is_dir():
            continue
        files = [f for f in snapshot.rglob("*") if f.is_file()]
        safe = [f for f in files if f.suffix == ".safetensors"]
        pickled = [f for f in files if f.suffix in PICKLED]
        if not any(f.stat().st_size >= weights.min_bytes for f in safe):
            if any(f.stat().st_size >= weights.min_bytes for f in pickled):
                return None, (f"{weights.repo}'s weights in {snapshot} are only in pickle form "
                              f"({pickled[0].relative_to(snapshot).as_posix()}), which runs code when it is loaded, "
                              f"and there is no safetensors copy: {weights.convert()} to convert them")
            continue
        bare = [f for f in pickled if not any(s.parent == f.parent for s in safe)]
        if bare:
            return None, (f"{bare[0].relative_to(snapshot).as_posix()} in {snapshot} is pickled weights with no "
                          f"safetensors copy beside it, which would be loaded as a pickle: {weights.convert()} to "
                          "convert it")
        return snapshot, ""
    return None, (f"{repo} holds no weights file of {weights.size} in safetensors form (only its metadata, or a "
                  f"partial copy). {get}")


def _missing(*modules: str) -> list[str]:
    return [m for m in modules if importlib.util.find_spec(m) is None]


def maxsim(query_tokens: Any, passage_tokens: Any) -> float:
    """ColBERT's late interaction: each query token's best cosine with any passage token, summed over the query."""
    import numpy as np

    q, d = np.asarray(query_tokens, dtype=float), np.asarray(passage_tokens, dtype=float)
    if q.ndim != 2 or d.ndim != 2 or not q.shape[0] or not d.shape[0] or q.shape[1] != d.shape[1]:
        raise ValueError(f"token vectors of shapes {q.shape} and {d.shape} cannot be compared")
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
    return float((q @ d.T).max(axis=1).sum())


@runtime_checkable
class Rung(Protocol):
    """A way to score texts for a query: its name, the scale of its scores, whether it is dense (ranks by meaning
    rather than shared words), the extra it needs, whether it is ready, and the scores."""

    name: str
    score_scale: str
    dense: bool
    extra: str

    def ready(self) -> str: ...

    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


Loader = Callable[[str], Any]


@functools.lru_cache(maxsize=1)
def _pylate(path: str) -> Any:  # pragma: no cover - needs g6[colbert] and the cached weights
    from pylate import models

    return models.ColBERT(model_name_or_path=path, local_files_only=True, model_kwargs={"use_safetensors": True})


@functools.lru_cache(maxsize=1)
def _sentence_transformer(path: str) -> Any:  # pragma: no cover - needs g6[embeddings] and the cached weights
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(path, local_files_only=True, model_kwargs={"use_safetensors": True})


class ColbertRung:
    """ColBERT late interaction through PyLate, on weights already in the person's Hugging Face cache. ``loader``
    (a function from the weights' folder to a model with ``encode(texts, is_query=...)``) replaces PyLate's."""

    name, score_scale, dense, extra = "colbert", "colbert_maxsim", True, "colbert"

    def __init__(self, weights: Weights = COLBERT_WEIGHTS, *, cache: Path | None = None,
                 loader: Loader | None = None) -> None:
        self.weights, self.cache, self.loader = weights, cache, loader

    def ready(self) -> str:
        missing = _missing("numpy", *(() if self.loader else ("pylate", "huggingface_hub")))
        if missing:
            return f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed: pip install g6[colbert]"
        return cached(self.weights, self.cache)[1]

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        where, why = cached(self.weights, self.cache)
        if where is None:
            raise RungUnavailable(why)
        model = (self.loader or _pylate)(str(where))
        query_tokens = model.encode([query], is_query=True)
        passage_tokens = model.encode(list(texts), is_query=False)
        if len(query_tokens) != 1 or len(passage_tokens) != len(texts):
            raise RungUnavailable(f"the model encoded {len(passage_tokens)} passages for {len(texts)}")
        return [maxsim(query_tokens[0], tokens) for tokens in passage_tokens]


class EmbeddingsRung:
    """Sentence embeddings through sentence-transformers, on weights already in the person's Hugging Face cache.
    ``loader`` (a function from the weights' folder to a model with ``encode(texts)``) replaces the library's."""

    name, score_scale, dense, extra = "embeddings", "cosine", True, "embeddings"

    def __init__(self, weights: Weights = MINILM_WEIGHTS, *, cache: Path | None = None,
                 loader: Loader | None = None) -> None:
        self.weights, self.cache, self.loader = weights, cache, loader

    def ready(self) -> str:
        missing = _missing("numpy", *(() if self.loader else ("sentence_transformers", "huggingface_hub")))
        if missing:
            return (f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed: "
                    "pip install g6[embeddings]")
        return cached(self.weights, self.cache)[1]

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        import numpy as np

        where, why = cached(self.weights, self.cache)
        if where is None:
            raise RungUnavailable(why)
        model = (self.loader or _sentence_transformer)(str(where))
        vectors = np.asarray(model.encode([query, *texts]), dtype=float)
        if vectors.ndim != 2 or vectors.shape[0] != len(texts) + 1:
            raise RungUnavailable(f"the model gave vectors of shape {vectors.shape} for {len(texts) + 1} texts")
        vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        return [float(v @ vectors[0]) for v in vectors[1:]]


class Bm25Rung:
    """``g6.grounding``'s BM25 over the passages: lexical, so it needs no extra and misses paraphrase."""

    name, score_scale, dense, extra = "bm25", "bm25", False, ""

    def ready(self) -> str:
        return ""

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        from g6.grounding.retrieval import TextRetriever

        scores = [0.0] * len(texts)
        for found in TextRetriever([(str(i), t) for i, t in enumerate(texts)]).search(query, top_k=len(texts) or 1):
            scores[int(found.source)] = float(found.score)
        return scores


class Rungs:
    """The rungs registered by name; a name is taken once, unless :meth:`register` is told to ``replace`` it."""

    def __init__(self) -> None:
        self._rungs: dict[str, Rung] = {}

    def register(self, rung: Rung, *, replace: bool = False) -> None:
        if not isinstance(rung, Rung):
            raise TypeError(f"{rung!r} is not a rung (name, score_scale, dense, extra, ready, score)")
        if not _NAME.fullmatch(str(rung.name)):
            raise ValueError(f"{rung.name!r} is not a rung name (lower-case letters, digits and '_')")
        if rung.name in self._rungs and not replace:
            raise ValueError(f"a rung named {rung.name!r} is already registered")
        self._rungs[rung.name] = rung

    def unregister(self, name: str) -> None:
        self._rungs.pop(name, None)

    def get(self, name: str) -> Rung | None:
        return self._rungs.get(name)

    def names(self) -> list[str]:
        return sorted(self._rungs)

    def describe(self) -> list[dict[str, Any]]:
        """Each rung: what it is and whether it is ready (nothing is loaded and nothing reaches the network)."""
        out = []
        for name in self.names():
            rung = self._rungs[name]
            weights = getattr(rung, "weights", None)
            out.append({"name": name, "dense": rung.dense, "score_scale": rung.score_scale, "extra": rung.extra,
                        "ready": rung.ready() or "ready",
                        "weights": {"repo": weights.repo, "licence": weights.licence, "size": weights.size,
                                    "how_to_get": weights.how()} if isinstance(weights, Weights) else None})
        return out


#: The order ``best`` tries the rungs in.
LADDER = ("colbert", "embeddings", "bm25")
RUNGS = Rungs()
for _rung in (ColbertRung(), EmbeddingsRung(), Bm25Rung()):
    RUNGS.register(_rung)


def finite(scores: Sequence[float]) -> bool:
    return all(isinstance(s, (int, float)) and math.isfinite(s) for s in scores)


__all__ = ["COLBERT_WEIGHTS", "LADDER", "MINILM_WEIGHTS", "PICKLED", "RUNGS", "Bm25Rung", "ColbertRung", "EmbeddingsRung",
           "Loader", "Rung", "RungUnavailable", "Rungs", "Weights", "cached", "finite", "hub_cache", "maxsim"]
