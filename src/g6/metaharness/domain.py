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
audits. A case keeps its side however the pool grows, unless the hash would leave a side under its minimum: a pool that
small is cut by counts along the same seeded order. The sealed split is a different pool (design 6.3), and the loop holds
only its vault and its cases' hashes.

**What the sealed guard is, and is not.** ``sealed_closed`` is a check inside one process, as ``g6.core.vault`` says of
itself. While the loop develops it refuses a Python-level open or memory map of anything the vault covers, by path or by
descriptor, from any thread, and counts each attempt as a breach. It also looks through this process's open descriptors
on the way in and on the way out, and counts a sealed file it finds held open. That is all it does.

A read through a handle opened before the tick cannot be stopped. A read emits no audit event, so the guard can only
notice that the handle exists, and only if it is still open at one of those two moments. It knows nothing of what was
read before the tick, of a mapping or a buffer that outlives its file, or of a child process or native code. So a breach
means the split may have been read, and no breach does not mean it was not. The owner's ruling O2 means every study
states that limit.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import os
import pickle
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import accumulate, product
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from g6.core.ledger import digest
from g6.core.llm import AnthropicCaller, EmptyRetry, FakeLLM, FallbackCaller, OllamaCaller, OpenAICompatibleCaller
from g6.core.vault import AccessDenied, Vault
from g6.harness import Check
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.monitor import MIN_PAIRS
from g6.metaharness.pool import EditSpace
from g6.metaharness.propose import ProposerView
from g6.metaharness.recovery import INTAKE_BLOCKED, ORACLE_SILENT, partition
from g6.metaharness.schema import EvidencePolicy, Model, PipelineStep, SuiteCase

NO_JUDGE = "G6_E_LOOP_NO_INDEPENDENT_JUDGE"
NO_SEALED = "G6_E_LOOP_NO_SEALED_SPLIT"
SEALED_OVERLAP = "G6_E_LOOP_SEALED_OVERLAP"
PAID_MODEL = "G6_E_LOOP_PAID_MODEL"
UNKNOWN_CALLER = "G6_E_LOOP_UNKNOWN_CALLER"
PROVIDER_MISMATCH = "G6_E_LOOP_PROVIDER_MISMATCH"
RETRY_MODEL = "G6_E_LOOP_RETRY_MODEL"
FALLBACK_MODEL = "G6_E_LOOP_FALLBACK_MODEL"
#: What a caller may not send under, wherever it is found: at enrolment, or at a request.
BARRED = (PAID_MODEL, UNKNOWN_CALLER, PROVIDER_MISMATCH)
INDEPENDENT = "independent"
#: Who pays for a caller's requests, where it is no provider's subscription.
PAID, LOCAL = "paid", "local"
#: Causes no profile can change: only a person can (recovery says who acts on each).
PERSON_ONLY = (INTAKE_BLOCKED, ORACLE_SILENT)
#: Shares of the development pool, in the order the seeded hash walks them.
SHARES = (("tune", 0.25), ("selection", 0.15), ("held_out", 0.35), ("monitor", 0.25))
_ENDS = tuple(zip((name for name, _ in SHARES), accumulate(share for _, share in SHARES)))
#: The fewest cases a side can work with, which the loop refuses a pool for.
FEWEST = {"tune": 1, "selection": 1, "held_out": EvidencePolicy().min_cases, "monitor": MIN_PAIRS}
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

    # A domain may also give ``root_cause(cause)``: a member of ``g6.metaharness.lessons.ROOT_CAUSES``, or ``None``. It is no member of
    # this protocol, which would make it required: a domain without it leaves no lesson, and nothing else changes.


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
        """The cut's identity: each case's reference, and its answer as the judge compares it (``answer_identity``), not a
        JSON rendering that two answers the judge tells apart can share."""
        return digest({name: [{**c.ref(), "answer": answer_identity(c.expected)} for c in getattr(self, name)]
                       for name, _ in SHARES})


def answer_identity(value: Any) -> str:
    """The SHA-256 of an expected answer as the judge compares it: every value with its type, so answers a judge can tell
    apart, as ``1`` and ``"1"`` or ``True`` and ``1``, never share an identity. A mapping's items and a set's members are put
    in a fixed order first, so the same answer read in another process has the same identity."""
    return hashlib.sha256(_answer_bytes(value)).hexdigest()


