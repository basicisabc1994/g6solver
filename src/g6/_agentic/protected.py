# SPDX-License-Identifier: Apache-2.0
"""The protected set (agentic override kernel design, section 5 and call 10.4): G6 code no override may substitute.

* every certificate-bound file, read from the certificates G6 ships (``<component>/certificate/certificate.json``:
  the code each certificate imports, its harness, and the certified component's own package);
* the trust doors and what decides: all of ``g6.core`` (isolation, keys, consent, ledgers, the door, the registry),
  ``g6.action_gating``, ``g6.mcp``, receipts, patch review's decided fields, integrity and authority, verification
  search's authority rules, the meta-harness gate, evaluator and promotion, and this kernel;
* any path ``g6.core.adaptation_gate.protected_hits`` names (oracles, graders, judges, verifiers, gates, ledgers,
  tests and the rest of ``PROTECTED_FRAGMENTS``);
* each component's own declared verifiers (``__g6_verifiers__``: a module, or ``module:function``).

A name is judged by the object it resolves to as well as by its text: a name bound to a protected function is
protected like it, whatever the name.

A part that touches any of them is refused (``G6_E_AGENTIC_PROTECTED``); so is a part when the certificates cannot be
read, since then the set cannot be known.
"""
from __future__ import annotations

import importlib
import json
import types
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from g6.core.adaptation_gate import protected_hits

PROTECTED = "G6_E_AGENTIC_PROTECTED"
#: Modules (and everything under them) that are trust doors or decide an outcome.
TRUST_DOORS = (
    "g6.core", "g6.action_gating", "g6.mcp", "g6._agentic", "g6.evidence", "g6.invariants", "g6.guards",
    "g6.epistemic_integrity", "g6.workforce.receipt_store", "g6.harness.patch_review", "g6.harness.patch_report",
    "g6.harness.verification_search", "g6.harness.verification_controller", "g6.harness.verification_formal",
    "g6.harness.verification_proposer", "g6.harness.verification_templates", "g6.harness.confirmed_checks",
    "g6.harness.completion_loop", "g6.metaharness", "g6.self_training", "g6.navigator.authority",
    "g6.reasoning.budget", "g6.agents._runtime",
)
#: Words that mark a trust door in a module's path, beside ``PROTECTED_FRAGMENTS``.
DOOR_WORDS = ("confine", "consent", "keys", "receipt", "sandbox", "authority", "custody", "integrity", "door")


@lru_cache(maxsize=1)
def certificate_bound() -> tuple[frozenset[str], tuple[str, ...]]:
    """The files G6's certificates bind (``g6/...`` paths) and the certified components' package folders (``g6/.../``).
    Raises ``ValueError`` when a certificate cannot be read."""
    import g6

    package = Path(g6.__file__).resolve().parent
    files: set[str] = set()
    folders: list[str] = []
    for path in sorted(package.rglob("certificate/certificate.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            files.update(record["imported_code"])
            files.update(record["harness_sha256"])
            folders.append(record["component"].replace(".", "/") + "/")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"the certificate {path.parent.parent.name} cannot be read: {exc}") from exc
    if not files:
        raise ValueError("no certificate was found, so the certificate-bound files are not known")
    return frozenset(files), tuple(folders)


def _module_of(name: str) -> str:
    return name.split(":", 1)[0]


def _resolve(name: str) -> Any:
    """The object ``module:qualname`` (or a module) names, or None when it cannot be found."""
    module_name, _, qualname = name.partition(":")
    try:
        target: Any = importlib.import_module(module_name)
        for part in qualname.split(".") if qualname else ():
            target = getattr(target, part)
    except (ImportError, AttributeError, ValueError, TypeError):
        return None
    return target


def _own_name(target: Any) -> str:
    """``module:qualname`` of the code ``target`` is (where it was defined), or ""."""
    module, qualname = getattr(target, "__module__", None), getattr(target, "__qualname__", None)
    return f"{module}:{qualname}" if isinstance(module, str) and isinstance(qualname, str) else ""


def _verifier_objects(verifiers: Iterable[str]) -> list[Any]:
    """The objects the declared verifiers name: a function, or every function a declared module holds."""
    found: list[Any] = []
    for declared in verifiers:
        target = _resolve(declared)
        if isinstance(target, types.ModuleType):
            found += [value for value in vars(target).values() if isinstance(value, types.FunctionType)]
        elif target is not None:
            found.append(target)
    return found


def reason(function: str, *, verifiers: Iterable[str] = ()) -> str:
    """"" when ``function`` (``module:qualname``) is outside the protected set, else why it is protected (the code
    first). Identity is decided by the object the name resolves to, not by the name: a name bound to a declared
    verifier, or to a function of a trust door or a certificate-bound file, is protected like that function."""
    verifiers = tuple(verifiers)
    said = _by_name(function, verifiers)
    if said:
        return said
    target = _resolve(function)
    if target is None:
        return ""
    own = _own_name(target)
    if own and own != function:
        said = _by_name(own, verifiers)
        if said:
            return f"[{PROTECTED}] {function} is {own}: {said[len(PROTECTED) + 3:]}"
    if any(target is verifier for verifier in _verifier_objects(verifiers)):
        return f"[{PROTECTED}] {function} is a verifier its component declares, under another name"
    return ""


def _by_name(function: str, verifiers: tuple[str, ...]) -> str:
    module = _module_of(function)
    if not module.startswith("g6.") or not all(part.isidentifier() for part in module.split(".")):
        return f"[{PROTECTED}] {function} is not a function of G6's own code"
    path = module.replace(".", "/") + ".py"
    try:
        files, folders = certificate_bound()
    except ValueError as exc:
        return f"[{PROTECTED}] {exc}; no part can be substituted"
    if path in files or module.replace(".", "/") + "/__init__.py" in files or path.startswith(folders):
        return f"[{PROTECTED}] {function} is in a certificate-bound file ({path})"
    door = next((d for d in TRUST_DOORS if module == d or module.startswith(d + ".")), None)
    if door:
        return f"[{PROTECTED}] {function} is in a trust door or what decides an outcome ({door})"
    target = f"{path}:{function.split(':', 1)[-1] if ':' in function else ''}"
    if protected_hits((target,)) or any(word in path for word in DOOR_WORDS):
        return f"[{PROTECTED}] {function} names what judges or guards a result ({target})"
    for declared in verifiers:
        if function == declared or module == declared or module.startswith(declared + ".") or (
                ":" in declared and function.startswith(declared + ".")):
            return f"[{PROTECTED}] {function} is a verifier its component declares ({declared})"
    return ""


__all__ = ["DOOR_WORDS", "PROTECTED", "TRUST_DOORS", "certificate_bound", "reason"]
