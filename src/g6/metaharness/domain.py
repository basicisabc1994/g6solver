# SPDX-License-Identifier: Apache-2.0
"""What the self-improvement loop needs from a kind of work, and nothing else: the domain seam.

A ``Domain`` gives the loop a name; its development cases; a sealed split it may only hold a handle to; how a profile
runs on a case; an independent judge; and failure records from a run. The same tick runs on any domain: nothing in the
loop knows what the work is.

**The judge is what makes it improvement.** A domain with no independent judge is refused, because self-grading is not
improvement. Independent here means four checkable things: its answers were not written by the caller or a model; it
is not a model's opinion; it has a source file a digest can pin; and it rejects a non-answer. It does not mean the
judge is right: the loop assumes that, and ``ShadowLoop`` reports it as assumed.

**Three separations.** The development cases are cut by a seeded hash of each case's id (``cut_pool``, design 6.5): the
cases proposers tune on, the cases candidates race on, the held-out cases the gate scores, and the cases the monitor
audits. A case keeps its side however the pool grows. The sealed split is a different pool (design 6.3), and the loop
holds only its vault and its cases' hashes. ``sealed_closed`` then keeps it shut while the loop develops: a Python-level
file open of anything the vault covers, from any thread of this process, is refused and counted as a breach. That is
a check inside one process, as ``g6.core.vault`` says of itself: a child process or native code is not stopped by it,
and the owner's ruling O2 means every study states that limit.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from g6.core.ledger import digest
from g6.core.vault import AccessDenied, Vault
from g6.harness import Check
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.pool import EditSpace
from g6.metaharness.propose import ProposerView
from g6.metaharness.recovery import INTAKE_BLOCKED, ORACLE_SILENT, partition
from g6.metaharness.schema import Model, PipelineStep, SuiteCase

NO_JUDGE = "G6_E_LOOP_NO_INDEPENDENT_JUDGE"
NO_SEALED = "G6_E_LOOP_NO_SEALED_SPLIT"
SEALED_OVERLAP = "G6_E_LOOP_SEALED_OVERLAP"
PAID_MODEL = "G6_E_LOOP_PAID_MODEL"
INDEPENDENT = "independent"
#: Causes no profile can change: only a person can (recovery says who acts on each).
PERSON_ONLY = (INTAKE_BLOCKED, ORACLE_SILENT)
#: Shares of the development pool, in the order the seeded hash walks them.
SHARES = (("tune", 0.25), ("selection", 0.15), ("held_out", 0.35), ("monitor", 0.25))
_NON_ANSWERS = ("", "this is not an answer")   # no digit or label in it: a judge may fairly read those as answers


@dataclass(frozen=True)
class Judge:
    """What says an answer is right. ``verdict(output, expected)`` is the only code that reads an answer."""

    verdict: Callable[[str, Any], bool]
    answers_by: str                         # who wrote the expected answers: only ``independent`` is accepted
    is_model: bool = False                  # a model's opinion is never accepted as a judge


@dataclass(frozen=True)
class SealedSplit:
    """All the loop may hold of a sealed split: the vault over its files, and each case's id and hashes, no text."""

    vault: Vault
    refs: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class Execution:
    """How a profile runs on a case. The harness runs it, so every domain gets the same checks, budgets and envelope.
    ``factory`` is left out for work that is one model call per case. ``space`` is what proposers may choose from, and
    ``initial`` the profile the loop starts from (the first declared model when left out)."""

    task: str
    checks: tuple[Check, ...]
    models: Mapping[str, Model]
    factory: ExecutorFactory | None = None
    optional_checks: Mapping[str, Check] = field(default_factory=dict)
    pipeline_steps: Mapping[str, PipelineStep] = field(default_factory=dict)
    space: EditSpace = field(default_factory=lambda: EditSpace({}))
    initial: Mapping[str, Any] | None = None
    feedback: Callable[[str, SuiteCase], str] | None = None   # why a dev answer failed, in words a proposer can use


@dataclass(frozen=True)
class FailureRecord:
    """One failed development case and its one cause: a stable reason code, never free text. A record says it was
    independently observed only by declaring so, and the loop names no constraint on any other kind."""

    case_id: str
    cause: str
    changeable: bool = True                 # a profile can change this; false when only a person can
    note: str = ""
    independently_observed: bool = False


@runtime_checkable
class Domain(Protocol):
    name: str

    def development(self) -> tuple[SuiteCase, ...]: ...

    def sealed(self) -> SealedSplit | None: ...

    def execution(self) -> Execution: ...

    def judge(self) -> Judge | None: ...

    def failures(self, view: ProposerView) -> tuple[FailureRecord, ...]: ...


def reason_coded(view: ProposerView) -> tuple[FailureRecord, ...]:
    """One record per failed case, from the harness's reason codes and the judge's verdict: what a domain returns when
    it has no causes of its own."""
    records: dict[str, FailureRecord] = {}
    for cause, failures in partition(view.failures).items():
        for f in failures:
            records.setdefault(f.case_id, FailureRecord(f.case_id, cause, cause not in PERSON_ONLY, f.feedback, True))
    return tuple(records.values())