def _answer_bytes(value: Any) -> bytes:
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    if value is None or isinstance(value, (bool, int, float, complex, str, bytes)):
        body = repr(value).encode("utf-8", "backslashreplace")
    elif isinstance(value, Mapping):
        body = b",".join(sorted(_answer_bytes(key) + b":" + _answer_bytes(item) for key, item in value.items()))
    elif isinstance(value, (set, frozenset)):
        body = b",".join(sorted(_answer_bytes(item) for item in value))
    elif isinstance(value, (list, tuple)):
        body = b",".join(_answer_bytes(item) for item in value)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        body = _answer_bytes({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})
    else:
        try:
            body = pickle.dumps(value, protocol=4)
        except Exception:  # noqa: BLE001 - what will not pickle is pinned by what it says of itself
            body = repr(value).encode("utf-8", "backslashreplace")
    return f"{kind}({len(body)})".encode() + body


def _point(seed: int, key: str) -> float:
    return int.from_bytes(hashlib.sha256(f"{seed}:{key}".encode()).digest()[:8], "big") / 2 ** 64


def _by_hash(points: Mapping[str, float]) -> dict[str, str]:
    return {key: next((name for name, end in _ENDS if point < end), SHARES[-1][0]) for key, point in points.items()}


def _by_counts(order: Sequence[str], sizes: Mapping[str, int]) -> dict[str, str]:
    """The same order, divided by counts. A group goes where its first case falls, unless any of it falls in held-out's
    share: that side has the minimum no person can lower, so a group on either of its edges never leaves it short."""
    total = sum(sizes.values())
    ends = [(name, round(end * total)) for name, end in _ENDS]
    (_, held_from), (_, held_to) = ends[1], ends[2]
    sides: dict[str, str] = {}
    walked = 0
    for key in order:
        first, walked = walked, walked + sizes[key]
        sides[key] = "held_out" if first < held_to and walked > held_from else next(name for name, end in ends if first < end)
    return sides


def _covering(order: Sequence[str], sizes: Mapping[str, int], sides: Mapping[str, str]) -> dict[str, str]:
    """``sides`` moved no further than ``FEWEST`` needs. Each group in turn keeps its side if the groups after it can still
    meet what is left, and otherwise takes the first side that lets them. When intact groups cannot meet every minimum,
    ``sides`` stands, and the loop refuses the pool. What is left is a small table, so this is exact, not a search."""
    names, least = tuple(FEWEST), tuple(FEWEST.values())

    def less(need: tuple[int, ...], side: str, size: int) -> tuple[int, ...]:
        return tuple(max(0, left - size) if name == side else left for name, left in zip(names, need))

    every = tuple(product(*(range(most + 1) for most in least)))
    met = [{(0,) * len(names)}]                       # what the groups from each one on can meet, built from the last
    for key in reversed(order):
        met.append({need for need in every if any(less(need, name, sizes[key]) in met[-1] for name in names)})
    met.reverse()
    if least not in met[0]:
        return dict(sides)
    covered: dict[str, str] = {}
    need = least
    for index, key in enumerate(order):
        covered[key] = next(name for name in (sides[key], *names) if less(need, name, sizes[key]) in met[index + 1])
        need = less(need, covered[key], sizes[key])
    return covered


def cut_pool(cases: Sequence[SuiteCase], seed: int) -> Cut:
    """Each case goes to a side by a seeded hash of its id, so it keeps that side whatever else joins the pool. Re-enrolling
    with a fresh split stands on that: what proposers saw stays out of held-out. Cases that are not independent name a
    ``meta["group"]``, which is hashed in place of the id, so a group stays on one side.

    A hash is only near the shares, so a small pool can leave a side under its minimum (``FEWEST``). Such a pool is cut by
    counts instead, along the same seeded order, and a pool of 86 gives held-out 30, with groups moved only as far as every
    minimum needs. Its cases can then change sides as the pool changes, and enrolling refuses a held-out case that proposers
    saw."""
    keys = [case.meta.get("group") or case.case_id for case in cases]
    sizes = Counter(keys)
    points = {key: _point(seed, key) for key in sizes}
    sides = _by_hash(points)
    counted = Counter(sides[key] for key in keys)
    if any(counted[name] < least for name, least in FEWEST.items()):
        order = sorted(points, key=points.__getitem__)
        sides = _covering(order, sizes, _by_counts(order, sizes))
    parts: dict[str, list[SuiteCase]] = {name: [] for name, _ in SHARES}
    for key, case in zip(keys, cases):
        parts[sides[key]].append(case)
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


