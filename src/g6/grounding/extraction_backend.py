# SPDX-License-Identifier: Apache-2.0
"""Structural seam for an optional, registry-resolved passage extractor."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class ExtractionSpan(Protocol):
    said: str
    start: int | None
    end: int | None
    grounded: bool
    match: str

    def at(self, text: str) -> str: ...


class ExtractionBackend(Protocol):
    # The extractor owns its limits, field records, errors and bounded pattern/model operations.
    extraction: Any

    def load_library(self) -> Any: ...

    def extract_passages(self, library: Any, read_prompt: Callable[..., str],
                         claim: str, text: str) -> list[tuple]: ...
