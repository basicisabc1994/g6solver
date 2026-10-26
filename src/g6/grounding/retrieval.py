# SPDX-License-Identifier: Apache-2.0
"""The ``Retriever`` protocol and a stdlib BM25 retriever over caller-supplied text."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

_WORD = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*%?")
STOPWORDS = frozenset(
    "a an the of to in on at by for with from and or as is are was were be been being it its this that these "
    "those there their they them he she his her we our you your i me my which who whom what when where how "
    "will would shall should can could may might must do does did has have had into over under than then so "
    "such any all each per via about after before also only".split())
# Negators are deliberately NOT stopwords: "never computes" must not match "computes".
NEGATORS = frozenset("not no never none neither nor cannot without isn't aren't wasn't weren't don't "
                     "doesn't didn't won't can't shouldn't mustn't".split())


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("’", "'"))


def stem(word: str) -> str:
    """A light suffix stripper; enough to match ``refunds``/``refund`` and ``issued``/``issue``."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix) and not word[-1].isdigit():
            return word[: -len(suffix)]
    return word


def content_terms(text: str) -> list[str]:
    return [stem(t) for t in tokens(text) if t not in STOPWORDS and t not in NEGATORS]


@dataclass(frozen=True)
class Passage:
    text: str
    source: str
    score: float = 0.0
    score_scale: str = "bm25"
    provenance: str = "caller_supplied"   # who captured the text; never assumed to be a vetted corpus
    # audit 014, GRD-017: the source's own publication date (ISO 8601), "" when unknown; never an index or capture time
    published_at: str = ""


@runtime_checkable
class Retriever(Protocol):
    def search(self, query: str, top_k: int = 3) -> list[Passage]: ...


def _chunks(text: str, size: int = 4) -> list[str]:
    """Paragraphs, with long ones cut into windows of ``size`` sentences."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", para.strip()) if s]
        for i in range(0, len(sentences), size):
            out.append(" ".join(sentences[i:i + size]))
    return [c for c in out if c.strip()]


class TextRetriever:
    """BM25 over passages held in memory. Sources are labels you choose (file names, URLs)."""

    def __init__(self, passages: list[tuple[str, str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._docs = [(src, txt, Counter(content_terms(txt))) for src, txt in passages if txt.strip()]
        self._k1, self._b = k1, b
        lengths = [sum(c.values()) for _, _, c in self._docs]
        self._avg = (sum(lengths) / len(lengths)) if lengths else 0.0
        df: Counter[str] = Counter()
        for _, _, counts in self._docs:
            df.update(counts.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def from_texts(cls, texts: dict[str, str]) -> TextRetriever:
        return cls([(src, chunk) for src, text in texts.items() for chunk in _chunks(text)])

    @classmethod
    def from_folder(cls, root: str | Path, patterns: tuple[str, ...] = ("*.md", "*.txt")) -> TextRetriever:
        root = Path(root)
        files = sorted({p for pat in patterns for p in root.rglob(pat) if p.is_file()})
        return cls.from_texts({str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
                               for p in files})

    def __len__(self) -> int:
        return len(self._docs)

    def search(self, query: str, top_k: int = 3) -> list[Passage]:
        terms = content_terms(query)
        scored = []
        for src, txt, counts in self._docs:
            length = sum(counts.values()) or 1
            score = 0.0
            for t in terms:
                f = counts.get(t, 0)
                if f:
                    score += self._idf[t] * f * (self._k1 + 1) / (
                        f + self._k1 * (1 - self._b + self._b * length / (self._avg or 1)))
            if score > 0:
                scored.append(Passage(txt, src, round(score, 4)))
        scored.sort(key=lambda p: -p.score)
        return scored[:top_k]