def _behind(caller: Any) -> Iterator[Any]:
    """The callers that answer through ``caller``, seen through the wrappers ``g6.core.llm`` has and any that hold a caller as
    they do. A caller of a known type sends for itself, so nothing it holds is asked about. This reads what a domain declares,
    so a wrapper can declare one caller and send through another: what holds there is the check at each request
    (``ShadowLoop._barred``), which reads the caller that really sends."""
    if who_pays(caller):
        yield caller
    elif hasattr(caller, "_callers"):
        for inner in caller._callers:
            yield from _behind(inner)
    elif hasattr(caller, "_inner"):
        yield from _behind(caller._inner)
    else:
        yield caller


def backends(caller: Any) -> int:
    """How many models can answer through ``caller``. A wrapper of a domain's own that holds a fallback under another name
    is not seen."""
    return sum(1 for _ in _behind(caller))


def who_pays(caller: Any) -> str:
    """Who pays for what ``caller`` sends, read from its type and not from what anyone declares: the provider a host program
    signs in to, whose subscription it is; ``PAID`` for one that carries an API key; ``LOCAL`` for one that bills nobody; and
    ``""`` for a type nothing here knows. The Anthropic SDK finds a key of its own, so it is paid with none given. An HTTP
    caller with no key has nothing to bill. A type can be several of these, and one that may carry a key is paid whatever else
    it inherits, so that is read first; a host's constructor gives it no ``api_key`` at all. This reads the type: it cannot see
    what a caller of a known type does inside."""
    from g6.core.host_models import ClaudeCodeCaller, CodexCaller   # only when asked: it brings in the process-running code

    if isinstance(caller, AnthropicCaller) or isinstance(caller, OpenAICompatibleCaller) and getattr(caller, "api_key", None):
        return PAID
    if isinstance(caller, ClaudeCodeCaller):
        return "claude"
    if isinstance(caller, CodexCaller):
        return "codex"
    return LOCAL if isinstance(caller, FakeLLM | OllamaCaller | OpenAICompatibleCaller) else ""


def _retries(caller: Any) -> bool:
    """Whether a wrapper ``g6.core.llm`` has would send a request again by itself. One of a domain's own is not seen."""
    if isinstance(caller, EmptyRetry | FallbackCaller):
        return True
    return _retries(caller._inner) if hasattr(caller, "_inner") and not who_pays(caller) else False


