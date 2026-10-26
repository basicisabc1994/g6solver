# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Reuse of verified results (Phase E Part 1, E2b; Ledger §13 step 2; Doctrine §10).

A unit may read a saved result of an earlier mission of the same account (``artifact:<id>`` in its inputs)
instead of having it made again. A saved result is reused only while every rule below holds; ``check`` raises
``ReuseRefused`` with the first that does not, in plain words for the person (plan ruling R8):

* permitted: it is one of this account's results (another account's reads exactly like an unknown one);
* current: it is still the latest output of the unit that made it;
* consequential enough: its mission was at least as consequential as the reader's (its risk tier stands in for
  "provenance meeting the acceptance contract" until E2c);
* verified: an independent check verified exactly that content, and no executed check failed it;
* provenance: it records how it was made (results saved before this change do not, so they are not reused);
* the same contract: the tool that made it and a check that verified it still have the contract digests they had
  then (a changed contract is a version change: the result is made again, never re-verified in place);
* the same input digests: every input it was made from is still the latest output of its unit with the same digest
  (a saved result it was itself made from is checked by these same rules, at most ``MAX_DEPTH`` deep);
* verified premises (Gate W: a result is trusted no further than what it rests on): the latest output of every unit of
  its mission it rests on (what it was made from, and what each verifier-grade check that verified it read, given the
  mission's ``plan``) passes these same rules too, at most ``MAX_DEPTH`` deep;
* within ``valid_for``: no older than the shortest ``valid_for_seconds`` its tool's or its check's contract
  declares, else ``REUSE_TTL_SECONDS``.

``lineage`` follows the same recorded provenance, with the same reads and bound, to name every op an output's ancestry
was made by: a check is independent only of what its op made none of (Security Brief 3, SR3-1).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from g6.workforce.capabilities import ParamContract
from g6.workforce.contracts import AUTHORITY_COPY, VERIFIED, VERIFIER_GRADE
from g6.workforce.plans import ARTIFACT_PREFIX, RISK_TIERS, Plan

#: How long a verified result may be reused when no contract behind it declares ``valid_for_seconds`` (ruling R8).
REUSE_TTL_SECONDS = 24 * 3600
#: How many saved results deep a chain of provenance is followed.
MAX_DEPTH = 3


class ReuseRefused(ValueError):
    """Why a saved result may not be used: a clause that completes "cannot be reused: ..."."""


@dataclass(frozen=True)
class Reused:
    artifact: dict
    mission_id: str
    verdict_ids: tuple[str, ...]
    valid_until: str

    def provenance(self) -> dict:
        """What a unit that reads it records about it."""
        return {"artifact_id": self.artifact["artifact_id"], "sha256": self.artifact.get("sha256", ""),
                "mission_id": self.mission_id, "verdicts": list(self.verdict_ids), "valid_until": self.valid_until}


def _period(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{seconds} seconds"


def _answered_call(ledger: Any, mission_id: str, run_id: str) -> dict | None:
    """The call a verify run recorded as answered (its check's), or None."""
    try:
        run = ledger.run(mission_id, run_id)
    except LookupError:
        return None
    calls = [c for c in json.loads(run.get("calls_json") or "[]") if isinstance(c, dict)]
    return next((c for c in calls if c.get("refused") is False), None)


#: The bench's ops: their pass can be reused only when their tests ran confined by the operating system.
_BENCH_OPS = frozenset({("workforce", "run_tests"), ("workforce", "run_tests_in_place")})
# COPY: why a saved result verified by tests that ran with the owner's permissions is not reused.
UNCONFINED = ("the tests that verified it did not run under operating-system isolation (or ran before G6 recorded it), "
              "so it is checked again")


def _unconfined_bench(call: dict | None) -> bool:
    return bool(call) and (call.get("component"), call.get("op")) in _BENCH_OPS and call.get("confined") is not True


def _current(contract: Callable[[str, str], ParamContract | None], call: dict | None) -> ParamContract | None:
    """The contract a recorded call was checked against, if it is still the current one (the same digest)."""
    if not call or not call.get("contract_sha"):
        return None
    found = contract(str(call.get("component", "")), str(call.get("op", "")))
    return found if found is not None and found.sha256 == call["contract_sha"] else None


def _read_by_checks(plan: Callable[[str], Plan] | None, mission_id: str, unit: str, verdicts: list[dict]) -> list[str]:
    """What the checks behind ``verdicts`` read besides the output they judged: each one's plan inputs that hold an
    output (a do unit of its mission, or a saved result). A check can read a premise its unit never read."""
    if plan is None:
        return []
    try:
        found = plan(mission_id)
        specs = [found.unit(str(v.get("unit_key", ""))) for v in verdicts]
    except (LookupError, ValueError):  # no such plan or unit, or a plan that no longer matches its digest
        raise ReuseRefused("the check that verified it has changed since") from None
    kinds = {u.key: u.kind for u in found.units}
    return [key for spec in specs for key in spec.inputs
            if key != unit and (key.startswith(ARTIFACT_PREFIX) or kinds.get(key) == "do")]


def _deeper(depth: int) -> None:
    """The walk's bound: a step to the results a result at ``depth`` was made from, refused past ``MAX_DEPTH``."""
    if depth + 1 >= MAX_DEPTH:
        raise ReuseRefused(f"it is built on more than {MAX_DEPTH} earlier saved results, too deep to check")


def _recorded(artifact: dict) -> dict:
    """How a result records it was made (its call and the input digests), or ``ReuseRefused`` when it records nothing."""
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        raise ReuseRefused("it was saved before G6 recorded how results are made, so how it was made cannot be checked")
    return provenance


def _premise(ledger: Any, key: str, artifact_id: str, *, contract: Callable[[str, str], ParamContract | None],
             now: datetime, risk_tier: str, depth: int, plan: Callable[[str], Plan] | None) -> None:
    """A result the reused one rests on (``key``: its unit, or ``artifact:<id>``) passes these same rules, one level
    deeper."""
    _deeper(depth)
    try:
        check(ledger, artifact_id, contract=contract, now=now, risk_tier=risk_tier, depth=depth + 1, plan=plan)
    except ReuseRefused as inner:
        raise ReuseRefused(f"the saved result it was made from ({key}) cannot be reused: {inner}") from None


def check(ledger: Any, artifact_id: str, *, contract: Callable[[str, str], ParamContract | None], now: datetime,
          risk_tier: str, depth: int = 0, plan: Callable[[str], Plan] | None = None) -> Reused:
    """The saved result ``artifact_id`` if a mission of ``risk_tier`` may reuse it now, else ``ReuseRefused``.
    ``ledger`` is the reader's ``RunLedger`` (its owner's results only). ``plan(mission_id)``: that mission's stored
    plan, so what each check that verified it read is judged too (the runtime always passes it; without it only what
    the result was made from is)."""
    try:
        mission_id, artifact = ledger.find_artifact(artifact_id)
    except LookupError:
        raise ReuseRefused("there is no such result for this account") from None
    unit = str(artifact.get("unit", ""))
    latest = ledger.last_artifact(mission_id, unit)
    if latest is None or latest["artifact_id"] != artifact_id:
        raise ReuseRefused("a newer result has replaced it")
    made_for = ledger.mission(mission_id)["risk_tier"]
    if RISK_TIERS.index(made_for) < RISK_TIERS.index(risk_tier):
        raise ReuseRefused(f"it was checked for lower-stakes work ({made_for}) than this mission ({risk_tier})")
    judged = [v for v in ledger.verdicts(mission_id, unit) if v["artifact_id"] == artifact_id]
    if any(v["result"] == "fail" and v["executed"] for v in judged):
        raise ReuseRefused("an independent check failed it")
    passed = [v for v in judged if v["state"] == VERIFIED and v["artifact_sha"] == artifact.get("sha256")]
    if not passed:
        raise ReuseRefused("no independent check verified it")
    certified = [v for v in passed if v.get("authority") == VERIFIER_GRADE]
    if not certified:
        raise ReuseRefused(AUTHORITY_COPY["reuse_uncertified"])
    passed = certified
    provenance = _recorded(artifact)
    lifetimes: list[int | None] = []
    made_by = provenance.get("call")
    if made_by:
        tool = _current(contract, made_by)
        if tool is None:
            raise ReuseRefused(f"the tool that made it ({made_by.get('component')}.{made_by.get('op')}) has changed "
                               "since")
        lifetimes.append(tool.valid_for_seconds)
    answered = [(v, _answered_call(ledger, mission_id, v["run_id"])) for v in passed]
    # a bench pass whose call record does not say its tests ran confined (one recorded before G6 said so included)
    unconfined = [v for v, call in answered if _unconfined_bench(call)]
    still = [(v, found) for v, call in answered
             if v not in unconfined and (found := _current(contract, call)) is not None]
    if not still:
        raise ReuseRefused(UNCONFINED if unconfined else "the check that verified it has changed since")
    lifetimes += [found.valid_for_seconds for _, found in still]
    deeper = {"contract": contract, "now": now, "risk_tier": risk_tier, "depth": depth, "plan": plan}
    made_from = provenance.get("inputs") or {}
    for key, recorded in made_from.items():
        if key.startswith(ARTIFACT_PREFIX):
            _premise(ledger, key, str(recorded.get("artifact_id", "")), **deeper)
            continue
        there = ledger.last_artifact(mission_id, key)
        if there is None or (there["artifact_id"], there.get("sha256")) != (recorded.get("artifact_id"),
                                                                            recorded.get("sha256")):
            raise ReuseRefused(f"the result it was made from ({key}) has changed since")
        _premise(ledger, key, there["artifact_id"], **deeper)  # Gate W review gw2: itself verified at A4
    for key in dict.fromkeys(_read_by_checks(plan, mission_id, unit, certified)):
        if key in made_from:
            continue
        if key.startswith(ARTIFACT_PREFIX):
            _premise(ledger, key, key.removeprefix(ARTIFACT_PREFIX), **deeper)
            continue
        there = ledger.last_artifact(mission_id, key)
        if there is None:
            raise ReuseRefused(f"the result it was made from ({key}) has changed since")
        _premise(ledger, key, there["artifact_id"], **deeper)
    seconds = min((s for s in lifetimes if s), default=REUSE_TTL_SECONDS)
    until = datetime.fromisoformat(artifact["at"]) + timedelta(seconds=seconds)
    if now > until:
        raise ReuseRefused(f"it is older than its validity period of {_period(seconds)}")
    return Reused(artifact, mission_id, tuple(v["verdict_id"] for v, _ in still), until.isoformat(timespec="seconds"))


#: The links a result's recorded provenance holds (review M4: any other key, like any malformed shape, cannot be read).
_PROVENANCE_KEYS = frozenset({"call", "inputs"})


def lineage(ledger: Any, artifact: dict, *, depth: int = 0, seen: frozenset[str] = frozenset(),
            digests: set[str] | None = None) -> frozenset[tuple[str, str]]:
    """Every op that made ``artifact`` or a result it was made from (a unit's output or a saved result), read from the
    provenance each recorded, by ``check``'s walk: its reads and its ``MAX_DEPTH`` bound (Security Brief 3, SR3-1: a
    check is independent only of a result whose whole ancestry it did not make). ``ReuseRefused`` when the ancestry
    cannot be read in full, so nothing is certified on a part (fail closed): a result that records no provenance or
    records it in a shape G6 does not read, an input that cannot be found or whose digest is not the one recorded, a
    chain that loops back on itself, or one deeper than ``MAX_DEPTH``. With no ``ledger`` only a result that records no
    inputs can be read. ``digests`` collects the content digest of every result visited (review I5)."""
    provenance = _recorded(artifact)
    made, inputs = provenance.get("call"), provenance.get("inputs")
    if (set(provenance) - _PROVENANCE_KEYS or not isinstance(made, dict | None)
            or not isinstance(inputs, dict | None)):
        raise ReuseRefused("its record of how it was made is in a shape G6 cannot read")
    if digests is not None and artifact.get("sha256"):
        digests.add(str(artifact["sha256"]))
    found = {(str(made.get("component", "")), str(made.get("op", "")))} if made else set()
    here = seen | {str(artifact.get("artifact_id", ""))}
    for key, recorded in (inputs or {}).items():
        _deeper(depth)
        recorded = recorded if isinstance(recorded, dict) else {}
        artifact_id = str(recorded.get("artifact_id", ""))
        if artifact_id and artifact_id in here:
            raise ReuseRefused(f"the result it was made from ({key}) is itself made from it")
        if ledger is None:
            raise ReuseRefused(f"the result it was made from ({key}) cannot be read here")
        try:
            _, parent = ledger.find_artifact(artifact_id)
        except LookupError:
            raise ReuseRefused(f"the result it was made from ({key}) cannot be found") from None
        if parent.get("sha256") != recorded.get("sha256"):
            raise ReuseRefused(f"the result it was made from ({key}) has changed since")
        found |= lineage(ledger, parent, depth=depth + 1, seen=here, digests=digests)
    return frozenset(found)


__all__ = ["MAX_DEPTH", "REUSE_TTL_SECONDS", "ReuseRefused", "Reused", "check", "lineage"]
