# SPDX-License-Identifier: Apache-2.0
"""DutyOfCareBlock: the registry and MCP door (``g6_invoke``) to the jobs family's duty-of-care code.

:meth:`DutyOfCareBlock.screen_raw` runs before typed validation (``g6.core.registry``), on every door, and again at the
top of :meth:`DutyOfCareBlock.infer` for a caller that skips the registry (DUTY_OF_CARE-02): it asks the run's
crisis lock, scans every key and leaf of the raw parameters (within bounds; past them the trigger cannot run, which is
treated as a possible crisis), and checks a raw PHQ-9 item 9, so a wrongly shaped call carrying crisis words still gets
the crisis result. Once a crisis is found for a subject in a run, every later op naming that run_id and subject
returns the same crisis result (DUTY_OF_CARE-01). Both are the caller's words: a call naming another run or subject is
not locked. The lock is a plain in-memory object per run (the jobs engine will hold its own and set both ids itself);
it is never written to disk. A crisis finding is ``blocked-escalated`` with ``G6_E_DUTY_OF_CARE_CRISIS``, the response
and the dated directory, and nothing else: nothing from the input is repeated. Every other answer tops out at
``qualified-draft``. A missing input is ``blocked-escalated``, never a default. Unknown fields (a clinician id, an
approval, a risk flag) are refused by the typed input (``G6_E_INPUT_VALIDATION``) and never read.

No op reaches the network, writes a file or runs a program, so none is sensitive.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from g6.core.ai_block import AIBlock
from g6.core.block_contract import FailureMode
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.jobs._care import crisis, directory, instruments
from g6.jobs._care.envelope import (
    DIRECTORY,
    DIRECTORY_STALE,
    DIRECTORY_UNAVAILABLE,
    INSTRUMENT_LIST,
    INSTRUMENT_UNAVAILABLE,
    MISSING,
    NO_LISTED_WORDS,
    REGION_NOT_COVERED,
    SCORE_COMPUTED,
    CareResult,
    answer,
    refuse,
)

_OPS: dict[str, str] = {
    "screen": "screen(fields, run_id, subject, region): every field scanned for listed crisis words; a finding gives "
              "the crisis result, the dated directory, and a lock on that subject for the run",
    "instruments": "instruments(): each instrument with its licence state; those not settled are refused, with why",
    "score": "score(instrument, responses, run_id, subject): the item scores added up as the form adds them, "
             "'score computed'; any PHQ-9 item 9 answer other than 0 gives the crisis result",
    "directory": "directory(region): the dated crisis and helpline numbers for a region, or every region, emergency "
                 "numbers first",
    "help": "help(): these lines",
}
_ID = r"^[A-Za-z0-9._:-]*$"
_Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_Text = Annotated[str, StringConstraints(max_length=100_000)]
#: The run locks this door holds, by run id, in memory for the life of the process. Nothing removes one.
_LOCKS: dict[str, crisis.CrisisLock] = {}


class DutyOfCareInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["screen", "instruments", "score", "directory", "help", "ops"] = "help"
    fields: dict[_Name, _Text] | None = Field(None, max_length=100)
    instrument: str | None = Field(None, max_length=40)
    responses: list[StrictInt | None] | None = Field(None, max_length=100)
    region: str | None = Field(None, pattern=r"^[A-Za-z]{2}$")
    subject: str = Field("", max_length=100, pattern=_ID)
    run_id: str = Field("", max_length=100, pattern=_ID)
    request_id: str = Field("", max_length=100)


#: Bounds on the raw walk, each above what the typed input accepts (100 fields of a 200-character name and a
#: 100,000-character value, 100 answers, short ids), so a valid call is never refused by them.
MAX_DEPTH = 32
MAX_LEAVES = 20_000
MAX_CHARS = 10_100_000
_VALID_ID = re.compile(r"[A-Za-z0-9._:-]{1,100}")
_OPS_ALL = ("screen", "instruments", "score", "directory", "help", "ops")


@dataclass(frozen=True)
class _Call:
    """What a crisis answer needs from a call: its op, its ids when they are valid, and a covered region or none."""

    op: str
    run_id: str = ""
    subject: str = ""
    region: str | None = None
    request_id: str = ""

    @property
    def named(self) -> bool:
        return bool(self.run_id and self.subject)

    def ids(self) -> dict[str, str]:
        return {"request_id": self.request_id, "run_id": self.run_id}


def _call(d: DutyOfCareInput) -> _Call:
    return _Call(d.op, d.run_id, d.subject, d.region, d.request_id)


def _ids(d: DutyOfCareInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _named(d: DutyOfCareInput) -> bool:
    return bool(d.run_id and d.subject)


def _fire(c: _Call, evidence: list[dict[str, Any]], why: str) -> CareResult:
    """The crisis result for this call, and the lock engaged when the call names a valid run and subject."""
    reason = (f"{why} This goes to a person now: show the response and the numbers, and hand the conversation to "
              "someone who can help.")
    if c.named:
        reason += (" Every later op naming this run_id and subject returns this result; a call naming another run or "
                   "subject is not locked.")
        _LOCKS.setdefault(c.run_id, crisis.CrisisLock()).engage(
            c.subject, {"operation": c.op, "evidence": evidence, "region": c.region, "reason": reason})
    else:
        reason += (" No lock was set: name a run and a subject (run_id and subject) so that every later op naming "
                   "them returns this result.")
    return crisis.crisis_result(c.op, evidence, region=c.region, locked=c.named, reason=reason, **c.ids())


def _locked(c: _Call, record: dict[str, Any]) -> CareResult:
    evidence = [*record["evidence"], {"kind": "crisis_lock", "first_op": record["operation"], "rule": "crisis_lock"}]
    return crisis.crisis_result(c.op, evidence, region=record["region"], locked=True, reason=record["reason"],
                                **c.ids())


def _found(finding: crisis.CrisisFinding) -> str:
    return f"Listed crisis words were found ({', '.join(sorted({m.category.replace('_', ' ') for m in finding.matches}))})."


class _Unscannable(Exception):
    """The raw input is past the scan's bounds or is not JSON: the trigger cannot run over it."""


