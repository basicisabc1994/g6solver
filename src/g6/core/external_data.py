# SPDX-License-Identifier: Apache-2.0
"""External text ingress and private snapshot receipts.

The boundary registry is component results/errors, caller echoes, quoted source
content, and executed-program output. Snapshot restoration confers no new trust:
it only restores exact bytes observed with a receipt before serialization.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import wraps
import json
from typing import get_args, get_type_hints

from g6.core.agent_guidance import (
    _ExternalText, _GuidanceText, _MixedText, _MAX_GUIDANCE_DEPTH, _structured, guidance_role, is_external,
    reply_for_agent, child_guidance_positions, guidance_positions, ENVELOPE_CONTRACT,
)


class ExternalDataLimit(ValueError):
    """The host could not safely copy external text receipts."""


def _model_values(item, depth=0):
    """Serialize model values without assigning any semantic role to their type."""
    from pydantic import BaseModel

    if depth > _MAX_GUIDANCE_DEPTH:
        raise ExternalDataLimit("[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person.")
    if isinstance(item, BaseModel):
        item = {name: getattr(item, name) for name in
                (*type(item).model_fields, *type(item).model_computed_fields)}
    if isinstance(item, Mapping):
        return {key: _model_values(child, depth + 1) for key, child in item.items()}
    if isinstance(item, (list, tuple)):
        children = [_model_values(child, depth + 1) for child in item]
        return tuple(children) if isinstance(item, tuple) else children
    return item


def external_value(value, *, _component=False, _schema=None):
    """Copy external JSON-like data, marking text without inspecting its words."""
    results, active = {}, set()
    root_positions = guidance_positions(value, schema=_schema)
    todo = [(value, False, 0, False, True, root_positions)]
    while todo:
        item, opaque, guidance, done, at_root, pending = todo.pop()
        positions = pending
        ident = (id(item), opaque, guidance, at_root, pending)
        if isinstance(item, str):
            results[ident] = (_GuidanceText(str.__str__(item))
                              if guidance or isinstance(item, _GuidanceText) else _ExternalText(item))
        elif not isinstance(item, (Mapping, list, tuple)):
            results[ident] = item
        elif done:
            results[ident] = ({key: results[(id(child), _component and (opaque or _structured(key)),
                                           guidance_role(key, guidance, opaque, positions=positions)
                                           if _component else 0, False, child_guidance_positions(positions, key))]
                              for key, child in item.items()}
                             if isinstance(item, Mapping) else
                             [results[(id(child), opaque, guidance, False,
                                       child_guidance_positions(pending, "*"))] for child in item])
            if isinstance(item, tuple):
                results[ident] = tuple(results[ident])
            active.remove(ident)
        elif ident in active:
            raise ExternalDataLimit("[G6_E_RESOURCE_LIMIT] cyclic external data")
        elif ident not in results:
            active.add(ident)
            todo.append((item, opaque, guidance, True, at_root, pending))
            if isinstance(item, Mapping):
                todo.extend((child, _component and (opaque or _structured(key)),
                             guidance_role(key, guidance, opaque, positions=positions)
                             if _component else 0, False, False, child_guidance_positions(positions, key))
                            for key, child in item.items())
            else:
                todo.extend((child, opaque, guidance, False, False,
                             child_guidance_positions(pending, "*")) for child in item)
    return results[(id(value), False, 0, True, root_positions)]


def component_value(value, *, in_result=False, schema=None):
    """Classify roles at their reply position before granting data receipts.

    A component payload is data unless its producer supplied a result contract.
    Reply roots declare their own envelope; no nested object is recognised by
    its keys. Private guidance receipts survive subsequent component wrappers.
    """
    contract = schema if in_result or schema is not None else ENVELOPE_CONTRACT
    try:
        value = _model_values(value)
    except ExternalDataLimit as exc:
        return {"ok": False, "completion_state": "blocked-escalated", "error": str(exc)}
    return reply_for_agent(external_value(value, _component=True, _schema=contract), _schema=contract)


def producer_result_contract(fn):
    """The producing route declares its return; runtime payloads are never read.

    These legacy dictionary routes flatten G6's envelope beside their data.
    Typed producers declare Result[T] in their annotation. Extensions can name
    a serialized return model with result_contract on the producer or owner.
    """
    owner = getattr(fn, "__self__", None)
    explicit = getattr(fn, "result_contract", getattr(owner, "result_contract", None))
    if explicit is not None:
        return explicit
    from g6.goal_engine.block import GoalEngineBlock
    from g6.coding_agent_gate.coding_agent_gate_block import CodingAgentGateBlock
    from g6.action_gating.action_gating_block import ActionGatingBlock
    from g6.algorithm_design.block import AlgorithmDesignBlock
    from g6.complexity.block import ComplexityBlock
    from g6.self_maintenance.block import SelfMaintenanceBlock
    from g6.knowledge.ctx_elastic.block import CtxElasticBlock
    from g6.knowledge.knowledge_fetcher.block import KnowledgeFetcherBlock
    from g6.knowledge._index import Searched
    from g6.knowledge.knowledge_fetcher.fetcher import FetchResult
    contracts = {GoalEngineBlock: ENVELOPE_CONTRACT,
                 CodingAgentGateBlock: ENVELOPE_CONTRACT,
                 ActionGatingBlock: ENVELOPE_CONTRACT,
                 AlgorithmDesignBlock: ENVELOPE_CONTRACT,
                 ComplexityBlock: ENVELOPE_CONTRACT,
                 SelfMaintenanceBlock: ENVELOPE_CONTRACT,
                 CtxElasticBlock: (ENVELOPE_CONTRACT, Searched),
                 KnowledgeFetcherBlock: (ENVELOPE_CONTRACT, FetchResult)}
    if type(owner) in contracts:
        return contracts[type(owner)]
    try:
        from g6.core.result import Result
        annotation = get_type_hints(fn).get("return")
        if getattr(annotation, "__origin__", None) is Result:
            return next(iter(get_args(annotation)), None)
    except (NameError, TypeError):
        pass
    return None


def component_call(fn, *args, **kwargs):
    """Composition's direct block boundary, including its own exceptions."""
    from g6.core.result import Result
    try:
        outcome = fn(*args, **kwargs)
    except Exception as exc:
        return Result.fail(external_value(str(exc)))
    if isinstance(outcome, Result):
        return (Result.fail(external_value(outcome.error)) if outcome.is_fail() else
                Result.ok(component_value(outcome.value, in_result=True, schema=producer_result_contract(fn))))
    return outcome


