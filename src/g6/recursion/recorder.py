# SPDX-License-Identifier: Apache-2.0
"""Episode events on the existing append-only chain, with detached readbacks."""
from __future__ import annotations

from pathlib import Path

from g6.core.ledger import ChainLedger


class EpisodeRecorder:
    def __init__(self, *, episode_id: str, path: str | Path | None = None) -> None:
        if not episode_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
                                 for c in episode_id):
            raise ValueError("G6_E_RSI_EPISODE_INVALID")
        self.episode_id = episode_id
        self._ledger = ChainLedger(path)
        if any(not row["id"].startswith(episode_id + ":") for row in self._ledger.entries()):
            raise ValueError("G6_E_RSI_EPISODE_INVALID")

    def record(self, kind: str, body: dict) -> None:
        self._ledger.append(kind, f"{self.episode_id}:{len(self._ledger.entries()):08d}", body)

    def entries(self) -> list[dict]:
        return self._ledger.entries()


__all__ = ["EpisodeRecorder"]