def _flatten(raw: Mapping[str, Any]) -> dict[str, str]:
    """Every key and every leaf of the raw parameters as text, in walk order, each labelled by its position (a
    parameter this block has is named; any other is ``param<i>``), so no label repeats the caller's own keys.
    Numbers and booleans are read as their text; ``None`` holds nothing. Raises :class:`_Unscannable` past
    :data:`MAX_DEPTH`, :data:`MAX_LEAVES` or :data:`MAX_CHARS`, or for anything that is not a JSON type."""
    out: dict[str, str] = {}
    used = [0, 0]

    def add(label: str, text: str) -> None:
        used[0] += 1
        used[1] += len(text)
        if used[0] > MAX_LEAVES or used[1] > MAX_CHARS:
            raise _Unscannable("past the scan's bounds")
        out[label] = text

    def walk(value: Any, label: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise _Unscannable("nested past the scan's bounds")
        if value is None:
            return
        if isinstance(value, bool):
            add(label, "true" if value else "false")
        elif isinstance(value, (int, float, str)):
            add(label, value if isinstance(value, str) else repr(value))
        elif isinstance(value, Mapping):
            for i, (key, item) in enumerate(value.items()):
                if not isinstance(key, str):
                    raise _Unscannable("a key that is not text")
                add(f"{label}[{i}].key", key)
                walk(item, f"{label}[{i}]", depth + 1)
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                walk(item, f"{label}[{i}]", depth + 1)
        else:
            raise _Unscannable("a value that is not a JSON type")

    for i, (key, value) in enumerate(raw.items()):
        if not isinstance(key, str):
            raise _Unscannable("a parameter name that is not text")
        label = key if key in DutyOfCareInput.model_fields else f"param{i}"
        if label != key:
            add(f"{label}.key", key)
        walk(value, label, 1)
    return out


def _covered_region(value: Any) -> str | None:
    """The raw region when it is a two-letter code the directory covers; anything else is an unstated region."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2}", value):
        return None
    try:
        data = directory.load()
    except Exception:  # noqa: BLE001 - no directory: the region cannot be checked, so it is unstated
        return None
    code = data.get("aliases", {}).get(value.upper(), value.upper())
    return code if code in data["regions"] else None


_ABSENT = object()


def _raw_item_9(responses: Any) -> Any:
    """A raw PHQ-9 item 9, whatever its type: the ninth of a list, or a mapping's item 9; :data:`_ABSENT` if none."""
    if isinstance(responses, (list, tuple)):
        return responses[8] if len(responses) > 8 else _ABSENT
    if isinstance(responses, Mapping):
        return next((responses[k] for k in ("9", "item9", "item_9", "item 9") if k in responses), _ABSENT)
    return _ABSENT


def _item_9_is_zero(value: Any) -> bool:
    return value is None or value == "0" or (type(value) is int and value == 0)


def _needs_names(d: DutyOfCareInput) -> CareResult:
    return refuse(d.op, MISSING, f"{d.op} needs run_id and subject: the crisis lock is kept for a subject in a run, "
                                 "and nothing is assumed in their place", missing=[f for f in ("run_id", "subject")
                                                                                   if not getattr(d, f)], **_ids(d))


def _screen(d: DutyOfCareInput) -> CareResult:
    if not d.fields:
        return refuse("screen", MISSING, "screen needs fields: the text to scan, as named fields", missing=["fields"],
                      **_ids(d))
    try:
        finding = crisis.scan(_flatten({"fields": d.fields}))
    except Exception as exc:  # noqa: BLE001 - G4: a trigger that cannot run is treated as a possible crisis
        return crisis.check_failed("screen", exc, region=d.region, **_ids(d))
    if finding.fired:
        return _fire(_call(d), finding.evidence(), _found(finding))
    if not _named(d):
        return _needs_names(d)
    return answer("screen", NO_LISTED_WORDS,
                  "No listed crisis words were found in the fields given. This is not an assessment of the person: "
                  "the trigger catches stated words only, so a person in crisis can be missed.",
                  evidence=[{"kind": "crisis_trigger", "rule": "crisis_trigger", "fired": False,
                             "fields_scanned": list(finding.fields_scanned)}],
                  crisis_words_found=False, fields_scanned=list(finding.fields_scanned),
                  categories_checked=list(crisis.CATEGORIES),
                  excepted=[{"exception": e.exception, "field": e.field} for e in finding.excepted], **_ids(d))


def _score(d: DutyOfCareInput) -> CareResult:
    if not d.instrument:
        return refuse("score", MISSING, "score needs instrument: one the instruments op lists", missing=["instrument"],
                      **_ids(d))
    try:
        outcome = instruments.score(d.instrument, d.responses)
    except Exception as exc:  # noqa: BLE001 - G4: no score is given in place of data that cannot be read
        return refuse("score", INSTRUMENT_UNAVAILABLE, f"{d.instrument}'s data could not be read ({type(exc).__name__}"
                                                       f": {exc}); no score is given", **_ids(d))
    if outcome.kind == "crisis":
        evidence = [{"kind": "instrument_item", "instrument": outcome.instrument, "item": outcome.item,
                     "rule": "phq9_item9", "source": outcome.source}]
        return _fire(_call(d), evidence, outcome.reason)
    if outcome.kind == "refused":
        return refuse("score", outcome.code, outcome.reason, instrument=outcome.instrument, **_ids(d))
    if not _named(d):
        return _needs_names(d)
    return answer("score", SCORE_COMPUTED, outcome.reason,
                  evidence=[{"kind": "instrument", "instrument": outcome.instrument, "source": outcome.source,
                             "rule": f"{outcome.instrument}_total"}],
                  instrument=outcome.instrument, total=outcome.total, item_scores=list(outcome.item_scores), **_ids(d))


def _instruments(d: DutyOfCareInput) -> CareResult:
    try:
        listed = instruments.catalogue()
    except Exception as exc:  # noqa: BLE001 - G4
        return refuse("instruments", INSTRUMENT_UNAVAILABLE, f"the instrument data could not be read "
                                                             f"({type(exc).__name__}: {exc})", **_ids(d))
    return answer("instruments", INSTRUMENT_LIST, "Each shipped instrument with its licence state. Only those whose "
                  "licence is settled are scored; the others are refused until their licence entry is settled.",
                  evidence=[{"kind": "licence", "instrument": i["id"], "state": i["licence_state"]} for i in listed],
                  instruments=listed, **_ids(d))


def _directory(d: DutyOfCareInput) -> CareResult:
    try:
        view = directory.lookup(d.region)
    except Exception as exc:  # noqa: BLE001 - G4
        return refuse("directory", DIRECTORY_UNAVAILABLE, f"the directory could not be read ({type(exc).__name__}: "
                                                          f"{exc})", **_ids(d))
    evidence = [{"kind": "directory_entry", "source": e["source"], "retrieved": e["retrieved"],
                 "age_days": e["age_days"]} for e in view["entries"]]
    if view["region_covered"] is False:
        return refuse("directory", REGION_NOT_COVERED, view["note"], evidence=evidence, directory=view, **_ids(d))
    return answer("directory", DIRECTORY_STALE if view["stale"] else DIRECTORY, view["note"], evidence=evidence,
                  directory=view, **_ids(d))


_RUN: dict[str, Callable[[DutyOfCareInput], CareResult]] = {
    "screen": _screen, "score": _score, "instruments": _instruments, "directory": _directory}


@block_contract(
    purpose="duty_of_care",
    outputs=("CareResult",),
    known_failure_modes=(FailureMode("no_finding_read_as_safe", detector="", severity="high"),),
    mitigates_failure_modes=("missed_risk_signs",),
    verification_method="tier1_review_pending",
)
@component_maturity(
    "beta",
    reason="scans stated words for crisis, scores the PHQ-9 and GAD-7 as their forms print them, gives a dated "
           "directory; qualified-draft at best; no qualified person has reviewed it",
)
class DutyOfCareBlock(AIBlock[DutyOfCareInput, dict, dict]):
    """Screen text for crisis words, list or score instruments, or give the directory. Never ``verified``."""

    def __init__(self, name: str = "duty_of_care", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def _ok(self, op: str, result: CareResult) -> Result[dict]:
        return Result.ok({"component": self.name, "op": op, **result.model_dump(mode="json")})

    def screen_raw(self, raw: dict[str, Any]) -> Result[dict] | None:
        """Before typed validation, on every door (``g6.core.registry``): (a) a locked run_id and subject get their
        crisis result; (b) every key and leaf of the raw parameters is scanned, and a finding gets the crisis result,
        the lock engaging when the raw run_id and subject are valid ids; past the scan's bounds, or for input that is
        not JSON, the trigger cannot run, which gets the check-failed crisis result; (c) a raw PHQ-9 item 9 that is
        present and is anything but 0, "0" or None gets the crisis result. Otherwise ``None``: typed validation
        decides, and raw input never reaches :meth:`infer` except through it."""
        op = raw.get("op") if raw.get("op") in _OPS_ALL else "screen"
        run_id, subject, request_id = raw.get("run_id"), raw.get("subject"), raw.get("request_id")
        valid = all(isinstance(v, str) and _VALID_ID.fullmatch(v) for v in (run_id, subject))
        call = _Call(op, run_id if valid else "", subject if valid else "", _covered_region(raw.get("region")),
                     request_id if isinstance(request_id, str) and _VALID_ID.fullmatch(request_id) else "")
        record = crisis.locked(_LOCKS, run_id, subject)
        if record is not None:
            return self._ok(op, _locked(call, record))
        try:
            finding = crisis.scan(_flatten(raw))
        except Exception as exc:  # noqa: BLE001 - G4: a trigger that cannot run is treated as a possible crisis
            return self._ok(op, crisis.check_failed(op, exc, region=call.region, **call.ids()))
        if finding.fired:
            return self._ok(op, _fire(call, finding.evidence(), _found(finding)))
        name = raw.get("instrument")
        if op == "score" and isinstance(name, str) and re.sub(r"[^a-z0-9]", "", name.lower()) == "phq9":
            item = _raw_item_9(raw.get("responses"))
            if item is not _ABSENT and not _item_9_is_zero(item):
                evidence = [{"kind": "instrument_item", "instrument": "phq9", "item": 9, "rule": "phq9_item9",
                             "source": "phq9-form"}]
                return self._ok(op, _fire(call, evidence, "PHQ-9 item 9 was answered with something other than 0: "
                                                          "any such answer goes to a person, whatever the total."))
        return None

    def infer(self, data: DutyOfCareInput) -> Result[dict]:
        # The lock and the crisis words come before anything else: no op, and no parameter an op takes, answers past
        # them. The raw screen runs here on every field of every op too, so a caller that skips the registry's raw
        # screen (a direct Python call) is answered as one through it is (DUTY_OF_CARE-02).
        early = self.screen_raw(data.model_dump(mode="json", exclude_none=True))
        if early is not None:
            return early
        record = crisis.locked(_LOCKS, data.run_id, data.subject)
        if record is not None:
            return self._ok(data.op, _locked(_call(data), record))
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["DutyOfCareBlock", "DutyOfCareInput"]
