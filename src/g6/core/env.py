# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""env — shared environment / run-mode helpers.

Round-13 (2026-05-28): consolidates the production-mode canonicalization
logic that round-12 introduced independently in patch_client (B179) and
recursive_architect.self_model_gate (B186). Centralizing here prevents
copy-paste drift as future audit landings need the same gate.

Canonical production forms (case-insensitive): "production", "prod".
Resolution order: explicit argument > G6_ENV env var > empty (non-prod).
"""
from __future__ import annotations

import os

# Set of canonical run-mode strings that resolve to production.
# Update here, not in caller-specific copies.
RUN_MODE_PRODUCTION: frozenset[str] = frozenset({"production", "prod"})


def resolve_run_mode(explicit: str | None = None) -> str:
    """Resolve the effective run-mode for a fail-closed decision.

    Returns a lowercase, whitespace-stripped string. Explicit parameter
    wins; falls back to ``G6_ENV`` env var; empty / unset returns "".

    Callers should treat the return value as opaque and check via
    ``is_production_env`` or set-membership against
    ``RUN_MODE_PRODUCTION``.
    """
    if explicit is not None and explicit.strip():
        return explicit.strip().lower()
    return os.environ.get("G6_ENV", "").strip().lower()


def is_production_env(explicit: str | None = None) -> bool:
    """Return True iff the resolved run-mode is in ``RUN_MODE_PRODUCTION``.

    Round-13 round-12-reviewer's I4 fix: this helper replaces module-private
    duplicates that round-12 introduced in patch_client and
    recursive_architect.self_model_gate. Other components facing the same
    "fail-closed in production" pattern should import this helper rather
    than reimplement the canonicalization.
    """
    return resolve_run_mode(explicit) in RUN_MODE_PRODUCTION