def caller_echo(value):
    return external_value(value)


def quoted_content(value):
    return external_value(value)


def knowledge_quotes(value, *, schema=None):
    """Copy declared knowledge data without discovering contracts inside it.

    None declares an entirely data payload. SearchResponse's declared fields
    include the open payload: everything below it is data except payload.reason.
    The MCP knowledge helper also declares passage/items as fetch/context data.
    """
    from g6._context_engine._envelope import SearchResponse
    out = _model_values(value)
    if schema is SearchResponse and isinstance(out, Mapping):
        # The helper's known result fields are data; its other host prose still
        # receives the default guidance filter. No nested key selects a model.
        data_fields = (*SearchResponse.model_fields, "passage", "items")
        out = {key: external_value(child) if key in data_fields else child for key, child in out.items()}
        return reply_for_agent(out, _schema=schema)
    return component_value(out, in_result=True, schema=schema)


def program_output(value):
    return external_value(value)


def program_diagnostic(prefix, output):
    return _MixedText((prefix, "\n", program_output(output)))


def receipts(value):
    """Paths and exact text, retained in host memory, never accepted from JSON."""
    found, todo = [], [((), value)]
    while todo:
        path, item = todo.pop()
        if len(path) > _MAX_GUIDANCE_DEPTH:
            raise ExternalDataLimit("[G6_E_RESOURCE_LIMIT] external text receipts are too deeply nested")
        if isinstance(item, _MixedText) or is_external(item):
            found.append((path, str(item)))
        elif isinstance(item, dict):
            todo.extend(((*path, key), child) for key, child in item.items())
        elif isinstance(item, (list, tuple)):
            todo.extend(((*path, index), child) for index, child in enumerate(item))
    return tuple(found)


