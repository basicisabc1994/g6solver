# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

from __future__ import annotations

from typing import Literal, TypeVar

Maturity = Literal["production", "beta", "experimental", "simulation_only", "stubbed"]
_VALID: frozenset[str] = frozenset({"production", "beta", "experimental", "simulation_only", "stubbed"})

T = TypeVar("T")


def component_maturity(level: Maturity, reason: str = ""):
    """Decorator: mark a component class with a maturity level."""
    if level not in _VALID:
        raise ValueError(f"maturity must be one of {sorted(_VALID)}; got {level!r}")

    def _wrap(cls: T) -> T:
        setattr(cls, "__g6_maturity__", level)
        setattr(cls, "__g6_maturity_reason__", reason)
        return cls

    return _wrap


def get_maturity(cls) -> Maturity | None:
    """Return the declared maturity level or None if unmarked."""
    return getattr(cls, "__g6_maturity__", None)


def maturity_reason(cls) -> str:
    return getattr(cls, "__g6_maturity_reason__", "")


def component_capabilities(*caps: str):
    """Decorator: declare capabilities a component provides (e.g. 'llm_routing', 'formal_verification')."""
    def _wrap(cls: T) -> T:
        setattr(cls, "__g6_capabilities__", tuple(caps))
        return cls
    return _wrap


def get_capabilities(cls) -> tuple[str, ...]:
    """Return declared capabilities for a component class."""
    return getattr(cls, "__g6_capabilities__", ())


def observable(cls):
    """Mark a component as observable — emits lifecycle events for monitoring."""
    setattr(cls, "__g6_observable__", True)
    return cls


def is_observable(cls) -> bool:
    """Return whether the class is marked as observable."""
    return getattr(cls, "__g6_observable__", False)
