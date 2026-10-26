# SPDX-License-Identifier: Apache-2.0
"""The prompt addendum: hint text built deterministically from theories whose trigger matches a task.

Ported from the selection half of the archive's ``prompt_evolver.py``. Its ``record_applications`` /
``record_outcome`` (credit every applied theory with the agent's pass or fail, audit 006 LL-005) are not ported;
credit is :meth:`g6.learning.TheoryStore.record_paired_outcome`.
"""
from __future__ import annotations

import json

from collections.abc import Mapping, Sequence
from typing import Any

from g6.learning.schema import PromptAddendum, Theory
from g6.learning.theory_store import TheoryStore

HEADER = "Optional hints (may or may not apply):"


def trigger_matches(trigger: str, task_meta: Mapping[str, Any] | None) -> bool:
    """``"always"`` matches every task; ``"key:value"`` matches when ``task_meta[key]`` equals ``value`` (any case)."""
    if trigger.startswith("condition_keys:"):
        try:
            keys = json.loads(trigger.removeprefix("condition_keys:"))
        except (ValueError, TypeError):
            return False
        if not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys):
            return False
        return not keys or (task_meta is not None and any(key in task_meta for key in keys))
    if trigger == "always":
        return True
    if not task_meta or ":" not in trigger:
        return False
    key, value = trigger.split(":", 1)
    return str(task_meta.get(key, "")).lower() == value.lower()


def render_addendum(theories: Sequence[Theory], task_meta: Mapping[str, Any] | None = None, max_chars: int = 6000,
                    *, max_theories: int = 10) -> PromptAddendum:
    """One hint block from the given theories (in the given order) whose trigger matches ``task_meta``."""
    selected = [t for t in theories if trigger_matches(t.trigger, task_meta)][:max_theories]
    if not selected:
        return PromptAddendum("")
    lines, ids, size = [HEADER], [], len(HEADER)
    for theory in selected:
        entry = f"- [{theory.failure_code}] {theory.instruction}"
        if size + len(entry) + 1 > max_chars:
            break
        lines.append(entry)
        ids.append(theory.theory_id)
        size += len(entry) + 1
    return PromptAddendum("\n".join(lines), tuple(ids)) if ids else PromptAddendum("")


def build_addendum(store: TheoryStore, task_meta: Mapping[str, Any] | None = None, max_chars: int = 6000, *,
                   max_theories: int = 10) -> PromptAddendum:
    """The addendum for a task outside training: ``active`` theories of the store's scope only, strongest first.

    Candidates never reach this path; a theory becomes active only when a person accepts a promotion recommendation.
    """
    return render_addendum(store.theories(status="active"), task_meta, max_chars, max_theories=max_theories)


def with_addendum(addendum: PromptAddendum, task: str) -> str:
    """The prompt for one task: the hint block, a blank line, the task. Built afresh for every call, so hint blocks
    never nest across cycles (audit 006, LL-010)."""
    return f"{addendum.text}\n\n{task}" if addendum.text else task


__all__ = ["HEADER", "build_addendum", "render_addendum", "trigger_matches", "with_addendum"]
