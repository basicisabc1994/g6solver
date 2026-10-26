# SPDX-License-Identifier: Apache-2.0
"""Per-run count caps, supplied by the person. File-backed mutations are serialised.

A check reserves one slot before execution. Failed execution still spends that slot.
This bounds calls; it never replaces evaluate_action for an outward act.
"""
from __future__ import annotations

import inspect
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from time import monotonic, sleep
from types import MappingProxyType
from uuid import uuid4

from g6.action_gating.schema import GateDecision, ImpactAssessment
from g6.core.ledger import ChainLedger
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.storage import state_path
from g6.csf import OPERATIONS


class BudgetRefused(RuntimeError):
    pass


def _policy(limits: Mapping[str, int]) -> Mapping[str, int]:
    if not isinstance(limits, Mapping):
        raise ValueError("[G6_E_OPERATION_POLICY_INVALID] limits must be a mapping")
    for operation, limit in limits.items():
        if operation not in OPERATIONS or type(limit) is not int or limit < 0:
            raise ValueError("[G6_E_OPERATION_POLICY_INVALID] known operations need non-negative integer limits")
    return MappingProxyType(dict(limits))


@dataclass(frozen=True)
class BudgetResult:
    gate_decision: GateDecision
    remaining: int
    code: str
    run_id: str
    request_id: str

    @property
    def decision(self) -> str:
        return self.gate_decision.decision

    @property
    def completion_state(self) -> str:
        return self.gate_decision.completion_state

    def to_envelope(self) -> dict:
        decision = self.gate_decision
        return {"decision": self.decision, "remaining": self.remaining, "code": self.code,
                "gate_decision": decision.model_dump(mode="json"),
                **envelope_fields(reliability_label=self.completion_state, warning_card=decision.warning_card,
                                  evidence=decision.evidence, request_id=self.request_id, run_id=self.run_id)}


def _result(run_id: str, operation: str, remaining: int, code: str, allowed: bool, ident: str,
            head: str = "") -> BudgetResult:
    state = "qualified-draft" if allowed else "blocked-escalated"
    message = "Count reservation only; outward acts still require the action gate." if allowed else code
    decision = GateDecision(decision="allow" if allowed else "block", risk=ImpactAssessment(aggregate_risk="unknown", confidence=None,
                                                  rationale=["Count reservation only; no risk assessment."]),
                            reason_codes=[code], required_user_message=message, audit_id=ident, action_id=operation,
                            completion_state=state, warning_card=warning_card(code, message, "Inspect the count policy."),
                            evidence=[{"kind": "operation_budget", "operation": operation, "remaining": remaining,
                                       "ledger_head": head, "reserved": allowed}])
    return BudgetResult(decision, remaining, code, run_id, ident)


