# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Rungs: how one attempt at a unit is carried out (workforce OS plan, P0 T6).

P0 has two, both code, no model:

* deterministic: a do unit's ``call`` through its worker's door, its parameters bound from the
  artifacts of the units it consumes; the component's own answer is the artifact; the outcome is at
  most qualified-draft (a produced output is a draft until judged);
* verify: a verify unit's ``oracle`` through a SEPARATE read-only worker's door, bound to the
  artifact it judges; the oracle's reply becomes a ``Verdict`` (executed only when the component
  answered, pass only when the answer holds the expected value), stored with the model-free
  declaration of the oracle's op; the outcome is the verdict's, demoted by a degraded or blocked call.

Local, pull and human rungs are V1. A unit no rung here can run is blocked, never guessed.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from g6.workforce import acceptance, reuse
from g6.workforce.calls import CallResult, Door
from g6.workforce.capabilities import ParamContract
from g6.workforce.contracts import (
    ANCESTOR_CHECK,
    AUTHORITY,
    AUTHORITY_COPY,
    BLOCKED,
    CONTRADICTORY_PREMISES,
    DISCLOSED_CHECK,
    FORMAL_CHECK_CAPPED,
    FORMAL_RULES,
    NON_FALSIFIABLE_CHECK,
    QUALIFIED,
    RECEIPTS_LOCAL,
    UNCONFINED_RUN,
    UNDECLARED_TESTS,
    UNTRACED_CHECK,
    VERIFIED,
    VERIFIER_GRADE,
    Outcome,
    Verdict,
    answer,
    lookup,
    verdict_from_reply,
)
from g6.workforce.plans import FACT_SORTS, UnitSpec, smt_sort
from g6.workforce.runs import VerdictRecord
from g6.workforce.truth import RECORDER_GAP

#: The bench's ops, whose answer says how its tests ran (``isolation``).
_BENCH_OPS = frozenset({("workforce", "run_tests"), ("workforce", "run_tests_in_place")})


@dataclass(frozen=True)
class RungResult:
    outcome: Outcome
    artifact: dict | None = None
    calls: tuple = ()
    model_calls: int = 0
    failure_code: str = ""
    verdict: VerdictRecord | None = None
    #: What each call in ``calls`` cost, in the same order (None: refused before it was sent; Phase E E1a).
    costs: tuple = ()
    #: Each check's Ledger §10 record (E2c): its mechanism, whether it ran independently and model-free, its result and what it counted.
    mechanisms: tuple = ()


#: How a call that raised is recorded (``calls.RAISED`` names the kinds; Phase E E2a).
CALL_FAILURES = {"timeout": "call_timeout", "unreachable": "call_unreachable", "raised": "call_raised",
                 "timeout_unconfirmed": "call_timeout_unconfirmed"}
#: Every failure code this module records (``classes.WORKFORCE_CODES`` maps each; door refusals come as they are).
FAILURE_CODES = ("no_rung", "unbound_input", "nothing_to_verify", "no_oracle", "oracle_unbound", "call_blocked",
                 "verify_failed", "verify_unmeasured", *CALL_FAILURES.values())


def _call_failure(result: CallResult) -> str:
    """Why a call did not answer, as a failure code ("" when it answered and was not blocked)."""
    if result.refused:
        return result.refusal_code
    if result.raised:
        return CALL_FAILURES[result.raised]
    if result.outcome.state == BLOCKED:
        return "call_blocked"
    return ""


def _verify_failure(result: CallResult, verdict: Verdict) -> str:
    """Why a check did not pass: its call did not answer, it failed, or its answer could not decide."""
    if result.refused or result.raised:
        return _call_failure(result)
    if verdict.result == "fail":
        return "verify_failed"
    if verdict.result == "unmeasured":
        return "verify_unmeasured"
    return _call_failure(result)


def _blocked(reason: str, code: str) -> RungResult:
    return RungResult(Outcome(BLOCKED, reasons=(reason,)), failure_code=code)


