# SPDX-License-Identifier: Apache-2.0
"""Deterministic checks on an outward draft. No model is called.

Each check answers ``pass``, ``fail``, ``not_measured`` (it applies but could not run, e.g. no contact list was given)
or ``n/a`` (nothing in this draft for it to check). A draft is ``blocked-escalated`` when a check fails and
``qualified-draft`` otherwise: passing the operator's own checks never makes a draft ``verified``, and the code says
when a check could not run or when no check ran at all. The archive called a draft ``verified`` when no check fired,
counting unmeasured steps as passes (audit 011, VEN-006).

Learned checks are :class:`Guard` s. A guard runs in shadow (its verdict is reported, never acted on) until a person
adopts it in the approval queue and ``ActionQueue.promote_guard`` returns it promoted (VEN-012). A guard built with
``promoted=True`` by hand skips that record; the queue enforces the rule at its own API, not against in-process code.
"""
from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from g6.operator.gates import amount_of

PASS, FAIL, NOT_MEASURED, NOT_APPLICABLE = "pass", "fail", "not_measured", "n/a"
QUALIFIED, BLOCKED = "qualified-draft", "blocked-escalated"

_QUOTE = re.compile(r'["“]([^"”\n]{20,})["”]')
_ATTRIBUTION = re.compile(r"\b(said|says|wrote|writes|told|tells|according to|testimonial|reviewed|customer|client|"
                          r"user)\b", re.IGNORECASE)
_PRICE = re.compile(r"(?:[A-Z]{0,3}\$|€|£)\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Finding:
    name: str
    status: str          # pass | fail | not_measured | n/a
    reason: str = ""


@dataclass(frozen=True)
class Guard:
    name: str
    check: Callable[[Mapping[str, Any]], bool | str]   # True / "pass", False / "fail", anything else: not measured
    promoted: bool = False

    def sha(self) -> str:
        """What a person adopts: the name and the check's source (its repr when there is no source)."""
        try:
            source = inspect.getsource(self.check)
        except (OSError, TypeError):
            source = repr(self.check)
        return hashlib.sha256(f"{self.name}\n{source}".encode()).hexdigest()


@dataclass(frozen=True)
class Context:
    contacts: frozenset[str] | None = None   # who may be e-mailed; None: unknown, so the recipient is not checked
    floor: float | None = None               # the lowest price a draft may quote; None: not set
    secrets: tuple[str, ...] = ()            # values that must never appear in outward text
    checks: tuple[Callable[[Mapping[str, Any]], Any], ...] = ()   # the caller's own: bool, "pass"/"fail" or Finding
    guards: tuple[Guard, ...] = ()


@dataclass(frozen=True)
class Routed:
    state: str
    code: str
    message: str
    findings: tuple[Finding, ...]
    shadow: tuple[dict[str, Any], ...] = ()

    @property
    def fired(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.findings if f.status == FAIL)

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "code": self.code, "message": self.message,
                "findings": [asdict(f) for f in self.findings], "shadow": list(self.shadow)}


def _recipient(action: Mapping[str, Any], ctx: Context) -> tuple[str, str]:
    if action.get("type") != "email":
        return NOT_APPLICABLE, ""
    if ctx.contacts is None:
        return NOT_MEASURED, "no contact list was given, so the recipient was not checked"
    to = str(action.get("to", ""))
    return (PASS, "") if to in ctx.contacts else (FAIL, f"recipient {to!r} is not a known contact")


def _witness(action: Mapping[str, Any], ctx: Context) -> tuple[str, str]:
    """An attributed quote must be a substring of the evidence the draft cites."""
    body = str(action.get("body", ""))
    cited = "\n".join(str(e) for e in action.get("evidence") or ())
    quotes = [m.group(1) for m in _QUOTE.finditer(body)
              if _ATTRIBUTION.search(body[max(0, m.start() - 60):m.end() + 60])]
    if not quotes:
        return NOT_APPLICABLE, ""
    missing = [q for q in quotes if q not in cited]
    return (FAIL, f"quote not in the cited evidence: {missing[0][:60]}") if missing else (PASS, "")