@dataclass(frozen=True)
class Cut:
    tune: tuple[SuiteCase, ...]
    selection: tuple[SuiteCase, ...]
    held_out: tuple[SuiteCase, ...]
    monitor: tuple[SuiteCase, ...]

    def digest(self) -> str:
        return digest({name: [c.ref() for c in getattr(self, name)] for name, _ in SHARES})


def cut_pool(cases: Sequence[SuiteCase], seed: int) -> Cut:
    """Each case goes to a side by a seeded hash of its id, so it keeps that side whatever else joins the pool."""
    parts: dict[str, list[SuiteCase]] = {name: [] for name, _ in SHARES}
    for case in cases:
        point = int.from_bytes(hashlib.sha256(f"{seed}:{case.case_id}".encode()).digest()[:8], "big") / 2 ** 64
        for name, share in SHARES:
            if point < share:
                parts[name].append(case)
                break
            point -= share
        else:
            parts[SHARES[-1][0]].append(case)
    return Cut(**{name: tuple(found) for name, found in parts.items()})


def judge_source(verdict: Callable[..., Any]) -> Path | None:
    """The file a judge's code lives in, or ``None`` when there is none a digest could pin."""
    try:
        found = inspect.getsourcefile(inspect.unwrap(verdict))
    except TypeError:
        return None
    return Path(found) if found and Path(found).is_file() else None


def _judge_problems(judge: Judge | None, cases: Sequence[SuiteCase]) -> list[str]:
    if judge is None:
        return ["this domain has no independent judge, and self-grading is not improvement"]
    found = []
    if judge.answers_by != INDEPENDENT:
        found.append(f"its answers were written by {judge.answers_by!r}, not independently: self-grading is not "
                     "improvement")
    if judge.is_model:
        found.append("its judge is a model's opinion, which never counts as evidence")
    if judge_source(judge.verdict) is None:
        found.append("its judge has no source file, so nothing can pin what judges")
    for case in cases[:8]:
        for answer in _NON_ANSWERS:
            try:
                accepted = judge.verdict(answer, case.expected) is True
            except Exception:  # noqa: BLE001 - a judge that raises has not accepted the answer
                accepted = False
            if accepted:
                return [*found, f"its judge accepts a non-answer ({answer!r}) on {case.case_id!r}, so it judges nothing"]
    return found


def domain_problems(domain: Domain) -> list[tuple[str, str]]:
    """Why the loop will not run on this domain, each with its stable code; empty when it will."""
    cases, sealed = domain.development(), domain.sealed()
    found = [(NO_JUDGE, why) for why in _judge_problems(domain.judge(), cases)]
    if sealed is None:
        found.append((NO_SEALED, "this domain has no sealed split, so nothing could ever test the loop's results on "
                                 "work it has not seen"))
    else:
        seen = {key for c in cases for key in (c.case_id, c.ref()["input_sha256"])}
        shared = sorted(r["case_id"] for r in sealed.refs if r["case_id"] in seen or r["input_sha256"] in seen)
        if shared:
            found.append((SEALED_OVERLAP, f"sealed cases are also development cases: {shared[:5]}"))
    paid = sorted(name for name, model in domain.execution().models.items() if model.paid)
    if paid:
        found.append((PAID_MODEL, f"the loop never spends API credits, and these declared models are paid: {paid}"))
    return found


# The sealed split stays shut while the loop develops --------------------------------------------------------------
_GUARDED: list[tuple[Vault, list[str]]] = []
_LOCK = threading.Lock()
_HOOKED = False


def _on_audit(event: str, args: tuple[Any, ...]) -> None:
    if event != "open" or not _GUARDED or not args or not isinstance(args[0], str | bytes | os.PathLike):
        return
    for vault, breaches in tuple(_GUARDED):
        if vault.covers(args[0]):
            breaches.append(hashlib.sha256(os.fsencode(args[0])).hexdigest())
            raise AccessDenied("the sealed split stays shut while the loop develops")


@contextmanager
def sealed_closed(vault: Vault) -> Iterator[list[str]]:
    """Inside, a Python-level open of anything ``vault`` covers is refused and counted. It yields the list of breaches,
    each a hash of the path: a strategy that swallows the refusal is still on the record. An audit hook lasts the
    process's lifetime, so it does nothing outside this block."""
    global _HOOKED
    with _LOCK:
        if not _HOOKED:
            sys.addaudithook(_on_audit)
            _HOOKED = True
    breaches: list[str] = []
    entry = (vault, breaches)
    _GUARDED.append(entry)
    try:
        yield breaches
    finally:
        _GUARDED.remove(entry)


__all__ = ["Cut", "Domain", "Execution", "FailureRecord", "Judge", "NO_JUDGE", "NO_SEALED", "PAID_MODEL",
           "PERSON_ONLY", "SEALED_OVERLAP", "SHARES", "SealedSplit", "cut_pool", "domain_problems", "judge_source",
           "reason_coded", "sealed_closed"]
