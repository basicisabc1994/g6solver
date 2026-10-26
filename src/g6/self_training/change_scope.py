# SPDX-License-Identifier: Apache-2.0
"""``classify_change_scope``: may a proposed self-modification touch these paths at all? (audit 024 §11)

Rewritten from the archive's ``self_improve/safety_classifier``. Pure: it reads the paths the change names, never the
files. Its answer is an input to :func:`govern_patch_promotion`, never a reliability label: ``allow`` means only that
the paths are inside the target component and touch nothing protected.

* ``block``: a deletion; a path that cannot be placed inside the repository (empty, absolute, a drive, or ``..`` out of
  it); a pytest configuration or collection-hook file (audit 024 REL-003: a new ``conftest.py`` that skips every test
  turned a broken change green); a path outside the target component's roots once ``.`` and ``..`` are resolved (REL-004:
  an earlier version compared a string prefix, so ``components/demo/../../../outside/x.py`` passed);
* ``hitl``: a path that hits one of the promotion gate's protected fragments (``PROTECTED_PATH_FRAGMENTS``).
"""
from __future__ import annotations

import posixpath
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from g6.self_training.patch_promotion_gate import PROTECTED_PATH_FRAGMENTS, _normalise_path, _outside_repo

#: The files that configure pytest or hook its collection: a change may not edit them (REL-003).
TEST_CONFIG_FILES = frozenset({"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "noxfile.py",
                               "sitecustomize.py", "usercustomize.py"})
#: Where a component's own files live in this repository's layout ({component} is filled in).
DEFAULT_ROOTS = ("src/g6/{component}/", "tests/{component}/")


class ScopeVerdict(StrEnum):
    ALLOW = "allow"
    HITL = "hitl"
    BLOCK = "block"


_RANK = {ScopeVerdict.ALLOW: 0, ScopeVerdict.HITL: 1, ScopeVerdict.BLOCK: 2}


@dataclass(frozen=True)
class ChangeScope:
    """The proposed change, as data: its target component and the repository-relative paths it changes or deletes."""

    component: str
    changed_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeDecision:
    """The verdict and every reason for it, each ``"<G6_E_ code>: <path or detail>"``."""

    verdict: ScopeVerdict
    reasons: tuple[str, ...] = ()


def classify_change_scope(scope: ChangeScope, *, roots: Sequence[str] = DEFAULT_ROOTS) -> ScopeDecision:
    """The scope verdict for ``scope``: the strictest any path earns; ``allow`` only when no path earns a reason."""
    found: list[tuple[ScopeVerdict, str]] = []
    component = scope.component.strip()
    if not component.isidentifier():
        found.append((ScopeVerdict.BLOCK, f"G6_E_SCOPE_NO_COMPONENT: {scope.component!r} names no component"))
    if not scope.changed_paths and not scope.deleted_paths:
        found.append((ScopeVerdict.BLOCK, "G6_E_SCOPE_EMPTY: the change names no path"))
    found += [(ScopeVerdict.BLOCK, f"G6_E_SCOPE_DELETION: {path}") for path in scope.deleted_paths]
    owned = tuple(_normalise_path(root.format(component=component)).rstrip("/") + "/" for root in roots)
    for path in scope.changed_paths:
        norm = _normalise_path(path)
        if _outside_repo(norm):
            found.append((ScopeVerdict.BLOCK, f"G6_E_SCOPE_OUTSIDE_REPO: {path}"))
            continue
        if posixpath.basename(norm) in TEST_CONFIG_FILES:
            found.append((ScopeVerdict.BLOCK, f"G6_E_SCOPE_TEST_CONFIG: {path}"))
        if not norm.startswith(owned):
            found.append((ScopeVerdict.BLOCK, f"G6_E_SCOPE_OTHER_COMPONENT: {path} is not under {', '.join(owned)}"))
        if any(fragment in norm for fragment in PROTECTED_PATH_FRAGMENTS):
            found.append((ScopeVerdict.HITL, f"G6_E_SCOPE_PROTECTED: {path}"))
    verdict = max((v for v, _ in found), key=_RANK.__getitem__, default=ScopeVerdict.ALLOW)
    return ScopeDecision(verdict, tuple(dict.fromkeys(reason for _, reason in found)))


__all__ = ["DEFAULT_ROOTS", "TEST_CONFIG_FILES", "ChangeScope", "ScopeDecision", "ScopeVerdict",
           "classify_change_scope"]
