# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The one door a worker calls a component through (workforce OS plan, P0 T5; Phase E Part 1).

This is the only workforce module that may reach a capability. In the library the only capabilities are the
workforce's own bench ops (``workforce.run_tests``, ``workforce.run_tests_in_place``), reached in process through a
static table (``_registry_invoke``); there is no registry door, no hosted transport and no charged (billed) mode.
Before anything is invoked the door refuses, as a blocked-escalated outcome naming which refusal it was (``REFUSALS``: each is a failure
code ``classes.py`` maps to a Ledger §26 class):

* any call while the process-wide stop (``g6.core.stop``, audit 007 SE-010) or the operator's stop is engaged
  (``refused:killed``); a contained call already running is interrupted at its next poll;
* a pair outside the worker's allowlist, or one no ParamContract declares;
* parameters the contract does not allow (unknown, missing or mistyped keys, ``op`` included);
* a writing op for a read-only worker (except an op whose contract declares a no-effect probe, ``shadow_probe``,
  called with exactly those probe parameters: V3a);
* with ``charged`` (a strict budget the caller asks for; the library never derives it from a hosted owner), a call
  whose cost it cannot bound: its contract declares no ``cost_ceiling``, or one in another currency than the budget's;
* anything the safety engine's ``PolicyEngine`` will not allow (kill switch, ceilings, allow-list); this door is
  the code that obeys its decision (no token is minted). The cost ceiling is one of them (E1c): a call's declared
  ceiling, added to what the unit already spent (``spent_cents``, its earlier attempts included), may not pass
  the worker's ``max_cost_cents``.