def _read(source: str, name: str, inputs: dict[str, dict | None]) -> tuple[bool, Any, str]:
    unit_key, _, path = str(source).partition(".")
    artifact = inputs.get(unit_key)
    if artifact is None:
        return False, None, f"{unit_key!r} has produced no artifact to bind {name!r} from"
    found, value = lookup(artifact.get("content"), path)
    if not found:
        return False, None, f"{unit_key!r}'s artifact carries no {path!r} for {name!r}"
    return True, value, ""


def _bound(step: dict, inputs: dict[str, dict | None], *, free_facts: tuple = ()) -> tuple[dict, str]:
    """The step's parameters with each bound one read from an input artifact, and each fact appended to
    its ``assertions`` as an SMT equality; or a reason it cannot be."""
    params = dict(step.get("params") or {})
    for param, source in (step.get("bind") or {}).items():
        found, value, why = _read(source, param, inputs)
        if not found:
            return {}, why
        params[param] = value
    facts = step.get("facts") or {}
    if facts:
        declared = params.get("variables") if isinstance(params.get("variables"), dict) else {}
        assertions = list(params.get("assertions") or [])
        for var, source in facts.items():
            if var in free_facts:
                continue
            found, value, why = _read(source, var, inputs)
            if not found:
                return {}, why
            sort = smt_sort(declared.get(var, ""))
            numeral = smt_number(value, integer=sort == "int") if sort in FACT_SORTS else None
            if numeral is None:
                return {}, f"fact {var!r} ({source}) is not a finite number the solver can take: {value!r}"[:300]
            assertions.append(f"(= {var} {numeral})")
        params["assertions"] = assertions
    return params, ""


#: A fact's numeral is at most this long (an artifact cannot hand the solver a gigantic literal).
_NUMERAL_CHARS = 64
#: How much of a failing oracle's answer its verdict keeps (V4b: what the producer is sent back).
_ANSWER_CHARS = 600
# COPY: Why a patch failed when only the rest of the repository's tests broke (ECHA Stage A).
SUITE_BROKE = "{count} test(s) that passed before this change no longer pass: {names}."


def shown_answer(answer) -> str:
    """The oracle's answer as the worker is shown it: JSON that still parses after the cut at ``_ANSWER_CHARS`` (the focused
    brief reads it again). A long list of the whole suite's broken tests gives way, id by id, to the named tests that
    follow it in the sorted keys; ``more`` says how many were left out (review I-4). How the bench ran (``isolation``)
    is not part of it: that is the run's, not what the oracle found, and the unit's outcome names it."""
    if isinstance(answer, dict) and "isolation" in answer:
        answer = {key: value for key, value in answer.items() if key != "isolation"}
    text = json.dumps(answer, sort_keys=True, default=str)
    suite = answer.get("suite") if isinstance(answer, dict) else None
    if len(text) <= _ANSWER_CHARS or not (isinstance(suite, dict) and suite.get("tests")):
        return text
    kept = list(suite["tests"])
    while kept and len(text) > _ANSWER_CHARS:
        kept.pop()
        text = json.dumps({**answer, "suite": {**suite, "tests": kept, "more": len(suite["tests"]) - len(kept)}},
                          sort_keys=True, default=str)
    return text
#: How a failing oracle's answer starts among its verdict's reasons (V4b; #182 reads a send-back's disclosure by it).
ANSWERED = "the oracle answered: "
#: Keys whose presence in a disclosed answer hands the worker what the check looks for (#182, ruling S-4b (ii)).
_PAYLOAD_KEYS = frozenset({"counterexamples", "expected", "probe"})
#: Values too common to show that an answer was copied (S-4b (i); booleans, None and "" are left out too).
_TRIVIAL = (Decimal(0), Decimal(1))
#: An answer cut short in the record is read as text: its quoted strings and numbers, and its payload keys.
_TOKENS = re.compile(r'"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)')
_PAYLOAD_TEXT = re.compile(r'"(?:counterexamples|expected|probe)"\s*:')


