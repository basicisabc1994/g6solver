# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""
AIBlock — mvp.core.ai_block

Generic composable unit of AI computation.
Every component in the system is (or wraps) an AIBlock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from g6.core.block_contract import BlockContract, permissive_default
from g6.core.evidence import Assumption, Claim, EvidenceBundle, TestResult
from g6.core.result import Result
from g6.core.maturity import component_maturity
from g6.core.contract_decorator import block_contract
from g6.core.block_contract import FailureMode

Input = TypeVar("Input")
Output = TypeVar("Output")
State = TypeVar("State")


@dataclass
class AIBlock(Generic[Input, Output, State]):
    """
    The atomic building block of the G6 hyperdistillation system.

    Type parameters
    ---------------
    Input  : the data type flowing *into* this block
    Output : the data type flowing *out of* this block
    State  : the type of the block's internal mutable state
    """

    name: str
    state: State | None = field(default=None)

    # ------------------------------------------------------------------
    # Core interface — override in subclasses
    # ------------------------------------------------------------------

    def infer(self, data: Input) -> Result[Output]:
        """Single-shot computation: transform Input → Result[Output]."""
        return Result.fail(f"{self.name}.infer() not implemented")

    def verify(self, output: Output, contract: BlockContract | None = None) -> EvidenceBundle:
        """Verify output using a registered per-harness verification method.

        Phase 03 populates ``mvp.core.verification``. Until then this returns a
        conservative fallback bundle, giving callers a stable type without
        claiming a full verification pass happened.
        """
        active_contract = contract or self.contract
        try:
            from g6.core import verification
            if not verification.is_empty():
                verifier = verification.get(active_contract.verification_method)
                return verifier(output=output, contract=active_contract, block=self)
        except Exception as exc:  # noqa: BLE001
            return EvidenceBundle(
                claims=(Claim(text="verification dispatch failed", checked=True),),
                tests_run=(
                    TestResult(
                        name=active_contract.verification_method,
                        passed=False,
                        output_ref=str(exc),
                    ),
                ),
                confidence=0.0,
                verifier_id="ai_block.verify",
                unresolved_questions=("verification registry dispatch failed",),
            )
        return EvidenceBundle(
            claims=(Claim(text="orchestrator-side fallback verification pending", checked=False),),
            assumptions=(
                Assumption(
                    text="phase 03 verification registry is not populated",
                    risk="medium",
                    validation="run phase 03 verification dispatch tests",
                ),
            ),
            confidence=None,
            verifier_id="permissive_default",
            unresolved_questions=("no named verification method registered yet",),
        )

    @property
    def bias(self) -> dict[str, "Any"]:
        """Structural assumptions about this block's hypothesis space.

        Override in subclasses for richer declarations with evolvable
        parameters. The default returns a minimal descriptor.
        """
        return {
            "name": self.name,
            "type": type(self).__name__,
            "strategy": "default",
            "assumptions": [],
            "evolvable_params": {},
            "version": 0,
        }

    @property
    def contract(self) -> BlockContract:
        """Runtime obligations this block promises to satisfy.

        Subclasses should declare via the ``@block_contract`` decorator
        from :mod:`mvp.core.contract_decorator`; the decorator stores a
        payload on the class which is materialised here. Unmigrated blocks
        receive a permissive default that the reliability orchestrator
        treats the same as having no contract.
        """
        attr = getattr(type(self), "__g6_block_contract__", None)
        if isinstance(attr, BlockContract):
            return attr
        if isinstance(attr, dict):
            payload = dict(attr)
            payload.setdefault("name", self.name)
            try:
                return BlockContract(**payload)
            except TypeError:
                return permissive_default(self.name)
        return permissive_default(self.name)

    # ------------------------------------------------------------------
    # Composition helpers
    # ------------------------------------------------------------------

    def then(self, other: "AIBlock[Output, Any, Any]") -> "PipelineBlock[Input, Any, None]":
        """Chain self >> other into a sequential pipeline."""
        return PipelineBlock(name=f"{self.name}>>{other.name}", _left=self, _right=other)

    def __rshift__(self, other: "AIBlock[Output, Any, Any]") -> "PipelineBlock[Input, Any, None]":
        return self.then(other)

    def __repr__(self) -> str:
        return f"<AIBlock name={self.name!r}>"


