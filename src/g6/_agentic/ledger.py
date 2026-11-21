# SPDX-License-Identifier: Apache-2.0
"""The local record of overrides (design section 4 step 6): one entry per override run, appended to a hash-chained
ledger (``g6.core.ledger.ChainLedger``, used unchanged) at ``<G6 state folder>/agentic/overrides.jsonl``.

The file is owned by the same user as G6: it records what was done, it does not stop that user's own programs from
writing it. An entry has a closed schema (:class:`Entry`): codes, names from G6's own declarations, digests, counts
and historical person metadata. New entries record remote provider consent, local model admission, consent refusal
or the caller's code allowance, with the observed provider where applicable and an empty name; no override dialog
is claimed.
No parameter, result, exception text, refusal detail or reason is kept. The writer refuses any other field or value,
and then nothing is written. :func:`entry` keeps that much of an override's record. It may direct where the loop looks
next; it never counts as evidence that a change works (design 10.10)."""
from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from g6._agentic import decode
from g6.core.ledger import ChainLedger
from g6.core.storage import state_path

KIND = "agentic_override"
_LOCK = threading.Lock()
#: The longest line of the ledger file read back (an entry is a few kilobytes).
LINE_CHARS = 1_000_000
#: A stable code, or none.
_CODE = r"^(G6_E_[A-Z0-9_]{1,80})?$"
#: A name from G6's own declarations: a component, op, part, ``module:function``, check, status or model.
_NAME = r"^[A-Za-z0-9_.:/@+\-]{0,200}$"
_HEX = r"^([0-9a-f]{64})?$"
Name = Annotated[str, Field(pattern=_NAME)]
Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
#: Why an override ended without a substitute, as the kernel names it.
Reason = Literal["", "budget_exhausted", "child_failed", "isolation_unavailable", "model_failed", "value_invalid",
                 "substitute_failed", "recheck_failed", "ledger_failed", "kernel_failed"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Trigger(_Closed):
    kind: Literal["refused", "failed", "asked"]
    code: str = Field("", pattern=_CODE)
    exception: str = Field("", pattern=_NAME)


class Person(_Closed):
    answer: Literal["accepted in the client's dialog", "allowed in code", "provider consent", "local model",
                    "refused by the consent check"]
    name: str = Field(max_length=200)   # as the person gave it: the one free text an entry holds
    authenticated: Literal[False]
    provider: Name = ""   # optional for historical entries; never stored in the name field


class Check(_Closed):
    check: str = Field(pattern=_NAME)
    passed: StrictBool


class Limits(_Closed):
    max_model_calls: StrictInt
    max_output_chars: StrictInt
    max_wall_clock_s: StrictFloat


class Digests(_Closed):
    source: str = Field(pattern=_HEX)
    question: str = Field(pattern=_HEX)
    params: str = Field(pattern=_HEX)
    result: str = Field(pattern=_HEX)
    prompts: list[Hex] = Field(max_length=64)


class Cost(_Closed):
    model_calls: StrictInt
    failed_model_calls: StrictInt
    output_chars: StrictInt
    child_runs: StrictInt
    seconds: StrictFloat


class Entry(_Closed):
    outcome: Literal["substituted", "degraded"]
    reason: Reason = ""
    component: str = Field(pattern=_NAME)
    op: str = Field(pattern=_NAME)
    part: str = Field(pattern=_NAME)
    functions: list[Name] = Field(max_length=32)
    mode: Literal["prompt"]
    model: str = Field(pattern=_NAME)
    trigger: Trigger
    person: Person
    budget: Limits
    checks: list[Check] = Field(max_length=64)
    rechecked: StrictBool
    recheck_status: str = Field("", pattern=_NAME)
    digests: Digests
    cost: Cost


def path() -> Path:
    return state_path("agentic", "overrides.jsonl")


def entry(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the ledger keeps of an override's ``record``: the fields of :class:`Entry`, nothing else (the trigger
    without its message, the checks without their detail, the re-check by its status only)."""
    trigger = record.get("trigger") or {}
    recheck = record.get("recheck")
    status = recheck.get("status") if isinstance(recheck, Mapping) else ""
    budget = record.get("budget") or {}
    cost = record.get("cost") or {}
    return {
        "outcome": record.get("outcome"), "reason": record.get("reason", ""), "component": record.get("component"),
        "op": record.get("op"), "part": record.get("part"), "functions": list(record.get("functions") or ()),
        "mode": record.get("mode"), "model": record.get("model"),
        "trigger": {k: trigger[k] for k in ("kind", "code", "exception") if k in trigger},
        "person": dict(record.get("person") or {}),
        "budget": {**budget, "max_wall_clock_s": float(budget.get("max_wall_clock_s", 0))},
        "checks": [{"check": c.get("check"), "passed": c.get("passed")} for c in record.get("checks") or ()],
        "rechecked": bool(record.get("rechecked")),
        "recheck_status": status if isinstance(status, str) and re.fullmatch(r"[a-z_]{0,80}", status) else "other",
        "digests": dict(record.get("digests") or {}),
        "cost": {**cost, "seconds": float(cost.get("seconds", 0))},
    }


def _readable(target: Path) -> None:
    """Every line of the file decoded within ``g6._agentic.decode``'s bounds (at most :data:`LINE_CHARS` characters,
    nested at most ``decode.MAX_DEPTH`` deep) before ``ChainLedger`` reads it, so its decoder never meets a line it
    cannot decode without failing (a line nested too deep raises ``RecursionError`` there); ``ValueError`` otherwise.
    The file is the user's: any program of theirs can write it."""
    if not target.exists():
        return
    text = target.read_bytes().decode("utf-8")      # not UTF-8: UnicodeDecodeError, a ValueError
    for number, line in enumerate(text.split("\n"), 1):   # as ChainLedger splits it
        if line.strip():
            try:
                decode.loads(line, max_chars=LINE_CHARS)
            except decode.Undecodable as exc:
                raise ValueError(f"line {number} of the override ledger was not read: {exc}"[:300]) from None


def append(body: Mapping[str, Any]) -> str:
    """Append one override's entry; its entry hash. Raises ``ValueError`` when ``body`` holds any field outside
    :class:`Entry` or a value outside its pattern, or a line of the file is not read within bounds (:func:`_readable`),
    and ``g6.core.ledger.LedgerError`` (or ``OSError``) when it cannot be written; then nothing is."""
    checked = Entry.model_validate(dict(body)).model_dump(mode="json")
    with _LOCK:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        _readable(target)
        return ChainLedger(target).append(KIND, uuid.uuid4().hex, checked)


__all__ = ["KIND", "Entry", "append", "entry", "path"]
