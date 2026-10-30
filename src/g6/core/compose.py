# SPDX-License-Identifier: Apache-2.0
"""Explicit, checked composition. Contracts describe scope; they do not prove correctness.

Inputs name caller fields or a previous stage's output (``caller.task``, ``0.checks``).
Mappings copy values, never execute code or infer semantic equivalence. Python block
objects are trusted host code; generated code must use a confined executor instead.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import inspect
from pathlib import Path
from typing import Any

from g6.core.ai_block import AIBlock
from g6.core.block_contract import contract_to_dict
from g6.core.door import blocked_reply, corrected_result, door_envelope, said_state
from g6.core.ledger import digest
from g6.core.registry import build_typed_input, get_registry
from g6.core.result import Result


class CompositionError(ValueError):
    pass


def _error(code: str, index: int, message: str) -> CompositionError:
    return CompositionError(f"[G6_E_COMPOSITION_{code}] stage {index}: {message}")


@dataclass(frozen=True)
class Stage:
    block: str | AIBlock
    op: str
    inputs: Mapping[str, str] | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


class ComposedBlock(AIBlock):
    def __init__(self, stages: Sequence[Stage], caller_inputs: Sequence[str], *, strict: bool,
                 allow_sensitive: frozenset[str]):
        super().__init__("composition")
        if not stages:
            raise CompositionError("[G6_E_COMPOSITION_EMPTY] at least one stage is required")
        self.strict = strict
        self.caller_inputs = tuple(caller_inputs)
        self.stages = []
        self.manifest = []
        registry = get_registry()
        for index, stage in enumerate(stages):
            if isinstance(stage.block, str):
                module = registry.get_module(stage.block)
                if stage.op in getattr(module, "__g6_sensitive_ops__", ()) and not (
                    stage.block in allow_sensitive or f"{stage.block}.{stage.op}" in allow_sensitive
                ):
                    raise CompositionError(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {stage.block}.{stage.op}")
                cls = registry.get_class(stage.block)
                if cls is None:
                    raise _error("NO_CONTRACT", index, "component has no block")
                block = cls()
            else:
                block = stage.block
            contract = block.contract
            if strict and contract.is_permissive_default():
                raise _error("NO_CONTRACT", index, block.name)
            required = contract.inputs_required_per_op.get(stage.op, contract.inputs_required)
            outputs = contract.outputs_per_op.get(stage.op, contract.outputs)
            links = dict(stage.inputs or ({key: f"caller.{key}" for key in caller_inputs} if index == 0 else {}))
            params = deepcopy(dict(stage.params))
            if "op" in params or "op" in links or set(params) & set(links):
                raise _error("INVALID_MAPPING", index, "op is fixed and inputs cannot overlap constants")
            for key in required:
                if key not in links and key not in params:
                    raise _error("UNMET_INPUT", index, key)
            for key, source in links.items():
                owner, sep, name = source.partition(".")
                if not sep:
                    raise _error("UNMET_INPUT", index, source)
                if owner == "caller":
                    available = caller_inputs
                    semantic = None  # caller-supplied evidence is not independently established
                elif owner.isdecimal() and int(owner) < index:
                    previous = self.stages[int(owner)]
                    available = previous[3]
                    semantic = previous[1].contract.evidence_outputs_per_op.get(previous[0].op, {}).get(name)
                else:
                    raise _error("UNMET_INPUT", index, source)
                if name not in available:
                    raise _error("UNMET_INPUT", index, source)
                expected = contract.evidence_inputs_per_op.get(stage.op, {}).get(key)
                if expected and semantic != expected:
                    raise _error("EVIDENCE_MISMATCH", index, f"{key} needs {expected}, got {semantic or 'undeclared'}")
            for key in contract.evidence_inputs_per_op.get(stage.op, {}):
                if key not in links:
                    raise _error("EVIDENCE_MISMATCH", index, f"{key} needs an explicit evidence source")
            self.stages.append((stage, block, links, outputs, params))
            self.manifest.append({"component": block.name, "operation": stage.op,
                                  "implementation": f"{type(block).__module__}.{type(block).__qualname__}",
                                  "implementation_sha256": _implementation_digest(block),
                                  "contract": deepcopy(contract_to_dict(contract)), "inputs": links, "params": params})

    def infer(self, data: dict) -> Result[dict]:
        values, checks, evidence = [], [], []
        state = "verified"
        rank = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}
        for index, (stage, block, links, outputs, params) in enumerate(self.stages):
            try:
                if contract_to_dict(block.contract) != self.manifest[index]["contract"]:
                    raise _error("CONTRACT_CHANGED", index, block.name)
                raw = deepcopy(params)
                for key, source in links.items():
                    owner, name = source.split(".", 1)
                    raw[key] = deepcopy(data[name] if owner == "caller" else values[int(owner)][name])
                raw["op"] = stage.op
                context = {"context": raw, "task": raw.get("task", ""), "constraints": {},
                           "job_name": block.name, "domain": block.contract.domain}
                if self.strict and any(not check(context) for check in block.contract.preconditions):
                    raise _error("PRECONDITION_FAILED", index, block.name)
                screen = getattr(block, "screen_raw", None)
                outcome = screen(raw) if callable(screen) else None
                if outcome is None:
                    typed, error = build_typed_input(block, raw, code="[G6_E_INPUT_VALIDATION]", op=stage.op)
                    if error:
                        raise CompositionError(error)
                    outcome = block.infer(typed)
                if not isinstance(outcome, Result) or outcome.is_fail():
                    raise CompositionError(outcome.error if isinstance(outcome, Result) else "invalid block result")
                value = outcome.value
                if not isinstance(value, Mapping):
                    raise _error("OUTPUT_MISSING", index, "an object output is required")
                if self.strict:
                    for key in outputs:
                        if key not in value:
                            raise _error("OUTPUT_MISSING", index, key)
                    if any(check(value) for check in block.contract.stop_conditions):
                        raise _error("STOP_CONDITION", index, block.name)
                envelope = door_envelope(said_state(value), who=block.name, producer=type(block), value=value, caller=raw)
                state = max((state, envelope["completion_state"]), key=rank.__getitem__)
                value = corrected_result(value, state)
                # Producer assertions are kept as assertions, never relabelled as executed evidence.
                claimed = value.get("evidence", [])
                records = [{"stage": index, "authority": "producer-declared", "record": deepcopy(item)}
                           for item in claimed] if isinstance(claimed, list) else []
                evidence.extend(records)
                # Preserve the component's schema for explicit downstream adapters. Assertion
                # authority lives in the separate report and cannot promote completion state.
                values.append(deepcopy(value))
                checks.append({"stage": index, "typed_input": True, "outputs_checked": self.strict,
                               "completion_state": state})
                if state == "blocked-escalated":
                    return Result.ok(self._reply(values, checks, evidence, state, envelope.get("warning_card")))
            except Exception as exc:  # a broken check or adapter cannot let later stages execute
                refused = blocked_reply(str(exc))
                return Result.ok({**refused, **self._reply(values, checks, evidence, "blocked-escalated",
                                                          refused["warning_card"])})
        return Result.ok(self._reply(values, checks, evidence, state))

    def _reply(self, values, checks, evidence, state, warning=None):
        report = {"status": "contract-checked" if self.strict and len(checks) == len(self.stages) else "unvalidated", "stages": deepcopy(self.manifest),
                  "checks": checks, "evidence": evidence,
                  "not_checked": ["application benefit", "complete behavioural correctness",
                                  "producer-declared evidence authenticity"],
                  "integration_tests": [], "application_validation": []}
        report["digest"] = digest(report)
        return {"ok": state != "blocked-escalated", "result": values[-1] if values else None,
                "completion_state": state, "warning_card": warning, "evidence": evidence, "assurance": report}


def compose(*stages: Stage, caller_inputs: Sequence[str], strict: bool = True,
            allow_sensitive: frozenset[str] = frozenset()) -> ComposedBlock:
    return ComposedBlock(stages, caller_inputs, strict=strict, allow_sensitive=allow_sensitive)


def _implementation_digest(block: AIBlock) -> str | None:
    """Bind the defining module, not all transitive dependencies or runtime object state."""
    try:
        return hashlib.sha256(Path(inspect.getfile(type(block))).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None