def restore(value, saved):
    """Restore only receipts captured from this snapshot's original object."""
    for path, text in saved:
        parent = value
        try:
            for key in path[:-1]:
                parent = parent[key]
            if not path:
                if value == text:
                    value = _MixedText(text.parts) if isinstance(text, _MixedText) else _ExternalText(value)
            elif parent[path[-1]] == text:
                parent[path[-1]] = _MixedText(text.parts) if isinstance(text, _MixedText) else _ExternalText(text)
        except (KeyError, IndexError, TypeError):
            continue
    return value


def freeze_output(value, *, snapshot=...):
    """Validate the original snapshot once, then filter those frozen bytes.

    Source custody is matched against the producer's bytes. The derived reply
    retains those sources; its eventual transport ticket binds the filtered bytes.
    """
    from g6.core import cloud_consent as cloud
    saved = receipts(value if snapshot is ... else snapshot)
    body, sources, metadata = cloud.freeze_output(value, snapshot=snapshot)
    shown = reply_for_agent(restore(json.loads(body), saved))
    return json.dumps(shown, separators=(',', ':'), allow_nan=False), sources, metadata


def detached(value):
    body, sources, metadata = freeze_output(value)
    return restore(json.loads(body), receipts(value)), sources, metadata


def shortened(original, text):
    """A cut of already external text retains its receipt inside compact replies."""
    if isinstance(original, _MixedText):
        parts, left = [], len(text) - 3
        for part in original.parts:
            if left <= 0:
                break
            take = part[:left]
            parts.append(_ExternalText(take) if is_external(part) else take)
            left -= len(take)
        return _MixedText((*parts, "..."))
    return _ExternalText(text) if is_external(original) else text


def borrowed_envelope(value, supplied):
    """A supplied card and its envelope mirrors are always guidance."""
    return reply_for_agent(value)


class ComponentRegistryIngress:
    """Observe actual component execution without copying bound admission rules."""

    def __init__(self, target):
        self.target = target
        self.failure = None
        self.called = False
        self.value = None

    def __getattr__(self, name):
        return getattr(self.target, name)

    def invoke(self, *args, **kwargs):
        from returns.result import Failure, Success
        from g6.core.registry import ComponentRegistry
        from g6.core.result import Result
        owner = self

        def run(fn):
            @wraps(fn)
            def invoke(*a, **k):
                owner.called = True
                try:
                    outcome = fn(*a, **k)
                except Exception as exc:
                    owner.failure = external_value(str(exc))
                    return Result.fail(owner.failure)
                if isinstance(outcome, Result):
                    if outcome.is_fail():
                        owner.failure = external_value(outcome.error)
                        return Result.fail(owner.failure)
                    owner.value = component_value(outcome.value, in_result=True, schema=producer_result_contract(fn))
                    return Result.ok(owner.value)
                return outcome
            return invoke

        class Dispatch:
            def __getattr__(self, name):
                return getattr(owner.target, name)

            def get_class(self, name):
                cls = owner.target.get_class(name)
                if cls is None:
                    return None

                def build(**options):
                    block = cls(**options)
                    # Wrap this call's instance only; never mutate a shared class.
                    block.infer = run(block.infer)
                    screen = getattr(block, "screen_raw", None)
                    if callable(screen):
                        block.screen_raw = run(screen)
                    return block
                return build

        if type(self.target) is ComponentRegistry:
            return ComponentRegistry.invoke(Dispatch(), *args, **kwargs)
        # A supplied registry is itself the extension's execution boundary.
        self.called = True
        try:
            outcome = self.target.invoke(*args, **kwargs)
        except Exception as exc:
            self.failure = external_value(str(exc))
            return Failure(self.failure)
        if outcome.__class__.__name__ == "Success":
            self.value = component_value(outcome.unwrap(), in_result=True,
                                         schema=producer_result_contract(self.target.invoke))
            return Success(self.value)
        self.failure = external_value(str(outcome.failure()))
        return Failure(self.failure)


def component_failure_reply(message, *, request_id=""):
    """Keep the raw diagnostic in data; envelope errors and cards are guidance."""
    from g6.core.door import blocked_reply
    reply = blocked_reply(str(message), request_id=request_id)
    reply["detail"] = external_value(str(message))
    return reply_for_agent(reply)