def caller_problems(callers: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Why the loop sends nothing through these named callers, each with its stable code."""
    payers = {name: {who_pays(found) for found in _behind(caller)} for name, caller in callers.items()}
    paid, unknown = sorted(n for n, who in payers.items() if PAID in who), sorted(n for n, who in payers.items() if "" in who)
    retried = sorted(name for name, caller in callers.items() if _retries(caller))
    return [*([(PAID_MODEL, f"the loop never spends API credits, and these call with an API key: {paid}")] if paid else []),
            *([(UNKNOWN_CALLER, f"nothing can say who pays for what these send: {unknown}. Use a host or local caller, or a "
                                "wrapper that holds one as _inner")] if unknown else []),
            *([(RETRY_MODEL, f"these send a request again by themselves: {retried}, and a classifier's refusal is never sent "
                             "again. Use the caller bare, such as ClaudeCodeCaller(), and not through provider_from_env, "
                             "which wraps it")] if retried else [])]


def provider_problems(callers: Mapping[str, Any], provider: str) -> list[tuple[str, str]]:
    """Why these callers are not on ``provider``, the one week a folder's use is charged to: a host caller signs in to its own
    subscription, and an allowance pays only for that. A local caller is on nobody's week, so it fits any."""
    others = sorted({who_pays(found) for caller in callers.values() for found in _behind(caller)} - {provider, PAID, LOCAL, ""})
    return ([(PROVIDER_MISMATCH, f"this folder's calls are stated to go to {provider} and its callers sign in to "
                                 f"{', '.join(others)}, so its allowance pays for nothing they send")] if others else [])


def sender_problems(sender: Any, provider: str) -> list[tuple[str, str]]:
    """Why ``sender``, the caller really sending a request, may not send it. It is judged as itself, with nothing unwrapped,
    since nothing stands between it and the request, and whatever it holds is not what is going out."""
    who, named = who_pays(sender), type(sender).__name__
    if who == PAID:
        return [(PAID_MODEL, f"the loop never spends API credits, and {named} sends with an API key")]
    if not who:
        return [(UNKNOWN_CALLER, f"nothing can say who pays for what {named} sends")]
    return [] if who in (provider, LOCAL) else [(PROVIDER_MISMATCH, f"this folder's calls are stated to go to {provider} and "
                                                f"{named} signs in to {who}, so its allowance pays for nothing it sends")]


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
    models = domain.execution().models
    paid = sorted(name for name, model in models.items() if model.paid)
    if paid:
        found.append((PAID_MODEL, f"the loop never spends API credits, and these declared models are paid: {paid}"))
    mixed = sorted(name for name, model in models.items() if backends(model.caller) > 1)
    if mixed:
        found.append((FALLBACK_MODEL, f"these declared models can answer from more than one backend: {mixed}. A paired "
                                      "comparison would compare backends, and a limit on one would pass unseen"))
    return found + caller_problems({name: model.caller for name, model in models.items()})


# The sealed split stays shut while the loop develops --------------------------------------------------------------
_GUARDED: list[tuple[Vault, list[str]]] = []
_LOCK = threading.Lock()
_HOOKED = False
_SEALED_EVENTS = ("open", "mmap.__new__")
_MOST_DESCRIPTORS = 8192          # the C runtime's own limit on Windows, where the system lists no descriptors


def _sealed_descriptor(vault: Vault, descriptor: int) -> bool:
    """Whether this descriptor is open on a file the vault sealed, by the file's identity."""
    try:
        info = os.fstat(descriptor)
    except OSError:
        return False
    return bool(info.st_ino) and (info.st_dev, info.st_ino) in vault.identities


def _on_audit(event: str, args: tuple[Any, ...]) -> None:
    if event not in _SEALED_EVENTS or not _GUARDED or not args:
        return
    target = args[0]
    for vault, breaches in tuple(_GUARDED):
        if isinstance(target, int):
            found = _sealed_descriptor(vault, target)
        else:
            found = isinstance(target, str | bytes | os.PathLike) and vault.covers(target)
        if found:
            breaches.append(f"{event} {hashlib.sha256(repr(target).encode()).hexdigest()}")
            raise AccessDenied("the sealed split stays shut while the loop develops")


def _descriptors() -> Sequence[int]:
    """This process's open descriptors where the system lists them, or every number one could have."""
    try:
        return [int(name) for name in os.listdir("/proc/self/fd")]
    except OSError:
        return range(_MOST_DESCRIPTORS)


def _held_open(vault: Vault) -> list[str]:
    return [f"descriptor {d} held open" for d in _descriptors() if _sealed_descriptor(vault, d)]


@contextmanager
def sealed_closed(vault: Vault) -> Iterator[list[str]]:
    """Inside, a Python-level open or map of anything ``vault`` covers is refused and counted. A sealed file already
    held open is counted on the way in, so the caller can stop before it starts, and again on the way out. It yields
    the breaches, so a strategy that swallows a refusal is still on the record. This cannot stop a read through a handle
    that is already open: the module docstring says what that leaves. An audit hook lasts the process's lifetime, so it
    does nothing outside this block."""
    global _HOOKED
    with _LOCK:
        if not _HOOKED:
            sys.addaudithook(_on_audit)
            _HOOKED = True
    breaches = _held_open(vault)
    entry = (vault, breaches)
    _GUARDED.append(entry)
    try:
        yield breaches
    finally:
        _GUARDED.remove(entry)
        breaches.extend(_held_open(vault))


__all__ = ["BARRED", "Cut", "Domain", "Execution", "FALLBACK_MODEL", "FEWEST", "FailureRecord", "Judge", "LOCAL", "NO_JUDGE",
           "NO_SEALED",
           "PAID", "PAID_MODEL", "PERSON_ONLY", "PROVIDER_MISMATCH", "RETRY_MODEL", "SEALED_OVERLAP", "SHARES", "SealedSplit",
           "UNKNOWN_CALLER",
           "backends", "caller_problems", "cut_pool", "domain_problems", "judge_source", "provider_problems", "reason_coded",
           "sealed_closed", "sender_problems", "who_pays"]