def smt_number(value: Any, *, integer: bool = False) -> str | None:
    """An SMT-LIB numeral for a finite number (a decimal string, int or float), or None: no other text
    can reach a formula through a fact."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    # No arithmetic that the decimal context could round or overflow (Codex review of V1c): the numeral's
    # width is projected from the digits and exponent first, and only the exact copy_abs/int/format follow.
    _sign, digits, exponent = number.as_tuple()
    width = len(digits) + exponent if exponent >= 0 else max(len(digits), -exponent) + 1
    if width > _NUMERAL_CHARS:
        return None
    magnitude = number.copy_abs()
    if integer:
        if exponent < 0 and any(digits[exponent:]):  # a nonzero digit after the point
            return None
        text = str(int(magnitude))
    else:
        text = format(magnitude, "f")
        text = text if "." in text else text + ".0"
    if len(text) > _NUMERAL_CHARS:
        return None
    return f"(- {text})" if number < 0 else text


def _fold(value: Any, out: set) -> None:
    """The comparable leaves of ``value`` (S-4b (i)): numbers as numbers (6 == 6.0, a numeric string too), other
    strings case- and whitespace-folded; booleans, None, "", 0 and 1 left out."""
    if isinstance(value, dict):
        for item in value.values():
            _fold(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _fold(item, out)
    elif isinstance(value, (int, float, Decimal, str)) and not isinstance(value, bool):
        text = " ".join(str(value).casefold().split())
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            number = None
        if number is not None and number.is_finite():
            if number not in _TRIVIAL:
                out.add(number)
        elif text:
            out.add(text)


def _payload(value: Any) -> bool:
    """A counterexample, probe or expected output anywhere in a parsed answer (S-4b (ii))."""
    if isinstance(value, dict):
        return bool(_PAYLOAD_KEYS & set(value)) or any(_payload(item) for item in value.values())
    return isinstance(value, (list, tuple)) and any(_payload(item) for item in value)


def _parsed(rest: str) -> tuple[Any, str]:
    """The answer that follows ``ANSWERED`` in a text: (its JSON, "") or, cut short in the record, (None, its text)."""
    try:
        return json.JSONDecoder().raw_decode(rest)[0], ""
    except ValueError:
        return None, rest


def _answers(message: Mapping[str, Any], check: str, check_of: Mapping[str, str]) -> list[tuple[Any, str]]:
    """What of ``check``'s own answer one message carried: its ``the oracle answered:`` reason (the message names that
    check's verdict) or a focused brief's evidence for it (its failing tests, counterexamples or answered reason)."""
    refs = message.get("refs") or {}
    text, found = str(message.get("text", "")), []
    if ANSWERED in text and check_of.get(str(refs.get("verdict_id", ""))) == check:
        found.append(_parsed(text.split(ANSWERED, 1)[1]))
    focused = refs.get("focused") if isinstance(refs.get("focused"), dict) else {}
    for item in focused.get("failing") or []:
        if not isinstance(item, dict) or item.get("check") != check:
            continue
        if item.get("tests") or item.get("counterexamples"):
            found.append(({k: item[k] for k in ("tests", "counterexamples") if k in item}, ""))
        if ANSWERED in str(item.get("reason", "")):
            found.append(_parsed(str(item["reason"]).split(ANSWERED, 1)[1]))
    return found


def _judged(oracle: Mapping[str, Any], verifies: str, produced: Mapping[str, Any]) -> set:
    """The comparable leaves of the output's fields the check's bound parameters and facts read (S-4b (i))."""
    out: set = set()
    for source in (*(oracle.get("bind") or {}).values(), *(oracle.get("facts") or {}).values()):
        unit, _, path = str(source).partition(".")
        found, value = lookup(produced.get("content"), path) if unit == verifies else (False, None)
        if found:
            _fold(value, out)
    return out


def disclosed(check: str, oracle: Mapping[str, Any], verifies: str, produced: Mapping[str, Any],
              messages: list[dict], verdicts: list[dict]) -> bool:
    """#182 (lead rulings S-2, S-4b): whether a send-back to ``verifies`` about an earlier output than ``produced``
    carried ``check``'s own answer and either (i) a value ``check`` judges in ``produced`` appears in that answer, or
    (ii) the answer carried a counterexample, a probe or an expected output. ``messages`` are the mission's recorded
    messages and ``verdicts`` its verdicts (a send-back names the verdict whose reasons it carried), so it is read from
    the ledger and holds across a restart. The runtime and the auditor both ask this."""
    check_of = {str(v["verdict_id"]): v["unit_key"] for v in verdicts}
    judged = None
    for message in messages:
        refs = message.get("refs") or {}
        focused = refs.get("focused") if isinstance(refs.get("focused"), dict) else {}
        about = {refs.get("artifact_id"), (focused.get("rejected") or {}).get("artifact_id")}
        if message.get("to") != verifies or produced.get("artifact_id") in about:
            continue
        for told, raw in _answers(message, check, check_of):
            if judged is None:
                judged = _judged(oracle, verifies, produced)
            if told is not None:
                carried: set = set()
                _fold(told, carried)
                if _payload(told) or carried & judged:
                    return True
            elif _PAYLOAD_TEXT.search(raw) or any(_leaf_in(token, judged) for token in _TOKENS.findall(raw)):
                return True
    return False


def _leaf_in(token: tuple[str, str], judged: set) -> bool:
    found: set = set()
    _fold(token[0] or token[1], found)
    return bool(found & judged)


def reads_its_artifact(spec: UnitSpec) -> bool:
    """A verify unit's oracle binds at least one parameter from the artifact of the unit it verifies (an
    oracle that checks only constants certifies nothing about that artifact)."""
    oracle = spec.oracle or {}
    sources = (*(oracle.get("bind") or {}).values(), *(oracle.get("facts") or {}).values())
    return any(str(source).partition(".")[0] == spec.verifies for source in sources)


def _call_record(result: CallResult) -> dict[str, Any]:
    record = {"component": result.component, "op": result.op, "state": result.outcome.state,
            "degraded": result.outcome.degraded, "refused": bool(result.refused), "refusal": result.refused,
            "refusal_code": result.refusal_code, "model_calls": result.model_calls,
            "charged_model_calls": result.charged_model_calls, "contract_sha": result.contract_sha}
    if (result.component, result.op) in _BENCH_OPS:   # whether its tests ran confined (reuse reads it)
        isolation = answer(result.reply).get("isolation") if not result.refused else None
        record["confined"] = isinstance(isolation, dict) and bool(isolation.get("backend"))
    if result.cost is not None and "resource_usage" in result.cost.metadata:
        record["resource_usage"] = result.cost.metadata["resource_usage"]
    return record


def run_do(spec: UnitSpec, door: Door, inputs: dict[str, dict | None]) -> RungResult:
    """A do unit on the deterministic rung."""
    if not spec.call:
        return _blocked(f"unit {spec.key!r} names no call, and only the deterministic rung runs in P0 "
                        "(local, pull and human rungs are V1)", "no_rung")
    params, why = _bound(spec.call, inputs)
    if why:
        return _blocked(f"unit {spec.key!r}: {why}", "unbound_input")
    component, op = spec.call["capability"]
    result = door.call(component, op, params)
    artifact = None
    if result.outcome.state != BLOCKED:
        artifact = {"schema": spec.output_schema, "content": answer(result.reply)}
    return RungResult(result.outcome.demote(QUALIFIED, "a produced output is a draft until it is judged"),
                      artifact=artifact, calls=(_call_record(result),), model_calls=result.model_calls,
                      failure_code=_call_failure(result), costs=(result.cost,))


def _count(value: Any) -> int | None:
    """A count an output states: a whole number, never a true/false value; None for anything else or nothing."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def independence(produced: Mapping[str, Any], oracle: tuple[str, str] | list[str], *, ledger: Any = None,
                 expect: Mapping[str, Any] | None = None) -> str:
    """Why ``oracle`` cannot certify ``produced`` independently, as its verdict's reason, or "" when it can. The runtime
    and the auditor both ask this (Security Brief 3):

    * the op made nothing in the output's ancestry (Gate W ruling A-5, followed across hops: ``reuse.lineage``, its
      bound and its fail-closed reads; an ancestry that cannot be read in full is not independent, SR3-1);
    * nor any of this owner's results with the same bytes as the output or anything it was built from (review I5, the
      lead's ruling: a copy needs no declared link; an independently computed identical answer is capped too, since G6
      never overstates);
    * a receipts check reads a send's own local record, so it never certifies independently (the library has no
      outbox, so the archive's narrower shadow-refusal claim does not arise; SR3-3)."""
    digests: set[str] = set()
    try:
        made = reuse.lineage(ledger, dict(produced), digests=digests)
    except reuse.ReuseRefused:
        return UNTRACED_CHECK
    if tuple(oracle) in made:
        own = (produced.get("provenance") or {}).get("call") or {}
        return AUTHORITY_COPY["verdict_self_check"] if (own.get("component"), own.get("op")) == tuple(oracle) \
            else ANCESTOR_CHECK
    if ledger is not None and any(tuple(oracle) in ledger.makers(digest) for digest in sorted(digests)):
        return ANCESTOR_CHECK
    if tuple(oracle) in acceptance.RECEIPT_OPS:
        return RECEIPTS_LOCAL
    return ""


