# SPDX-License-Identifier: Apache-2.0
"""Reference material for the proposer: short passages from folders of Markdown the person keeps, read only on the host.

A passage reaches the proposer and nothing else. What the proposer writes goes to agents and into a public repository, so ``copied``
refuses a text with a run of eight words from any passage, and no passage is saved. The folders are named outside the repository.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

CHUNK_CHARS, COPY_WORDS = 1200, 8
SKIP = ("universal-rag-chat", "readme.md")


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def chunks(shelves: Iterable[Path]) -> list[str]:
    """Each folder's Markdown as paragraphs, joined up to ``CHUNK_CHARS``."""
    found = []
    for shelf in shelves:
        for path in sorted(Path(shelf).rglob("*.md")):
            if any(part in path.as_posix().lower() for part in SKIP):
                continue
            held = ""
            for block in re.split(r"\n\s*\n", path.read_text("utf-8", errors="replace")):
                if held and len(held) + len(block) > CHUNK_CHARS:
                    found.append(held)
                    held = ""
                held = f"{held}\n\n{block.strip()}".strip()
            if held:
                found.append(held)
    return found


def passages(shelves: Iterable[Path], query: str, limit: int = 3) -> list[str]:
    """The ``limit`` passages sharing the most long words with ``query``. A plain count: it needs no embedding model."""
    wanted = {w for w in words(query) if len(w) > 3}
    scored = sorted(((len(wanted & set(words(c))) / (1 + len(c) / CHUNK_CHARS), i, c) for i, c in enumerate(chunks(shelves))),
                    key=lambda t: (-t[0], t[1]))
    return [c for score, _, c in scored[:limit] if score > 0]


def copied(text: str, sources: Sequence[str]) -> bool:
    said = words(text)
    runs = {tuple(said[i:i + COPY_WORDS]) for i in range(len(said) - COPY_WORDS + 1)}
    for source in sources:
        seen = words(source)
        if any(tuple(seen[i:i + COPY_WORDS]) in runs for i in range(len(seen) - COPY_WORDS + 1)):
            return True
    return False
