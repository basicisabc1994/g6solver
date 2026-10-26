# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The workforce's completion contract (workforce OS plan, P0 T1).

An ``Outcome`` is what one layer hands the next: one of the product's three completion states,
whether anything degraded, the reasons, and the evidence ids. It only gets worse on the way up:
``combine`` keeps the worst state, any degradation and every reason, and ``demote`` never
promotes.

This module is the ONLY workforce code that reads a reply's state fields (enforced by
tests/mvp/venture/test_workforce_boundaries.py). ``Outcome.from_envelope`` reads beneath the outer
envelope, which stamps ``qualified-draft`` and ``degraded: false`` whatever the component said
(base_helpers/__init__.py:677-685): the product's own down-only reading
(``outcome_of`` below, from the archive's ``middleware.usage``), the component's ``degraded`` at every level, the job packs'
unread-parameter metadata, and ``chain.verdict``'s "the component did not answer" patterns. A
component can never certify itself ``verified``: only a ``Verdict`` that ran, was independent of
the producer and asked no model promotes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from g6.workforce.chain import StepResult
from g6.workforce.chain import verdict as chain_verdict

VERIFIED, QUALIFIED, BLOCKED = "verified", "qualified-draft", "blocked-escalated"
STATES = (VERIFIED, QUALIFIED, BLOCKED)
_RANK = {BLOCKED: 0, QUALIFIED: 1, VERIFIED: 2}
_ALIASES = {"qualified_draft": QUALIFIED, "blocked_escalated": BLOCKED}
#: How far the reader follows nested ``result`` values (envelope > pack output > the pack's own JSON
#: result > ...). A reply that nests deeper is BLOCKED: what the reader cannot see it cannot trust.
MAX_DEPTH = 5
#: Holders beside a result that declare a state of their own (the job envelope and the job verdict).
_HOLDERS = ("reliability_envelope", "trust", "trust_panel")
#: A component status that means it did not answer (chain.verdict's ambiguous set, plus failed).
_UNANSWERED_STATUS = frozenset({"unknown", "timeout", "error", "failed"})
#: A nested JSON string larger than this is not parsed (the same bound as middleware.usage).
_MAX_PARSED_CHARS = 256 * 1024
_REASON_CHARS = 200

VERIFIER_KINDS = ("oracle", "deterministic", "formal", "test", "human", "model_critic")
VERDICT_RESULTS = ("pass", "fail", "unmeasured")
#: Gate W's verification authority ladder (production-path spec, amendment 2026-10-05): what an op may be trusted for.
#: A0 registered, A1 executable (W-A), A2 checked (a known-answer test), A3 validated (a property or differential test
#: and an edge-case test), A4 verifier-grade (A3, model-free, read-only). The census computes an op's class
#: (scripts/capability_map/census.py CLASSES, the same tuple); anything else reads as A0.
AUTHORITY = ("A0", "A1", "A2", "A3", "A4")
VERIFIER_GRADE = "A4"
#: Approved by the founder on 2026-10-06 (task #161, P-3 to P-6).
AUTHORITY_COPY = {
    "plan_a0": ("G6 cannot include {op} in step {unit}: it has not been cleared for automatic use."),
    "plan_effect": ("G6 cannot include {op} in step {unit}: it may make external or irreversible changes, "
                    "but has not passed the required tests with known answers."),
    "verdict_capped": ("Use with caution: this check has not met G6’s verification requirements."),
    "verdict_self_check": ("Use with caution: the same operation produced and checked this result. "
                           "A separate check is needed."),
    "rollup_capped": ("Use with caution: step {unit} remains unverified. {reason}"),
    "reuse_uncertified": ("G6 cannot reuse this saved result: its recorded check does not meet the verification "
                          "requirements."),
}
# COPY: (Security Brief 3, SR3-1) why a check cannot verify a result built from something its own operation made.
ANCESTOR_CHECK = ("Use with caution: the operation that checked this result also produced something it was built "
                  "from. A separate check is needed.")
# COPY: (Security Brief 3, SR3-1) why a check cannot verify a result whose making G6 cannot trace in full.
UNTRACED_CHECK = ("Use with caution: G6 cannot trace everything this result was built from, so this check cannot be "
                  "shown to be independent of it.")
# COPY: (Security Brief 3, SR3-3) why the send's receipts check verifies no more than G6's own record of the send.
RECEIPTS_LOCAL = ("Use with caution: this check reads only G6’s own record of the send. It confirms what that record "
                  "says, not that any message was delivered.")
# COPY: (task #182, lead ruling S-2) why a check's pass on a retry, after its own answer was sent to the worker, is capped.
DISCLOSED_CHECK = ("This check passed only after its own answer was sent to the worker, so the pass does not count as "
                   "independent evidence.")
# COPY: (DECISIONS #22) why a test check in a plan the caller wrote can refute a change but never verify it.
UNDECLARED_TESTS = ("Use with caution: nobody declared who wrote these tests, so G6 cannot show they are independent of "
                    "the change. They can show it fails, never verify it.")
# COPY: why a test check whose tests ran with the owner's permissions (no operating-system isolation) never verifies.
UNCONFINED_RUN = ("These tests ran with your permissions, not confined by the operating system ({why}), so the code they "
                  "ran could have changed what G6 reads. They can show the change fails, never verify it.")
#: The reasons a check is not independent of what it judges, each carried as it is into the verdict, the unit and the
#: roll-up (Gate W ruling A-5; Security Brief 3).
NOT_INDEPENDENT = frozenset({AUTHORITY_COPY["verdict_self_check"], ANCESTOR_CHECK, UNTRACED_CHECK, RECEIPTS_LOCAL,
                             DISCLOSED_CHECK, UNDECLARED_TESTS})

#: Approved by the founder on 2026-10-06 (G1): contradictory premises cannot support a result.
CONTRADICTORY_PREMISES = (
    "Could not verify: the check's assumptions contradict each other, so this check cannot support the result. "
    "Correct the assumptions and run it again."
)

#: COPY: (G1, task #156) a check that cannot fail cannot support the result.
NON_FALSIFIABLE_CHECK = (
    "Could not verify: this check would pass whatever the result says, so it cannot support the result."
)
# COPY: (G1, task #156) a plan refusal: produced_facts must name this check's facts that read the checked unit's result.
INVALID_PRODUCED_FACTS = (
    "oracle.produced_facts must list one or more of this check's facts, each read from the result of the unit it "
    "checks"
)
# COPY: (G1, task #156) why a passing formal check that is not set up as a proof against the result cannot verify.
FORMAL_CHECK_CAPPED = (
    "This formal check passed, but it can verify a result only when it reads the result's own figures and expects "
    "no counterexample, so it does not count as verification here."
)


#: The rules a ``formal_methods.solve`` verdict is made under, recorded with it (task #165). G1 (task #156, 2026-10-06)
#: added the premise and non-vacuity solves and the proof-shaped, marked check; a formal verdict that records other
#: rules, or none (every one stored before this was recorded), is read at A3 (``runs.RunLedger.verdicts``), the class
#: those rules give a formal check that cannot show it could have failed: never reused, never rolled up verified, so
#: the check runs again. Change it when the formal rules change.
FORMAL_RULES = "G1"


def authority_rank(found: str) -> int:
    """A class's place on the ladder; anything unmeasured or unknown sits with A0 (fail closed)."""
    return AUTHORITY.index(found) if found in AUTHORITY else 0


def normalize_state(value: str) -> str:
    """The canonical (hyphen) state for either vocabulary; anything else is refused."""
    norm = str(value).strip().lower()
    norm = _ALIASES.get(norm, norm)
    if norm not in _RANK:
        raise ValueError(f"not a completion state: {value!r}; one of {STATES}")
    return norm


def rank(state: str) -> int:
    """Lower is worse."""
    return _RANK[normalize_state(state)]


def _worst(a: str, b: str) -> str:
    return a if _RANK[a] <= _RANK[b] else b


def _merged(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


@dataclass(frozen=True)
class Outcome:
    state: str
    degraded: bool = False
    reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    source: str = ""

    def __post_init__(self) -> None:
        if self.state not in _RANK:
            raise ValueError(f"not a canonical completion state: {self.state!r}; one of {STATES}")
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def demote(self, state: str, reason: str) -> Outcome:
        """At most ``state``, with the reason recorded; a worse state stays."""
        return Outcome(_worst(self.state, normalize_state(state)), self.degraded,
                       _merged(self.reasons, (reason,)), self.evidence, self.source)

    def as_record(self) -> dict:
        """The stored form (``from_record`` reads it back)."""
        return {"state": self.state, "degraded": self.degraded, "reasons": list(self.reasons),
                "evidence": list(self.evidence), "source": self.source}

    @classmethod
    def from_record(cls, record: Any, *, missing: str = "no outcome was recorded") -> Outcome:
        """A stored outcome; an absent or unreadable one is blocked (nothing finished)."""
        if not isinstance(record, dict) or record.get("state") not in _RANK:
            return cls(BLOCKED, reasons=(missing,))
        return cls(record["state"], degraded=bool(record.get("degraded")), reasons=tuple(record.get("reasons", ())),
                   evidence=tuple(record.get("evidence", ())), source=str(record.get("source", "")))

    @classmethod
    def from_envelope(cls, reply: Any, *, source: str = "") -> Outcome:
        """The outcome of one component reply, never better than anything it declares."""
        if not isinstance(reply, dict):
            return cls(BLOCKED, reasons=(f"not an envelope: {type(reply).__name__}",), source=source)
        state, degraded, reasons = QUALIFIED, False, []
        product = outcome_of(reply)
        if product == BLOCKED:
            state = BLOCKED
            reasons.append("the reply declares blocked-escalated")
        elif product == "error":
            state = BLOCKED
            reasons.append(f"the call failed: {_error_text(reply)}")
        levels, truncated = _levels(reply)
        if truncated:
            state = BLOCKED
            reasons.append(f"the reply nests deeper than {MAX_DEPTH} levels; nothing below is trusted")
        for level in levels:
            holders = [level, *(level[k] for k in _HOLDERS if isinstance(level.get(k), dict))]
            for holder in holders:
                blocked = _blocks(holder)
                if blocked:
                    state = BLOCKED
                    reasons.append(blocked)
                if holder.get("degraded"):
                    degraded = True
                    reasons.append(f"degraded: {_text(holder.get('degradation_reason')) or 'no reason given'}")
                if _declared(holder.get("completion_state")) == VERIFIED:
                    reasons.append("the component's own 'verified' is capped at qualified-draft: "
                                   "only an independent verdict promotes")
            meta = level.get("metadata") if isinstance(level.get("metadata"), dict) else {}
            for holder in (level, meta):
                unread = list(holder.get("unrecognised_parameters") or []) + list(
                    holder.get("misplaced_parameters") or [])
                if holder.get("answered_on_defaults"):
                    state = BLOCKED
                    reasons.append(f"answered on defaults: none of the supplied inputs was read {unread or ''}".strip())
                elif unread:
                    degraded = True
                    reasons.append(f"parameters with no effect: {unread}")
        unanswered = _unanswered(reply)
        if unanswered:
            state = BLOCKED
            reasons.append(unanswered)
        return cls(state, degraded, _merged(tuple(r[:_REASON_CHARS] for r in reasons)), (), source)


def combine(*outcomes: Outcome) -> Outcome:
    """Worst state, any degradation, every reason and evidence id (order kept, duplicates dropped)."""
    if not outcomes:
        raise ValueError("nothing to combine: an empty roll-up has no outcome, and is never verified")
    state = outcomes[0].state
    for out in outcomes[1:]:
        state = _worst(state, out.state)
    return Outcome(state, any(o.degraded for o in outcomes), _merged(*(o.reasons for o in outcomes)),
                   _merged(*(o.evidence for o in outcomes)), "combine")


def suite_limits(suite: Any) -> tuple[str, ...]:
    """Known limits on the bench's comparison, shared by new calls and historical receipt reads.

    This rule interprets explicit limitations; it does not attest old execution or reconstruct missing receipts.
    """
    if not isinstance(suite, dict):
        return ()
    limits = []
    if suite.get("status") == "not_run":
        limits.append("The full suite could not be compared; only completed named checks have results. "
                      "Resolve the suite runner issue and rerun the check.")
    if suite.get("not_collected"):
        limits.append(f"{suite['not_collected']} test files could not be imported, so their tests were not compared. "
                      "Resolve the import errors and rerun the check.")
    return tuple(limits)


def limited_outcome(outcome: Outcome, reasons: tuple[str, ...]) -> Outcome:
    """Apply an evidence limit down-only; a failed check never becomes a qualified pass."""
    if not reasons:
        return outcome
    return Outcome(_worst(outcome.state, QUALIFIED), True, _merged(outcome.reasons, reasons),
                   outcome.evidence, outcome.source)


def suite_receipt_limits(closed: list[dict]) -> dict[tuple[str, str, str, str], tuple[str, ...]]:
    """Limits keyed by (run, check, judged unit, artifact), from owner-scoped raw run.closed details.

    Use the complete identity when projecting a verdict. Another operation's similarly named field is not a
    bench receipt. Raw observations remain untouched, including failures and superseded attempts.
    """
    found = {}
    for detail in closed:
        records = detail.get("verification")
        if not isinstance(records, (list, tuple)):
            continue
        for record in records:
            if (not isinstance(record, dict) or record.get("op") != "workforce.run_tests"
                    or record.get("check") != detail.get("unit")):
                continue
            key = (detail.get("run_id"), record.get("check"), record.get("judges"), record.get("artifact_id"))
            reasons = suite_limits(record.get("suite"))
            if reasons and all(isinstance(part, str) and part for part in key):
                found[key] = _merged(found.get(key, ()), reasons)
    return found


def current_suite_limits(closed: list[dict]) -> tuple[str, ...]:
    """Only receipts on each unit's latest closed output can qualify the mission's current result."""
    latest = {detail.get("unit"): detail["artifact_id"] for detail in closed
              if detail.get("state") == "closed" and detail.get("artifact_id")}
    return _merged(*(reasons for (_, _, unit, artifact), reasons in suite_receipt_limits(closed).items()
                     if latest.get(unit) == artifact))


@dataclass(frozen=True)
class Verdict:
    """One verifier's finding on one artifact. The only way an outcome becomes ``verified``."""

    verifier_kind: str
    result: str
    executed: bool
    independent: bool
    model_calls: int = 0
    reason: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.verifier_kind not in VERIFIER_KINDS:
            raise ValueError(f"unknown verifier kind {self.verifier_kind!r}; one of {VERIFIER_KINDS}")
        if self.result not in VERDICT_RESULTS:
            raise ValueError(f"unknown verdict result {self.result!r}; one of {VERDICT_RESULTS}")
        if not isinstance(self.model_calls, int) or self.model_calls < 0:
            raise ValueError(f"model_calls must be a count, got {self.model_calls!r}")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def outcome(self) -> Outcome:
        source = f"verdict:{self.verifier_kind}"
        if self.result == "fail":
            return Outcome(BLOCKED, reasons=(f"verification failed ({self.verifier_kind}): {self.reason}",),
                           evidence=self.evidence, source=source)
        if self.result == "unmeasured":
            return Outcome(QUALIFIED, reasons=(f"verification not measured ({self.verifier_kind}): {self.reason}",),
                           evidence=self.evidence, source=source)
        missing = []
        if not self.executed:
            missing.append("not executed")
        if not self.independent:
            missing.append("not independent of the producer")
        if self.model_calls:
            missing.append(f"the verifier asked a model ({self.model_calls} calls)")
        if self.verifier_kind == "model_critic":
            missing.append("a model critic is not an oracle")
        if missing:
            return Outcome(QUALIFIED, reasons=("a pass that cannot verify: " + "; ".join(missing),),
                           evidence=self.evidence, source=source)
        return Outcome(VERIFIED, evidence=self.evidence, source=source)

    @classmethod
    def from_step(cls, step: StepResult, *, verifier_kind: str, independent: bool, model_calls: int = 0) -> Verdict:
        """A chain step's result as a verdict: ran is executed, anything else is unmeasured."""
        if step.status == "ran":
            return cls(verifier_kind, "pass" if step.ok else "fail", True, independent, model_calls, step.reason)
        return cls(verifier_kind, "unmeasured", False, independent, model_calls, step.reason)


def answer(reply: Any) -> dict:
    """The component's own answer: the innermost result mapping beneath the envelope ({} if none)."""
    if not isinstance(reply, dict):
        return {}
    levels, truncated = _levels(reply)
    return {} if truncated or len(levels) < 2 else dict(levels[-1])


def lookup(mapping: Any, path: str) -> tuple[bool, Any]:
    """``(found, value)`` for a dotted path through nested mappings (a digit part indexes a list)."""
    value = mapping
    for part in str(path).split("."):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
            continue
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def verdict_from_reply(reply: Any, *, verifier_kind: str, independent: bool, model_calls: int, field_path: str,
                       equals: Any) -> Verdict:
    """An oracle's reply as a verdict: executed only when the component answered (``chain.verdict``
    ran), pass only when its answer holds ``equals`` at ``field_path``."""
    if not isinstance(reply, dict):
        return Verdict(verifier_kind, "unmeasured", False, independent, model_calls, "the oracle did not answer")
    status, ok, reason = chain_verdict(reply)
    if status != "ran":
        return Verdict(verifier_kind, "unmeasured", False, independent, model_calls, _text(reason))
    if not ok:
        return Verdict(verifier_kind, "fail", True, independent, model_calls, _text(reason))
    found, value = lookup(answer(reply), field_path)
    if not found:
        return Verdict(verifier_kind, "unmeasured", True, independent, model_calls,
                       f"the oracle's answer carries no {field_path!r}")
    if value != equals:
        return Verdict(verifier_kind, "fail", True, independent, model_calls,
                       _text(f"{field_path} is {value!r}, the oracle expects {equals!r}"))
    return Verdict(verifier_kind, "pass", True, independent, model_calls, f"{field_path} is {equals!r}")


def answered_field(reply: Any, name: str) -> bool:
    """Whether the component's own answer carries ``name`` (what a capability probe expects).

    Only the nested results are searched, never the outer envelope's keys: an envelope field cannot
    stand in for the component's answer."""
    if not isinstance(reply, dict) or not name:
        return False
    levels, _ = _levels(reply)
    return any(name in level for level in levels[1:])


# --- reading helpers (private: the single reader's own) ---


def _text(value: Any) -> str:
    return str(value or "").strip()[:_REASON_CHARS]


def _error_text(reply: dict) -> str:
    err = reply.get("error")
    if isinstance(err, dict):
        return " ".join(_text(err.get(k)) for k in ("code", "message") if err.get(k)) or "ok is false"
    return _text(err) or "ok is false"


def _declared(value: Any) -> str | None:
    """A declared completion state in either vocabulary, read down-only (the product's own
    middleware.usage reading): any blocked_* or a failure is a block, any verified_* is verified."""
    if not isinstance(value, str):
        return None
    norm = value.strip().lower().replace("-", "_")
    if norm.startswith("blocked") or norm in {"failed", "error"}:
        return BLOCKED
    if norm == "verified" or norm.startswith("verified_ready"):
        return VERIFIED
    if norm.startswith("qualified") or norm == "awaiting_cost_confirmation":
        return QUALIFIED
    return None


def _has_error(value: Any) -> bool:
    """An error the holder reports. The ok envelope carries an EMPTY error payload: that is none."""
    if isinstance(value, dict):
        return bool(value.get("code") or value.get("message"))
    if isinstance(value, str):
        return bool(value.strip())
    return value is True


def _blocks(holder: dict) -> str:
    """Why this one holder (a level, or a state holder beside it) says the call did not succeed, or ""."""
    if _declared(holder.get("completion_state")) == BLOCKED:
        why = holder.get("block_reason") or holder.get("degradation_reason") or ""
        return f"declared {holder.get('completion_state')}: {_text(why)}".rstrip(": ")
    if holder.get("ok") is False or holder.get("success") is False:
        return f"reported a failure: {_error_text(holder)}"
    if _has_error(holder.get("error")):
        return f"reported an error: {_error_text(holder)}"
    # A formal answer marked "computed" (an LP optimum, a model count) never asked satisfiable (W3 formal1 D1).
    if "satisfiable" in holder and holder.get("satisfiable") is None and holder.get("answer_kind") != "computed":
        return "the solver answered unknown"
    if "valid" in holder and holder.get("valid") is None:
        return "validity unknown"
    status = str(holder.get("status") or "").strip().lower()
    if status in _UNANSWERED_STATUS:
        return f"component status {status}"
    return ""


def _nested(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith("{") and len(value) <= _MAX_PARSED_CHARS:
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _levels(reply: dict) -> tuple[list[dict], bool]:
    """(the reply and each nested ``result`` mapping, a JSON object string parsed; whether a nested
    result lies deeper than MAX_DEPTH and was not read)."""
    levels, current = [reply], reply
    for _ in range(MAX_DEPTH):
        nested = _nested(current.get("result"))
        if not isinstance(nested, dict):
            return levels, False
        levels.append(nested)
        current = nested
    return levels, isinstance(_nested(current.get("result")), dict)


def _unanswered(reply: dict) -> str:
    """chain.verdict's reading of whether the component answered at all: a component error, an
    unknown solver or validity, an ambiguous status, or a declared block. A degraded result that
    still answers is NOT this (it is degraded, read above), so the result's own `degraded` flag is
    left out of this probe."""
    result = reply.get("result")
    probe = dict(reply)
    if isinstance(result, dict):
        probe["result"] = {k: v for k, v in result.items() if k != "degraded"}
    status, ok, reason = chain_verdict(probe)
    if status == "not_measured" or not ok:
        return f"the component did not answer: {reason}"[:_REASON_CHARS]
    return ""


# -- the product's down-only reading of a reply's declared state (vendored from the archive's
# ``middleware.usage.outcome_of``, without its usage-recording database writer) --------------------------------------
_usage_QUALIFIED_STATES = frozenset({"qualified_draft", "qualified", "awaiting_cost_confirmation"})
_usage_STATE_HOLDERS = (None, "reliability_envelope", "trust", "trust_panel")
#: A job's own `state` for a result that is not in yet: nothing has ended, so no outcome.
_usage_NOT_FINISHED = frozenset({"queued", "running", "pending", "needs_input"})
#: The product's DOWN-ONLY rule: of every state a result declares, the most degraded wins --
#: a block; then a job not finished (no outcome yet, "none"); then a failure; then the rest.
_usage_PRECEDENCE = ("blocked-escalated", "pending", "error", "qualified-draft", "verified")
_usage_max_parsed_result_chars = 256 * 1024


def _usage_state(value: str) -> str:
    norm = value.strip().lower().replace("-", "_")
    if norm == "verified" or norm.startswith("verified_ready"):
        return "verified"
    if norm in _usage_QUALIFIED_STATES:
        return "qualified-draft"
    if norm == "blocked" or norm.startswith("blocked_"):
        return "blocked-escalated"
    if norm in {"failed", "error"}:
        return "error"
    return "none"


def _usage_declared(level: dict) -> set[str]:
    """Every state one level of a result declares -- "pending" for a job not finished."""
    found = set()
    for key in _usage_STATE_HOLDERS:
        holder = level if key is None else level.get(key)
        if isinstance(holder, dict) and isinstance(holder.get("completion_state"), str):
            found.add(_usage_state(holder["completion_state"]))
    # A job's own `state` can only LOWER the outcome (blocked, failed, not finished): the job
    # tools never declare success there, and another tool's `state` may mean anything.
    job = level.get("state")
    if isinstance(job, str):
        norm = job.strip().lower()
        lowered = "pending" if norm in _usage_NOT_FINISHED else _usage_state(norm)
        if lowered in {"pending", "blocked-escalated", "error"}:
            found.add(lowered)
    return found - {"none"}


def _usage_reports_failure(holder: Any) -> bool:
    """`ok: false` or `success: false` -- how the tools report a failure they return rather
    than raise (the MCP error envelope does the same)."""
    return isinstance(holder, dict) and (holder.get("ok") is False or holder.get("success") is False)


def _usage_levels(result: Any) -> list[dict] | None:
    """The result's top and `invoke_component`'s nested `result`; None if it is not a dict
    (plain text, or a string too large to parse)."""
    if isinstance(result, str):
        if len(result) > _usage_max_parsed_result_chars:
            return None
        result = json.loads(result)
    if not isinstance(result, dict):
        return None
    return [result] + ([result["result"]] if isinstance(result.get("result"), dict) else [])


def _usage_outcome(levels: list[dict]) -> str:
    declared = set().union(*(_usage_declared(level) for level in levels))
    if any(_usage_reports_failure(level) for level in levels):
        declared.add("error")
    state = next((state for state in _usage_PRECEDENCE if state in declared), "none")
    return "none" if state == "pending" else state


def outcome_of(result: Any) -> str:
    """How a reply ended by the down-only rule: of every state it declares (at its top and under ``result``), the most
    degraded wins: a block, then a job not finished (``none``), then a reported failure (``error``), then
    qualified-draft over verified. Never raises."""
    try:
        levels = _usage_levels(result)
        return "none" if levels is None else _usage_outcome(levels)
    except Exception:  # noqa: BLE001 - a reply that cannot be read declares nothing
        return "none"