@contextmanager
def _exclusive_ledger_lock(path: Path | None) -> Iterator[None]:
    """Serialise cooperating processes; keep the sidecar so waiters share one lock inode."""
    if path is None:
        yield
        return
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as handle:
        locked = False
        try:
            deadline = monotonic() + 5
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if monotonic() >= deadline:
                        raise
                    sleep(0.01)
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class OperationBudget:
    def __init__(self, policy: Mapping[str, int] | None = None, *, ledger: ChainLedger | None = None) -> None:
        self._lock = threading.RLock()
        if ledger is None:
            ledger = ChainLedger(state_path("action_gating", "operations.jsonl"))
        self.ledger = ledger
        self._limits: Mapping[str, int] | None = None
        self._reload()
        if policy is not None:
            self.set_policy(policy, dry_run=False)

    def _reload(self) -> None:
        self.ledger.reload()
        if self.ledger.verify():
            raise ValueError("corrupt ledger")
        limits = None
        for entry in self.ledger.entries():
            if entry["kind"] == "operation_policy":
                limits = _policy(entry["body"]["limits"])
        self._limits = limits

    def set_policy(self, limits: Mapping[str, int], *, dry_run: bool = True) -> dict:
        """Host configuration or the sensitive block op. Never resets counts."""
        proposed = _policy(limits)
        with self._lock:
            if not dry_run:
                try:
                    with _exclusive_ledger_lock(self.ledger.path):
                        self._reload()
                        self.ledger.append("operation_policy", uuid4().hex, {"limits": dict(proposed)})
                        self._limits = proposed
                except Exception as exc:
                    raise BudgetRefused("[G6_E_OPERATION_LEDGER_FAILED] policy was not changed") from exc
            return {"dry_run": dry_run, "applied": not dry_run, "limits": dict(proposed),
                    "completion_state": "qualified-draft"}

    def _used(self, run_id: str, operation: str) -> int:
        return sum(e["kind"] == "operation_check" and e["body"].get("run_id") == run_id
                   and e["body"].get("operation") == operation and e["body"].get("decision") == "allow"
                   for e in self.ledger.entries())

    def check(self, run_id: str, operation: str) -> BudgetResult:
        """Reserve one count and append every decision, including refusals, to the chain."""
        ident = uuid4().hex
        with self._lock:
            remaining, allowed = 0, False
            try:
                with _exclusive_ledger_lock(self.ledger.path):
                    self._reload()
                    valid = isinstance(run_id, str) and bool(run_id.strip()) and isinstance(operation, str)
                    if not valid:
                        code = "G6_E_OPERATION_INPUT_INVALID"
                    elif operation not in OPERATIONS:
                        code = "G6_E_OPERATION_UNKNOWN"
                    elif self._limits is None:
                        code = "G6_E_OPERATION_POLICY_MISSING"
                    elif operation not in self._limits:
                        code = "G6_E_OPERATION_NOT_PERMITTED"
                    else:
                        remaining = max(0, self._limits[operation] - self._used(run_id, operation))
                        allowed = remaining > 0
                        if allowed:
                            remaining -= 1
                        code = "G6_E_OPERATION_RESERVED" if allowed else "G6_E_OPERATION_BUDGET_EXHAUSTED"
                    body = {"run_id": run_id if isinstance(run_id, str) else "",
                            "operation": operation if isinstance(operation, str) else "",
                            "decision": "allow" if allowed else "block", "remaining": remaining, "code": code}
                    head = self.ledger.append("operation_check", ident, body)
            except Exception:
                # An unavailable sink cannot record its own failure; never issue an unlogged reservation.
                return _result(str(run_id), str(operation), remaining, "G6_E_OPERATION_LEDGER_FAILED", False, ident)
            return _result(run_id if isinstance(run_id, str) else "", operation if isinstance(operation, str) else "",
                           remaining, code, allowed, ident, head)

    def record(self, run_id: str, operation: str) -> dict:
        """Record one observation against the oldest unrecorded reservation, without a second charge."""
        with self._lock:
            try:
                with _exclusive_ledger_lock(self.ledger.path):
                    self._reload()
                    entries = self.ledger.entries()
                    recorded = {e["body"].get("reservation") for e in entries if e["kind"] == "operation_record"}
                    pending = [e["id"] for e in entries if e["kind"] == "operation_check"
                               and e["body"].get("run_id") == run_id and e["body"].get("operation") == operation
                               and e["body"].get("decision") == "allow" and e["id"] not in recorded]
                    if not pending:
                        return {"recorded": False, "completion_state": "blocked-escalated",
                                "code": "G6_E_OPERATION_RESERVATION_MISSING"}
                    head = self.ledger.append("operation_record", uuid4().hex,
                                              {"run_id": run_id, "operation": operation, "reservation": pending[0]})
                    return {"recorded": True, "completion_state": "qualified-draft", "ledger_head": head}
            except Exception:
                return {"recorded": False, "completion_state": "blocked-escalated",
                        "code": "G6_E_OPERATION_LEDGER_FAILED"}

    def report(self, run_id: str) -> dict:
        """Read a validated snapshot; never create a directory, ledger or lock file."""
        with self._lock:
            try:
                self._reload()
                if self._limits is None:
                    return {"completion_state": "blocked-escalated", "code": "G6_E_OPERATION_POLICY_MISSING",
                            "remaining": {}, "used": {}}
                used = {op: self._used(run_id, op) for op in self._limits}
                return {"completion_state": "qualified-draft", "run_id": run_id, "used": used,
                        "remaining": {op: max(0, limit - used[op]) for op, limit in self._limits.items()},
                        "ledger_head": self.ledger.head()}
            except Exception:
                return {"completion_state": "blocked-escalated", "code": "G6_E_OPERATION_LEDGER_FAILED",
                        "remaining": {}, "used": {}}

    def guarded(self, operation: str):
        """Trusted Python functions only; run_id must be an explicit argument of the wrapped function."""
        def decorate(fn):
            if (inspect.iscoroutinefunction(fn) or inspect.isgeneratorfunction(fn)
                    or inspect.isasyncgenfunction(fn)):
                raise TypeError("[G6_E_OPERATION_INPUT_INVALID] use a synchronous guarded function")
            signature = inspect.signature(fn)

            @wraps(fn)
            def wrapped(*args, **kwargs):
                try:
                    bound = signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    run_id = bound.arguments.get("run_id")
                    result = self.check(run_id, operation)
                    if not isinstance(result, BudgetResult) or result.decision != "allow":
                        raise BudgetRefused("[G6_E_OPERATION_REFUSED] no count reservation")
                except Exception as exc:
                    raise BudgetRefused("[G6_E_OPERATION_REFUSED] guard check failed or refused") from exc
                value = fn(*args, **kwargs)
                receipt = self.record(run_id, operation)
                if not receipt.get("recorded"):
                    raise BudgetRefused("[G6_E_OPERATION_LEDGER_FAILED] operation ran but its observation was not recorded")
                return value
            return wrapped
        return decorate
