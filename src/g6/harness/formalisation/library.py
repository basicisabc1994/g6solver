# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A library of earlier statements for the drafter to look at (design section 15.4).

Whatever is retrieved is untrusted context: it carries its source and the digest of its text, and nothing more is
claimed of it. It has no trust label, and it stands for a checked statement only where it names a receipt that
resolves. The one library in this version is the person's own accepted statements under ``G6_HOME``; the ranking is
by shared terms, as in the earlier repository's library. A wider search can replace it behind ``StatementLibrary``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_TERM = re.compile(r"[a-z0-9_]{2,}")


@dataclass(frozen=True)
class Retrieved:
    text: str
    language: str
    source_id: str
    content_sha256: str
    receipt: str = ""


class StatementLibrary(Protocol):
    def search(self, query: str, *, language: str, limit: int = 3) -> list[Retrieved]: ...


def _terms(text: str) -> set[str]:
    return set(_TERM.findall(text.lower()))


def ranked(query: str, items: list[tuple[str, Retrieved]], limit: int) -> list[Retrieved]:
    """The items whose description shares the most terms with the query, most first; one sharing none is left out."""
    wanted = _terms(query)
    scored = [(len(wanted & _terms(described)), i, one) for i, (described, one) in enumerate(items)]
    return [one for score, _, one in sorted(scored, key=lambda s: (-s[0], s[1])) if score][:max(0, limit)]


class LocalLibrary:
    """The person's own accepted statements, one JSON line each, in a file under ``G6_HOME``."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from g6.core.storage import g6_home

            path = g6_home() / "formalisation" / "library.jsonl"
        self.path = Path(path)

    def add(self, *, described: str, text: str, language: str, receipt: str = "") -> Retrieved:
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        one = Retrieved(text=text, language=language, source_id=f"formalisation-library:{sha}", content_sha256=sha,
                        receipt=receipt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps({"described": described, "text": text, "language": language, "receipt": receipt},
                                 sort_keys=True) + "\n")
        return one

    def search(self, query: str, *, language: str, limit: int = 3) -> list[Retrieved]:
        if not self.path.exists():
            return []
        items = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                held = json.loads(line)
                text, described = held["text"], held["described"]
                if held["language"] != language or type(text) is not str or type(described) is not str:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            items.append((described, Retrieved(text=text, language=language, content_sha256=sha,
                                               source_id=f"formalisation-library:{sha}",
                                               receipt=str(held.get("receipt") or ""))))
        return ranked(query, items, limit)


__all__ = ["LocalLibrary", "Retrieved", "StatementLibrary", "ranked"]