def _price_floor(action: Mapping[str, Any], ctx: Context) -> tuple[str, str]:
    prices = [float(m.group(1).replace(",", "")) for m in _PRICE.finditer(str(action.get("body", "")))]
    if not prices:
        return NOT_APPLICABLE, ""
    if ctx.floor is None:
        return NOT_MEASURED, "the draft quotes a price but no floor was given"
    low = min(prices)
    return (FAIL, f"{low:g} is below the floor of {ctx.floor:g}") if low < ctx.floor else (PASS, "")


def _secret_scan(action: Mapping[str, Any], ctx: Context) -> tuple[str, str]:
    if not ctx.secrets:
        return NOT_MEASURED, "no secret values were given to scan for"
    text = str(action.get("body", "")) + "\n" + "\n".join(str(e) for e in action.get("evidence") or ())
    hit = any(s and s in text for s in ctx.secrets)
    return (FAIL, "a known secret value appears in the outward text") if hit else (PASS, "")


def _spend_amount(action: Mapping[str, Any], ctx: Context) -> tuple[str, str]:
    if action.get("type") != "spend":
        return NOT_APPLICABLE, ""
    ok = amount_of(action.get("amount")) is not None
    return (PASS, "") if ok else (FAIL, "a spend draft needs a finite, positive amount")


_BUILTIN = (("recipient", _recipient), ("witness", _witness), ("price_floor", _price_floor),
            ("secret_scan", _secret_scan), ("spend_amount", _spend_amount))


def _status(value: Any) -> str:
    if value is True or value == PASS:
        return PASS
    if value is False or value == FAIL:
        return FAIL
    return NOT_MEASURED


def _run(fn: Callable[[Mapping[str, Any]], Any], action: Mapping[str, Any]) -> Finding:
    name = getattr(fn, "__name__", type(fn).__name__)
    try:
        out = fn(action)
    except Exception as exc:  # noqa: BLE001 - a check that raises has not answered
        return Finding(name, NOT_MEASURED, f"the check raised {type(exc).__name__}")
    return out if isinstance(out, Finding) else Finding(name, _status(out))


def route(action: Mapping[str, Any], ctx: Context | None = None) -> Routed:
    """Every check on ``action``; never ``verified``."""
    ctx = ctx or Context()
    findings = [Finding(name, *fn(action, ctx)) for name, fn in _BUILTIN]
    findings += [_run(fn, action) for fn in ctx.checks]
    shadow = []
    for guard in ctx.guards:
        found = _run(guard.check, action)
        if guard.promoted:
            findings.append(Finding(f"guard:{guard.name}", found.status, found.reason))
        else:
            shadow.append({"guard": guard.name, "would_fire": found.status == FAIL})
    fired = [f.name for f in findings if f.status == FAIL]
    unmeasured = [f.name for f in findings if f.status == NOT_MEASURED]
    if fired:
        state, code, message = BLOCKED, "G6_E_OPERATOR_DRAFT_BLOCKED", "a check failed: " + ", ".join(fired)
    elif not any(f.status == PASS for f in findings):
        state, code, message = QUALIFIED, "G6_E_OPERATOR_NO_CHECK", "no check ran on this draft; nothing was checked"
    elif unmeasured:
        state, code, message = (QUALIFIED, "G6_E_OPERATOR_NOT_MEASURED",
                                "checks that apply but could not run: " + ", ".join(unmeasured))
    else:
        state, code, message = (QUALIFIED, "G6_E_OPERATOR_CHECKS_PASSED",
                                "every check that applies passed; they are the operator's own, so a person decides")
    return Routed(state, code, message, tuple(findings), tuple(shadow))
