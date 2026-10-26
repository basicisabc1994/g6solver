# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Per-harness verification method registry."""
from __future__ import annotations

from typing import Callable

from g6.core.evidence import EvidenceBundle

VerificationFn = Callable[..., EvidenceBundle]

_REGISTRY: dict[str, VerificationFn] = {}


def register(name: str, fn: VerificationFn) -> VerificationFn:
    key = name.strip()
    if not key:
        raise ValueError("verification method name is required")
    _REGISTRY[key] = fn
    return fn


def get(name: str) -> VerificationFn:
    key = name.strip()
    if key not in _REGISTRY:
        raise KeyError(f"unknown verification method: {name}")
    return _REGISTRY[key]


def is_empty() -> bool:
    return not _REGISTRY


def list_names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["VerificationFn", "get", "is_empty", "list_names", "register"]