def oracle_authority(found: str, oracle: tuple[str, str] | list[str], produced: Mapping[str, Any], *,
                     ledger: Any = None, expect: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """The class a check carries into its verdict: its op's own (A0 when unmeasured), except that a check that is not
    independent of what it judges (``independence``) carries A3 at most for that use, with the reason why."""
    cls = found if found in AUTHORITY else "A0"
    why = independence(produced, oracle, ledger=ledger, expect=expect)
    if why:
        return "A3" if cls == VERIFIER_GRADE else cls, why
    return cls, ""


def run_verify(spec: UnitSpec, door: Door, *, instance_id: str, produced: dict | None,
               inputs: dict[str, dict | None], contract: ParamContract | None, authority: str = "",
               test_provenance: str = "", ledger: Any = None, disclosed: bool = False,
               receipts: Mapping[str, str] | None = None) -> RungResult:
    """A verify unit on the verify rung: judges the artifact ``produced`` (its do unit's latest); ``ledger`` reads the
    results it was made from (``independence``); ``disclosed``: this check's own answer went back to the worker before
    ``produced`` was made (``disclosed``, #182), so its pass earns A3 at most. ``receipts`` (mission certificates on,
    RVC R6.4): the frozen test files' digests by path; a test check then records one receipt per named test (its
    result and the digest of the frozen file that ran: the bench runs only byte-identical test files)."""
    if produced is None:
        return _blocked(f"unit {spec.verifies!r} has produced nothing for {spec.key!r} to judge", "nothing_to_verify")
    if not spec.oracle:
        return _blocked(f"unit {spec.key!r} names no oracle, so nothing here can judge {spec.verifies!r}",
                        "no_oracle")
    if not reads_its_artifact(spec):
        return _blocked(f"unit {spec.key!r}'s oracle reads nothing from {spec.verifies!r}'s artifact, so it "
                        "cannot certify it", "oracle_unbound")
    params, why = _bound(spec.oracle, {**inputs, spec.verifies: produced})
    if why:
        return _blocked(f"unit {spec.key!r}: {why}", "unbound_input")
    component, op = spec.oracle["capability"]
    earned, authority_reason = oracle_authority(authority, (component, op), produced, ledger=ledger,
                                                expect=spec.oracle["expect"])
    regression = (component, op) == ("workforce", "run_tests") and "regression" in params
    if regression:
        earned = "A0"  # the bench's census does not measure this differential adapter
    if disclosed and earned == VERIFIER_GRADE:  # #182 (S-2): a check below A4 is left as it is (S-4b)
        earned, authority_reason = "A3", authority_reason or DISCLOSED_CHECK
    independent = door.worker.read_only and produced.get("producer") != instance_id
    if test_provenance in ("caller", "local_planner", "caller_and_task_derived"):
        independent = False
    elif not test_provenance and component == "workforce" and op in ("run_tests", "run_tests_in_place"):
        from g6.workforce import coding_certificate

        # DECISIONS #22: tests nobody declared the authorship of can refute a change, never verify it; the owner's
        # founder-d2 override counts the repository's tests as independent
        if coding_certificate.policy() != coding_certificate.FOUNDER_D2:
            independent, authority_reason = False, authority_reason or UNDECLARED_TESTS
    expect = spec.oracle["expect"]
    formal = (component, op) == ("formal_methods", "solve")
    marked = spec.oracle.get("produced_facts")
    proof_shaped = ((expect["field"] == "satisfiable" and expect["equals"] is False)
                    or expect == {"field": "proof", "equals": "UNSAT"})
    capped = formal and not (marked and proof_shaped)
    if capped and earned == VERIFIER_GRADE:
        earned = "A3"
    results = []
    verdict = None
    if formal:
        # An unsat negated property proves nothing when the bound premises already contradict each other.
        result = door.call(component, op, {**params, "formula": "true"})
        results.append(result)
        verdict = verdict_from_reply(None if result.refused else result.reply, verifier_kind=spec.oracle["kind"],
                                     independent=independent, model_calls=result.model_calls,
                                     field_path="satisfiable", equals=True)
        if verdict.result == "fail" and answer(result.reply).get("satisfiable") is False:
            verdict = replace(verdict, reason=CONTRADICTORY_PREMISES)
    if (formal and marked and proof_shaped
            and (verdict is None or (verdict.result == "pass" and result.outcome.state != BLOCKED))):
        # Keep static assertions and every other fact; only the judged figures may vary.
        unbound, _why = _bound(spec.oracle, {**inputs, spec.verifies: produced}, free_facts=tuple(marked))
        result = door.call(component, op, unbound)
        results.append(result)
        verdict = verdict_from_reply(None if result.refused else result.reply, verifier_kind=spec.oracle["kind"],
                                     independent=independent, model_calls=sum(r.model_calls for r in results),
                                     field_path="satisfiable", equals=True)
        if (verdict.result == "fail" and answer(result.reply).get("satisfiable") is False
                and result.outcome.state != BLOCKED):
            verdict = replace(verdict, reason=NON_FALSIFIABLE_CHECK)
    if verdict is None or (verdict.result == "pass" and result.outcome.state != BLOCKED):
        result = door.call(component, op, params)
        results.append(result)
        verdict = verdict_from_reply(None if result.refused else result.reply, verifier_kind=spec.oracle["kind"],
                                     independent=independent, model_calls=sum(r.model_calls for r in results),
                                     field_path=expect["field"], equals=expect["equals"])
    # A bench pass from tests that ran with the owner's permissions (no operating-system isolation, g6.core.confine)
    # stays below verifier grade: the code they ran could have changed what G6 reads.
    bench_run = (component, op) in _BENCH_OPS and not regression
    isolation = answer(result.reply).get("isolation") if bench_run else None
    confined = isinstance(isolation, dict) and bool(isolation.get("backend"))
    unconfined = ""
    if bench_run and not confined:
        unconfined = UNCONFINED_RUN.format(why=(isolation.get("why") if isinstance(isolation, dict) else "")
                                           or "the bench did not say how they ran")
        if earned == VERIFIER_GRADE:
            earned = "A3"
    probe = answer(result.reply).get("probe", {}) if regression else {}
    suite = answer(result.reply).get("suite")
    partial = (answer(result.reply) if (component, op) == ("workforce", "run_tests")
               and not regression and answer(result.reply).get("interruption") else {})
    if isinstance(suite, dict) and suite.get("status") == "fail" and verdict.result == "fail":
        broken = list(suite.get("tests") or [])  # the person is told what broke, not only that a check failed (review I-5)
        said = ", ".join(broken[:3]) + (f" and {suite.get('broken', 0) - 3} more" if suite.get("broken", 0) > 3 else "")
        verdict = replace(verdict, reason=(SUITE_BROKE.format(count=suite.get("broken", len(broken)), names=said)
                                           if broken else str(suite.get("reason", ""))))
    if regression and probe.get("status") not in ("pass", "fail", "not_applicable"):
        verdict = replace(verdict, result="unmeasured", executed=False, reason=probe.get("reason", ""))
    if authority_reason and verdict.result == "pass":
        verdict = replace(verdict, reason=authority_reason)
    record = VerdictRecord(verifies_key=spec.verifies, artifact_id=produced["artifact_id"],
                           producer_instance=str(produced.get("producer", "")), verdict=verdict,
                           model_free=bool(contract and contract.model_free),
                           artifact_sha=str(produced.get("sha256", "")), authority=earned,
                           formal_rules=FORMAL_RULES if formal else "")
    outcome = record.outcome()
    if capped and verdict.result == "pass":
        outcome = outcome.demote(outcome.state, FORMAL_CHECK_CAPPED)
    if unconfined and verdict.result == "pass":
        outcome = outcome.demote(outcome.state, unconfined)
    if bench_run and outcome.state == VERIFIED:
        # WORKFORCE-01: a confined run's pass is still read from a record the process that ran the patch wrote
        outcome = outcome.demote(VERIFIED, RECORDER_GAP)
    if partial:
        # Lead with missing scope before any truncated JSON or long failure list.
        outcome = outcome.demote(outcome.state, partial["interruption"]["reason"])
    if verdict.result == "fail":  # what the oracle saw goes with its verdict (V4b: the producer is told why)
        shown = shown_answer({"counterexamples": probe["counterexamples"]} if probe else answer(result.reply))
        outcome = outcome.demote(outcome.state, f"{ANSWERED}{shown[:_ANSWER_CHARS]}")
    if partial:
        # Plain repair feedback survives the legacy JSON display limit. The full
        # observations below remain bound to this run and actual artifact.
        for node, passed in partial.get("tests", {}).items():
            if passed is False:
                outcome = outcome.demote(outcome.state, f"{node}: {partial.get('causes', {}).get(node, 'the check failed')}")
    for called in results:
        if called.outcome.state == BLOCKED:
            outcome = Outcome(BLOCKED, reasons=outcome.reasons + called.outcome.reasons, source=f"{component}.{op}")
        elif called.outcome.degraded:
            outcome = replace(outcome, degraded=True, reasons=outcome.reasons + called.outcome.reasons)
            outcome = outcome.demote(QUALIFIED, "the oracle's answer was degraded")
    mechanism = acceptance.mechanism_record(
        check=spec.key, judges=spec.verifies, artifact_id=str(produced["artifact_id"]), kind=spec.oracle["kind"],
        op=(component, op), model_free=bool(contract and contract.model_free), executed=verdict.executed,
        # review M2: a check capped for its independence (``independence``) is never recorded as independent
        independent=verdict.independent and not authority_reason, result=verdict.result,
        measured=acceptance.tally(answer(result.reply)) if not result.refused else {}, authority=earned)
    if outcome.degraded:
        mechanism["outcome"] = outcome.as_record()
    if test_provenance or regression:
        mechanism.update(artifact_sha=produced["sha256"], execution_id=uuid.uuid4().hex)
    if isinstance(suite, dict):  # ECHA Stage A: the patch's comparison with the whole suite
        mechanism["suite"] = suite
    if partial:
        mechanism.update({key: partial[key] for key in ("tests", "causes", "not_run", "interruption") if key in partial})
    if receipts is not None and (component, op) == ("workforce", "run_tests") and not regression:
        ran = answer(result.reply).get("tests") if not result.refused else None
        ran = ran if isinstance(ran, dict) else {}
        mechanism["receipts"] = [
            {"test": node, "result": {True: "pass", False: "fail"}.get(ran.get(node), "unknown"),
             "check_sha": str(receipts.get(node.split("::")[0], "")), "confined": confined}
            for node in dict.fromkeys(params.get("tests") or []) if isinstance(node, str)]
    if regression:
        for function in probe.get("functions", []):
            for side in ("baseline", "candidate"):
                if side in function:
                    function[side].update(artifact_id=produced["artifact_id"], artifact_sha=produced["sha256"])
        mechanism["probe"] = probe
    return RungResult(outcome, calls=tuple(_call_record(r) for r in results), model_calls=verdict.model_calls,
                      failure_code=_verify_failure(result, verdict), costs=tuple(r.cost for r in results), verdict=record,
                      mechanisms=(mechanism,))