@block_contract(
    purpose=(
        "Foundation layer: the generic AIBlock base, railway-oriented Result, "
        "guardrailed SearchTree, and resource-bounding primitives that every other "
        "G6 component builds on."
    ),
    good_at=(
        "defining custom reasoning blocks by subclassing AIBlock",
        "building typed, composable pipelines with PipelineBlock and >> chaining",
        "enforcing token, time, and disk resource bounds on a pipeline",
    ),
    bad_at=(
        "domain reasoning or task-specific logic — core is infrastructure, not a solver",
        "standing in for end-to-end product readiness (passing core tests is not full launch readiness)",
    ),
    inputs_required=("data",),
    outputs=("pipeline_result", "evidence", "completion_state", "warning_card"),
    verification_method="tier1_review_pending",
    known_failure_modes=(
        FailureMode("review_pending", detector="operator_review", severity="medium"),
    ),
)
@component_maturity("beta", reason="implemented; awaiting GDPval calibration")
class PipelineBlock(AIBlock[Input, Output, None]):
    """Two AIBlocks chained sequentially: left.emit() → right.ingest()."""

    _REGISTRY_DISCOVERABLE = False

    def __init__(
        self,
        name: str,
        _left: AIBlock[Any, Any, Any],
        _right: AIBlock[Any, Any, Any],
    ) -> None:
        super().__init__(name=name)
        self._left = _left
        self._right = _right

    def infer(self, data: Any) -> Result[Any]:  # noqa: ANN401
        left_result = self._left.infer(data)
        evidence = _stage_evidence("first", left_result)
        warning = _stage_warning("first", left_result)
        if left_result.is_fail():
            return _annotate_result(
                left_result,
                evidence=evidence,
                warning_card=warning,
                completion_state="blocked-escalated",
                failed_stage="first",
            )
        right_result = self._right.infer(left_result.value)
        evidence.extend(_stage_evidence("second", right_result))
        warning = _stage_warning("second", right_result) or warning
        if right_result.is_fail():
            return _annotate_result(
                right_result,
                evidence=evidence,
                warning_card=warning,
                completion_state="blocked-escalated",
                failed_stage="second",
            )
        # CORE_LABELS-01: library blocks report their state in the value dict, so read it there too.
        claimed = _worst_completion_state(_stage_state(left_result), _stage_state(right_result))
        # Plan A1: a stage's own verified stands only when G6's re-check at the door reproduces it, compared against
        # the pipeline caller's own input, never a stage's output.
        caller = data if isinstance(data, dict) else None
        completion_state = _worst_completion_state(
            _door_state(self._left, left_result, caller),
            _door_state(self._right, right_result, caller),
        )
        from g6.core.door import corrected_result, door_envelope

        if claimed == "verified" and completion_state != "verified":
            warning = door_envelope("verified", who=f"the pipeline {self.name}")["warning_card"]
            setattr(right_result, "claimed_completion_state", claimed)
        if isinstance(right_result.value, dict):
            # The value dict says what the result attribute says (CORE_LABELS-01): no stage's own word outranks it.
            right_result._value = corrected_result(right_result.value, completion_state)
        return _annotate_result(
            right_result,
            evidence=evidence,
            warning_card=warning,
            completion_state=completion_state,
        )


def _stage_evidence(stage: str, result: Result[Any]) -> list[dict[str, Any]]:
    items: list[Any] = []
    items.extend(getattr(result, "evidence", []) or [])
    if result.is_ok() and isinstance(result.value, dict):
        items.extend(result.value.get("evidence") or [])
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append({"stage": stage, **item})
        else:
            out.append({"stage": stage, "summary": str(item)})
    return out


def _stage_warning(stage: str, result: Result[Any]) -> dict[str, Any] | None:
    warning = getattr(result, "warning_card", None)
    if warning is None and result.is_ok() and isinstance(result.value, dict):
        warning = result.value.get("warning_card")
    if not isinstance(warning, dict):
        return None
    return {"stage": stage, **warning}


def _annotate_result(
    result: Result[Any],
    *,
    evidence: list[dict[str, Any]],
    warning_card: dict[str, Any] | None,
    completion_state: str,
    failed_stage: str | None = None,
) -> Result[Any]:
    setattr(result, "evidence", evidence)
    setattr(result, "completion_state", completion_state or "qualified-draft")
    setattr(result, "assurance", {"status": "unvalidated", "connection": "plain >>",
                                  "note": "Completion states are enforced; contracts and application benefit are not checked."})
    if warning_card is not None:
        if failed_stage:
            warning_card = {"failed_stage": failed_stage, **warning_card}
        setattr(result, "warning_card", warning_card)
    if failed_stage:
        setattr(result, "failed_stage", failed_stage)
    return result


_STATE_RANK = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}


def _stage_state(result: Result[Any]) -> str:
    """The weakest state one stage reports, on the result or in its value dict.

    A stage that reports no state, or a word outside the three, is a ``qualified-draft``: saying nothing never
    earns ``verified``.
    """
    said: list[Any] = [getattr(result, "completion_state", None)]
    if result.is_ok() and isinstance(result.value, dict):
        said.append(result.value.get("completion_state"))
        said.append(result.value.get("reliability_label"))
    return _worst_completion_state(*[state if isinstance(state, str) else "qualified-draft" for state in said if state])


def _door_state(block: AIBlock[Any, Any, Any], result: Result[Any], caller: dict[str, Any] | None = None) -> str:
    """The state a stage's result keeps at the door: a stage's own ``verified`` stands only when G6's re-checker for
    the code of the stage's block (its class, by where it lives) re-runs the check its value names and reproduces it.
    A nested pipeline was already through the door, so its state is kept."""
    said = _stage_state(result)
    if said != "verified" or type(block) is PipelineBlock:   # a subclass that overrides infer is a stage like any
        return said
    from g6.core.door import door_envelope

    value = result.value if result.is_ok() else None
    return door_envelope(said, producer=type(block), value=value, caller=caller)["completion_state"]


def _worst_completion_state(*states: str) -> str:
    cleaned = [state if state in _STATE_RANK else "qualified-draft" for state in states if state]
    if not cleaned:
        return "qualified-draft"
    return max(cleaned, key=lambda state: _STATE_RANK[state])