What comes back is read only through ``Outcome.from_envelope``. Every call that is sent gets one
``CostEntry`` (``economics.call_cost``, Ledger §5) with the model calls ITS OWN context noted
(``core.model_calls.counting``), so on a shared server one tenant is never charged for another's calls; it is handed
to ``record`` (the runtime writes it as the attempt's cost row) the moment the call returns, before anything that
could fail later (a store error there is raised: a sent call is never left without its row unnoticed);
``model_calls`` keeps the process-wide delta, the conservative count the verified-verdict proof relies on (plan
ruling R7). Outbound connections are not measured (the process-wide socket hook was removed by ruling, W-B second
review 1), so none is claimed: what an op may touch is its contract's declared ``side_effect``, the sensitivity
denylist and the read-only check. An exception is a blocked outcome naming its kind (``RAISED``), never a raise.
``Door.reply`` is the same door shaped for a capability probe (``Capabilities.probe``): a refusal comes back as a
blocked envelope. A probe keeps the per-call allowance its contract declares. Runtime calls reserve durably before
invocation.
"""
from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from g6.core import component_process, stop
from g6.core.model_calls import counting, model_calls
from g6.safety_engine.ceilings import COST_CEILING, RUNTIME_CEILING
from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, AuthorizationDecision, RuntimePolicy
from g6.safety_engine.state import RunState
from g6.workforce import bench, classes, economics
from g6.workforce.capabilities import Capabilities, ParamContract
from g6.workforce.contracts import BLOCKED, Outcome
from g6.workforce.economics import CostEntry, Money
from g6.workforce.plans import UnitSpec
from g6.workforce.workers import WorkerSpec, form_worker

#: The ceiling a capability probe runs under: one read-only call, no cost, no retries.
PROBE_CEILING = RuntimePolicy(max_steps=1, max_tool_calls=1, max_shell_commands=0, max_files_modified=0,
                              max_lines_changed=0, max_retries=0, max_runtime_seconds=60, max_cost_cents=0,
                              max_privilege_level=0)
_REASON_CHARS = 300
#: Every refusal the door makes, as the failure code a run records (``classes.WORKFORCE_CODES`` maps each).
REFUSALS = ("refused:allowlist", "refused:undeclared", "refused:params", "refused:sensitive", "refused:read_only",
            "refused:killed", "refused:ceiling", "refused:clock", "refused:policy", "refused:permit",
            "refused:budget", "refused:unpriced")
#: How a call that raised is named, by what it raised (anything else: ``raised``).
RAISED = (("timeout", TimeoutError), ("unreachable", ConnectionError))
#: A timeout means the answer did not come back, not that nothing happened. For an op that does more than read, the
#: action may have been done, so it is named apart (``timeout_unconfirmed``) and never sent again by the runtime (integrated
#: audit AT16, 2026-10-11).
UNCONFIRMED = "timeout_unconfirmed"
#: What a write can raise after the request left (independent review F5, 2026-10-11): a timeout, or the connection cut
#: mid-way. A refused connection never reached the other side, so it is not here and is still tried again.
SENT_THEN_LOST = (TimeoutError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


@dataclass(frozen=True)
class CallResult:
    component: str
    op: str
    outcome: Outcome
    reply: Any = None
    model_calls: int = 0
    refused: str = ""
    #: Which refusal (one of ``REFUSALS``) when ``refused``.
    refusal_code: str = ""
    #: What the call raised: ``timeout``, ``unreachable`` or ``raised`` ("" when it answered).
    raised: str = ""
    #: The model calls this call's own context noted: what is charged to it (``model_calls`` is process-wide).
    charged_model_calls: int = 0
    #: The digest of the ParamContract the call was checked against (E2b: a saved result names it).
    contract_sha: str = ""
    #: What the call cost (None: refused before it was sent, so nothing was spent).
    cost: CostEntry | None = None


def _registry_invoke(component: str, op: str, params: dict, *, venture: str | None = None) -> Any:
    """The library's capability table: the workforce bench's ops (with no venture bound, only their probes run).
    Anything else is unreachable here (there is no component registry door in the library)."""
    if component == bench.COMPONENT and op in bench.OPS:
        return bench.run(op, params, venture=venture)
    raise ConnectionError(f"{component}.{op} is not a capability of this library")


def venture_invoke(venture_id: str, *,
                   invoke: Callable[[str, str, dict], Any] | None = None) -> Callable[[str, str, dict], Any]:
    """The door's invoke for one venture's mission: the bench reaches that venture's repositories only. ``invoke`` (a
    test's scripted world) answers everything in its place."""

    def call(component: str, op: str, params: dict) -> Any:
        if invoke is not None:
            return invoke(component, op, params)
        return _registry_invoke(component, op, params, venture=venture_id)

    return call


def _engine_refusal(decision: AuthorizationDecision) -> str:
    """Which refusal a policy-engine decision was: its cost axis is ``refused:budget`` (E1c)."""
    last = decision.policy_checks[-1] if decision.policy_checks else ""
    if last == "kill_switch":
        return "refused:killed"
    if last == "ceiling":
        if COST_CEILING in decision.reason:
            return "refused:budget"
        return "refused:clock" if RUNTIME_CEILING in decision.reason else "refused:ceiling"
    return "refused:policy"


class Door:
    """One worker's door for one attempt: its allowlist, its contracts, its policy, its run state. ``charged``: a strict
    budget, so a call whose cost cannot be bounded is refused; ``spent_cents``: what the unit's earlier attempts already
    spent against the same budget, known; ``spent_unknown``: whether one of them spent an amount nobody measured, which
    under a strict budget uses up the rest of it; ``record(index, cost)``: called with each sent call's cost the moment it returns (``index`` numbers this door's
    calls in the order they are made, as the rung records them). ``reserve(index, contract)``: commit the hold before
    invoking, returning a budget refusal reason or an empty string. A storage or lease failure propagates unsent."""

    def __init__(self, worker: WorkerSpec, *, contracts: Callable[[str, str], ParamContract | None], run_id: str,
                 invoke: Callable[[str, str, dict], Any] | None = None, charged: bool = False,
                 spent_cents: int = 0, spent_unknown: bool = False,
                 record: Callable[[int, CostEntry], None] | None = None,
                 reserve: Callable[[int, ParamContract], str] | None = None,
                 stop_requested: Callable[[], bool] | None = None) -> None:
        self.worker = worker
        self._contracts = contracts
        self._invoke = invoke or _registry_invoke
        self._charged = bool(charged)
        self._record = record
        self._reserve = reserve
        self._stop_requested = stop_requested
        self._calls = 0
        self._engine = PolicyEngine()
        #: An unknown earlier cost under a strict budget: it may have been all that was left, so it counts as that.
        self._unknown = self._charged and bool(spent_unknown)
        spent = max(0, int(spent_cents))
        if self._unknown:
            spent = max(spent, int(worker.policy.max_cost_cents))
        self._state = RunState(policy=worker.policy, run_id=run_id, cost_cents=spent)

    def call(self, component: str, op: str, params: Any) -> CallResult:
        source = f"{component}.{op}"
        index, self._calls = self._calls, self._calls + 1
        stopped = stop.engaged()
        if stopped is not None:
            return self._refused(component, op, "refused:killed", f"G6 is stopped: {stopped}")
        if self._stop_requested is not None and self._stop_requested():
            return self._refused(component, op, "refused:killed", "the operator requested stop")
        code, why = self._refusal(component, op, params)
        if code:
            return self._refused(component, op, code, why)
        params = dict(params)
        contract = self._contracts(component, op)
        ceiling = contract.cost_ceiling
        priced = ceiling is not None and ceiling.currency == economics.BUDGET_CURRENCY
        if self._charged and not priced:
            return self._refused(component, op, "refused:unpriced", self._unpriced(source, ceiling))
        action = ActionRequest(tool_name=source, arguments=params, privilege_level=0,
                               cost_ceiling_cents=ceiling.amount_minor_units if priced else 0)
        decision = self._engine.authorize_action(action, self._state, self.worker.policy)
        if decision.decision != "ALLOW":
            code = _engine_refusal(decision)
            why = (self._over_budget(source, ceiling) if code == "refused:budget"
                   else f"the policy engine said {decision.decision}: {decision.reason}")
            return self._refused(component, op, code, why)
        if self._reserve is not None:
            why = self._reserve(index, contract)
            if why:
                return self._refused(component, op, "refused:budget", why)
        calls_before = model_calls()
        error = raised = ""
        remaining = self.worker.policy.max_runtime_seconds - self._state.elapsed_seconds()
        with counting() as own_calls, component_process.window(remaining, self._halt) as window:
            try:
                reply = self._invoke(component, op, params)
            except Exception as exc:  # noqa: BLE001 - a failed call is a blocked outcome, never a raise
                reply, error = None, f"the call raised {type(exc).__name__}: {exc}"
                raised = next((name for name, kind in RAISED if isinstance(exc, kind)), "raised")
                if (contract.side_effect != "read" and isinstance(exc, SENT_THEN_LOST)
                        or isinstance(exc, component_process.ContainmentFailure)):
                    raised = UNCONFIRMED
            finally:
                self._state.record_execution(action)
            charged = own_calls()
        called = model_calls() - calls_before
        cost = economics.call_cost(ceiling, model_free=contract.model_free, component=component, op=op,
                                   charged_model_calls=charged, process_model_calls=called)
        if window.execution:
            cost = dataclasses.replace(cost, metadata={**cost.metadata, "execution": window.execution})
            if window.execution.get("cleanup_confirmed") is False:
                cost = dataclasses.replace(cost, source="abandoned", amount_minor_units=None, currency=None, estimated=False,
                                           method="The process cleanup is unconfirmed, so its final cost is unknown")
        if self._record is not None:  # the call was sent: its cost row first, before anything that can fail
            self._record(index, cost)
        if cost.amount_minor_units is not None and cost.currency == economics.BUDGET_CURRENCY:
            self._state.record_cost(cost.amount_minor_units)
        if error:
            outcome = Outcome(BLOCKED, reasons=(error[:_REASON_CHARS],), source=source)
        else:
            outcome = Outcome.from_envelope(reply, source=source)
        return CallResult(component, op, outcome, reply, called, raised=raised, charged_model_calls=charged,
                          contract_sha=contract.sha256, cost=cost)

    def _halt(self) -> bool:
        """What a contained call polls while it runs: the process-wide stop, or the operator's."""
        return stop.stopped() or bool(self._stop_requested is not None and self._stop_requested())

    def reply(self, component: str, op: str, params: Any) -> Any:
        """The call shaped for a capability probe: the reply, or a blocked envelope saying why not, with the stable
        ``G6_E_*`` code of what stopped it (a refusal, or a call that raised; ``classes.surface_code``)."""
        result = self.call(component, op, params)
        if result.refused or result.reply is None:
            out = {"ok": False, "completion_state": BLOCKED, "error": "; ".join(result.outcome.reasons)}
            stopped = result.refusal_code or (f"call_{result.raised}" if result.raised else "")
            if stopped:
                out["code"] = classes.surface_code(stopped)
            return out
        return result.reply

    def _refusal(self, component: str, op: str, params: Any) -> tuple[str, str]:
        """(the refusal code, why) for a call the door will not send, or ("", "")."""
        source = f"{component}.{op}"
        if (component, op) not in self.worker.allowed:
            return "refused:allowlist", f"{source} is outside this worker's allowlist"
        contract = self._contracts(component, op)
        if contract is None:
            return "refused:undeclared", f"no ParamContract declares {source}"
        problems = contract.violations(params)
        if problems:
            return "refused:params", "the parameters break the contract: " + "; ".join(problems)
        if self.worker.read_only and contract.side_effect != "read" \
                and not (contract.shadow_probe and params == contract.probe_params):
            return "refused:read_only", f"a read-only worker cannot send a {contract.side_effect} op"
        return "", ""

    def _over_budget(self, source: str, ceiling: Money) -> str:
        """Why a call was refused for its budget (what failed and why; ``classes.explain`` adds the rest)."""
        limit = self.worker.policy.max_cost_cents
        left = max(0, limit - self._state.cost_cents)
        currency = economics.BUDGET_CURRENCY
        why = (f"one call to {source} may cost up to {economics.format_money(ceiling.amount_minor_units, currency)}, "
               f"and this step has {economics.format_money(left, currency)} left of its "
               f"{economics.format_money(limit, currency)} budget")
        if self._unknown:
            why += (": an earlier attempt stopped before G6 could see what it spent, and under a strict budget a cost "
                    "that is not known counts as all that was left")
        return why

    @staticmethod
    def _unpriced(source: str, ceiling: Money | None) -> str:
        if ceiling is None:
            return (f"{source} has no declared cost, and under a strict budget G6 runs only a step whose cost can be "
                    "shown to fit its budget")
        return (f"{source} declares its cost in {ceiling.currency} and the budget is in "
                f"{economics.BUDGET_CURRENCY}, and G6 has no exchange rate to compare them")

    @staticmethod
    def _refused(component: str, op: str, code: str, why: str) -> CallResult:
        why = why[:_REASON_CHARS]
        return CallResult(component, op, Outcome(BLOCKED, reasons=(f"refused at the door: {why}",),
                                                  source=f"{component}.{op}"), refused=why, refusal_code=code)


class _Prober:
    def __init__(self, capabilities: Capabilities, invoke: Callable[[str, str, dict], Any] | None,
                 stop_requested: Callable[[], bool] | None = None) -> None:
        self._caps, self._invoke = capabilities, invoke
        self._stop_requested = stop_requested

    def reply(self, component: str, op: str, params: Any) -> Any:
        contract = self._caps.contract(component, op)
        declared = contract.cost_ceiling if contract is not None else None
        allowance = (declared.amount_minor_units
                     if declared is not None and declared.currency == economics.BUDGET_CURRENCY else 0)
        ceiling = dataclasses.replace(PROBE_CEILING, max_cost_cents=allowance)
        probe = UnitSpec(key="probe", kind="do", goal=f"probe {component}.{op}", capabilities=((component, op),),
                         budget_cents=allowance)
        try:
            worker = form_worker("prober", ceiling=ceiling, contracts=self._caps.contract, unit=probe,
                                 allowed=((component, op),))
        except ValueError as exc:
            return {"ok": False, "completion_state": BLOCKED, "error": f"no prober could be formed: {exc}"}
        door = Door(worker, contracts=self._caps.contract, run_id=f"probe:{uuid.uuid4().hex[:12]}",
                    invoke=self._invoke, stop_requested=self._stop_requested)
        return door.reply(component, op, params)


def prober(capabilities: Capabilities, *, invoke: Callable[[str, str, dict], Any] | None = None,
           stop_requested: Callable[[], bool] | None = None) -> _Prober:
    """What ``Capabilities.probe`` calls: a one-call, read-only worker formed per probed pair."""
    return _Prober(capabilities, invoke, stop_requested)
