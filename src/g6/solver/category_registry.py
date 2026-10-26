# SPDX-License-Identifier: Apache-2.0
"""Fallback runners grouped by category. A step whose own runner gives nothing usable tries the runners of its
``fallback_categories`` in order; a value produced that way is marked ``fallback_used`` and recorded as a
``SolverFailure``, so a fallback is never mistaken for the primary path.

The archive classified its own component names by prefix; here the caller names the category when it registers
a runner, because there are no archive components to classify.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any


class ComponentCategory(str, Enum):
    CONTEXT = "context"
    SAFETY = "safety"
    ALIGNMENT = "alignment"
    ADAPTATION = "adaptation"
    AGENTS = "agents"
    STANDALONE = "standalone"
    UTILITY = "utility"
    ORCHESTRATION = "orchestration"


class CategoryRegistry:
    """Named fallback runners per category, in registration order."""

    def __init__(self) -> None:
        self._runners: dict[ComponentCategory, dict[str, Callable[[Any], Any]]] = {c: {} for c in ComponentCategory}

    def register(self, name: str, category: ComponentCategory | str, runner: Callable[[Any], Any]) -> None:
        if not name:
            raise ValueError("a fallback runner needs a name")
        self._runners[ComponentCategory(category)][name] = runner

    def runners(self, category: ComponentCategory | str) -> list[tuple[str, Callable[[Any], Any]]]:
        return list(self._runners[ComponentCategory(category)].items())

    def fallback_chain(self, categories: Iterable[ComponentCategory | str]) -> list[tuple[str, Callable[[Any], Any]]]:
        """The runners of ``categories`` in order, each name once."""
        chain: list[tuple[str, Callable[[Any], Any]]] = []
        seen: set[str] = set()
        for category in categories:
            for name, runner in self.runners(category):
                if name not in seen:
                    seen.add(name)
                    chain.append((name, runner))
        return chain


__all__ = ["CategoryRegistry", "ComponentCategory"]
