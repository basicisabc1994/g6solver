# SPDX-License-Identifier: Apache-2.0
"""g6.triage — what kind of Python error is this, and may a fix be tried without a person? (audit 022 §11)

Capability label: ``implemented``.

    from g6.triage import classify
    t = classify("KeyError", "'timeout'")      # category "key", posture "auto_heal", a qualified-draft

The category comes from the exception's class (``ConnectionResetError`` is a ``ConnectionError``, so ``network``); the
message only hints. Only built-in classes are known: nothing named in an error string is imported (importing runs
code), so ``json.decoder.JSONDecodeError`` is ``unknown`` and goes to review. When the message points at another category (a ``KeyError`` saying a file was not found), the
two are reported and the posture rises to ``review``. Postures are ordered ``auto_heal`` < ``review`` < ``escalate``,
and every rule only raises one.

What it is not: a diagnosis. A class name and a fixed hint sentence cannot establish a root cause, so every result is a
``qualified-draft`` (SD-005: the archive labelled the same classification ``verified``). :func:`refine` takes the
caller's model function; the model may name a category the table could not and raise the posture, never lower it, and
anything it says that cannot be read leaves the table's answer standing. No model is called by default.
"""
from __future__ import annotations

import builtins
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

POSTURES = ("auto_heal", "review", "escalate")
#: Base exception classes to categories, most specific first (the class's MRO is walked against this order).
_BASES: tuple[tuple[type[BaseException], str], ...] = (
    (SyntaxError, "syntax"), (ImportError, "import"), (NameError, "name"), (TypeError, "type"),
    (AttributeError, "attribute"), (KeyError, "key"), (IndexError, "index"), (RecursionError, "recursion"),
    (ArithmeticError, "arithmetic"), (ConnectionError, "network"), (TimeoutError, "network"), (OSError, "io"),
    (MemoryError, "resource"), (UnicodeError, "value"), (ValueError, "value"), (RuntimeError, "runtime"))
CATEGORIES = tuple(dict.fromkeys(c for _, c in _BASES)) + ("unknown",)
HINTS = {
    "syntax": "the code does not parse: a missing colon, bracket or quote is common",
    "import": "a module is missing or failed to import: check the installation and the import path",
    "name": "a name is used before it is defined, or misspelled",
    "type": "a value of the wrong type was passed: check the call's arguments",
    "attribute": "the object has no such attribute: check its type and spelling",
    "key": "a mapping has no such key: check the key exists before reading it",
    "index": "a sequence index is out of range: check its length",
    "recursion": "recursion went too deep: check the base case",
    "arithmetic": "a division by zero or a numeric overflow",
    "network": "a connection failed or timed out: check the address, the network and the timeout",
    "io": "a file system error: check the path, its permissions and that it exists",
    "resource": "the process ran out of memory",
    "value": "an argument had the right type but a value the function cannot use",
    "runtime": "a general runtime error: read the message itself",
    "unknown": "not a known Python exception class: read the message and the traceback",
}
#: What a message says that points at a category, whatever the class.
_MESSAGE_HINTS = ((re.compile(r"no such file|not found|does not exist|permission denied", re.I), "io"),
                  (re.compile(r"connection (?:refused|reset)|timed out|name or service not known", re.I), "network"),
                  (re.compile(r"out of memory|cannot allocate", re.I), "resource"))
#: Categories that touch the world outside the code; a fix is not tried without a person.
_OUTSIDE = frozenset({"network", "io", "resource"})


@dataclass(frozen=True)
class Triage:
    category: str
    hint: str
    posture: str
    reasons: tuple[str, ...] = ()
    source: str = "table"              # table | model (a model named the category)
    completion_state: str = "qualified-draft"

    @property
    def requires_review(self) -> bool:
        return self.posture != "auto_heal"


def _category_of(error_type: str) -> str:
    name = error_type.strip().rsplit(".", 1)[-1]
    cls = getattr(builtins, name, None)
    if not (isinstance(cls, type) and issubclass(cls, BaseException)):
        return "unknown"
    for base, category in _BASES:
        if issubclass(cls, base):
            return category
    return "unknown"


def clamp(baseline: str, candidate: str) -> str:
    """The stricter of two postures; an unknown posture raises (callers fail closed to their baseline)."""
    for posture in (baseline, candidate):
        if posture not in POSTURES:
            raise ValueError(f"unknown posture {posture!r}")
    return max(baseline, candidate, key=POSTURES.index)


def classify(error_type: str, message: str = "", traceback: str = "", *, safety_approved: bool | None = None) -> Triage:
    """The category, a hint, and the posture; ``safety_approved=False`` (a safety check refused the fix) escalates."""
    category, reasons, posture = _category_of(error_type), [], "auto_heal"
    hinted = [c for pattern, c in _MESSAGE_HINTS if pattern.search(message)]
    if category == "unknown":
        reasons.append("unknown_exception_class")
        posture = clamp(posture, "review")
    if hinted and category not in hinted:
        reasons.append(f"message_points_at_{hinted[0]}")
        posture = clamp(posture, "review")
    if category in _OUTSIDE:
        reasons.append("touches_the_world_outside_the_code")
        posture = clamp(posture, "review")
    if safety_approved is False:
        reasons.append("safety_not_approved")
        posture = clamp(posture, "escalate")
    hint = HINTS[category]
    frame = traceback.strip().splitlines()[-1].strip()[:100] if "line " in traceback else ""
    return Triage(category, hint + (f" (last frame: {frame})" if frame else ""), posture, tuple(reasons))


def refine(triage: Triage, *, complete: Callable[[str], str], error_type: str, message: str) -> Triage:
    """Ask the caller's model (``complete(prompt) -> JSON text``) for ``{"category", "posture"}``. It may name the
    category only where the table said ``unknown``, and may only raise the posture; an unreadable reply changes nothing."""
    prompt = (f"Classify this Python error. Reply with JSON {{\"category\": one of {list(CATEGORIES)}, \"posture\": one of"
              f" {list(POSTURES)}}}.\nerror type: {error_type}\nmessage: {message[:500]}")
    try:
        reply = json.loads(complete(prompt))
        category, posture = str(reply["category"]).strip().lower(), str(reply["posture"]).strip().lower()
        if category not in CATEGORIES:
            raise ValueError(category)
        posture = clamp(triage.posture, posture)
    except Exception:  # noqa: BLE001 - any unreadable reply leaves the table's answer standing
        return replace(triage, reasons=(*triage.reasons, "model_reply_unreadable"))
    if triage.category == "unknown" and category != "unknown":
        return replace(triage, category=category, hint=HINTS[category], posture=posture, source="model",
                       reasons=(*triage.reasons, "category_named_by_model"))
    return replace(triage, posture=posture)


__all__ = ["CATEGORIES", "HINTS", "POSTURES", "Triage", "clamp", "classify", "refine"]
