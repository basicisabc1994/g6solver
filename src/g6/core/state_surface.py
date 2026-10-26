# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Declared persistence/query surface for a harness."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Persistence = Literal["none", "run", "session", "project", "global"]
QueryableVia = Literal["scorecard", "ledger", "snapshot_export", "none"]


@dataclass(frozen=True)
class StateSurface:
    persistence: Persistence = "none"
    queryable_via: QueryableVia = "none"
    retention: str = "run"

    def __post_init__(self) -> None:
        if self.persistence not in {"none", "run", "session", "project", "global"}:
            raise ValueError(f"unknown persistence: {self.persistence}")
        if self.queryable_via not in {"scorecard", "ledger", "snapshot_export", "none"}:
            raise ValueError(f"unknown queryable_via: {self.queryable_via}")
        if not self.retention.strip():
            raise ValueError("retention is required")


__all__ = ["Persistence", "QueryableVia", "StateSurface"]
