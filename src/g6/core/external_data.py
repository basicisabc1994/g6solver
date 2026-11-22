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
import sys
from typing import get_args, get_type_hints

from g6.core.agent_guidance import (
    _ExternalText, _GuidanceText, _MixedText, _MAX_GUIDANCE_DEPTH, _structured, guidance_role, is_external,
    reply_for_agent, child_guidance_positions, guidance_positions, ENVELOPE_CONTRACT,
)


class ExternalDataLimit(ValueError):
    """The host could not safely copy external text receipts."""


def _model_values(item, depth=0, *, explicit=(), positions=(), path=(), _memo=None):
    """Serialize models and retain the wire roles declared by runtime identities."""
    from pydantic import BaseModel

    if depth > _MAX_GUIDANCE_DEPTH:
        raise ExternalDataLimit("[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person.")
    if _memo is None:
        _memo = {}
    # Keep shared containers shared so the door can still count repeated reads.
    # Explicit serialization bindings can depend on the full path, so those
    # conversions must continue to visit each position independently.
    identity = (id(item), positions)
    if not explicit and identity in _memo:
        return _memo[identity][1]
    source = item
    if isinstance(item, BaseModel):
        from g6.core.serialized_guidance import SerializedGuidance, validate_model_serialization
        model = type(item)
        runtime_positions = validate_model_serialization(model, positions, explicit, path)
        item = item.model_dump(mode="json")
        if runtime_positions:
            # Preserve the roles resolved before dumping erased class identity.
            # Route declarations still apply separately, including to plain dicts.
            item = external_value(item, _component=True, _schema=SerializedGuidance(model, runtime_positions))
    if isinstance(item, Mapping):
        result = {key: _model_values(child, depth + 1, explicit=explicit,
                                    positions=child_guidance_positions(positions, key), path=(*path, key), _memo=_memo)
                  for key, child in item.items()}
    elif isinstance(item, (list, tuple)):
        children = [_model_values(child, depth + 1, explicit=explicit,
                                  positions=child_guidance_positions(positions, "*"), path=(*path, index), _memo=_memo)
                    for index, child in enumerate(item)]
        result = tuple(children) if isinstance(item, tuple) else children
    else:
        return item
    if not explicit:
        # Model dumps contain temporary dictionaries. Retain their originals so
        # a later dump cannot reuse an id already held in this conversion.
        _memo[identity] = (source, result)
    return result


def external_value(value, *, _component=False, _schema=None):
    """Copy external JSON-like data, marking text without inspecting its words."""
    results, active = {}, set()
    root_positions = guidance_positions(value, schema=_schema)
    root_guidance = int(() in root_positions)
    todo = [(value, False, root_guidance, False, True, root_positions)]
    while todo:
        item, opaque, guidance, done, at_root, pending = todo.pop()
        positions = pending
        sequence_guidance = guidance or int(("*",) in pending)
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
                             [results[(id(child), opaque, sequence_guidance, False,
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
                todo.extend((child, opaque, sequence_guidance, False, False,
                             child_guidance_positions(pending, "*")) for child in item)
    return results[(id(value), False, root_guidance, True, root_positions)]


def component_value(value, *, in_result=False, schema=None, _producer=None):
    """Classify roles at their reply position before granting data receipts.

    A component payload is data unless its producer or a registered runtime
    identity declares guidance roles. Reply roots declare their own envelope;
    no nested object is recognised by its keys. Private guidance receipts survive
    subsequent component wrappers.
    """
    contract = schema if in_result or schema is not None else ENVELOPE_CONTRACT
    return _reply_value(value, contract, producer=_producer)


def _reply_value(value, contract, *, knowledge=False, producer=None):
    """The sole guarded conversion from component values to reply data."""
    try:
        from g6.core.serialized_guidance import explicit_serialization_bindings, validate_explicit_positions
        if producer is not None:
            contract = producer_result_contract(producer)
        # Resolve the declaration before running any user serialization code.
        positions = guidance_positions(None, schema=contract)
        value = _model_values(value, explicit=explicit_serialization_bindings(contract), positions=positions)
        validate_explicit_positions(value, contract)
        from g6._context_engine._envelope import SearchResponse
        if knowledge and contract is SearchResponse and isinstance(value, Mapping):
            data_fields = (*SearchResponse.model_fields, "passage", "items")
            value = {key: external_value(child) if key in data_fields else child for key, child in value.items()}
        else:
            value = external_value(value, _component=True, _schema=contract)
        value = reply_for_agent(value, _schema=contract)
        # Account for the exact JSON/UTF-8 representation here, before a later
        # envelope or transport can try to serialize an unsupported value.
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        return value
    except Exception as exc:
        # A data or shape failure becomes a bounded refusal. Control flow (a person's interrupt,
        # job cancellation, interpreter exit) is never swallowed into a reply.
        name = type(exc).__name__
        name = name if len(name) <= 80 and name.isascii() and name.isidentifier() else "Exception"
        return {"ok": False, "completion_state": "blocked-escalated",
                "error": "[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. "
                         f"Tell the person. ({name})"}


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
    from g6.knowledge._index import Searched
    from g6.knowledge.knowledge_fetcher.fetcher import FetchResult
    # core imports no component (pyproject's first import contract): a producer is located by module and class name
    # in what is already loaded (an instance's own module is), and the class must be that one.
    contracts = {("g6.goal_engine.block", "GoalEngineBlock"): ENVELOPE_CONTRACT,
                 ("g6.coding_agent_gate.coding_agent_gate_block", "CodingAgentGateBlock"): ENVELOPE_CONTRACT,
                 ("g6.action_gating.action_gating_block", "ActionGatingBlock"): ENVELOPE_CONTRACT,
                 ("g6.algorithm_design.block", "AlgorithmDesignBlock"): ENVELOPE_CONTRACT,
                 ("g6.complexity.block", "ComplexityBlock"): ENVELOPE_CONTRACT,
                 ("g6.self_maintenance.block", "SelfMaintenanceBlock"): ENVELOPE_CONTRACT,
                 ("g6.knowledge.ctx_elastic.block", "CtxElasticBlock"): (ENVELOPE_CONTRACT, Searched),
                 ("g6.knowledge.knowledge_fetcher.block", "KnowledgeFetcherBlock"): (ENVELOPE_CONTRACT, FetchResult)}
    cls = type(owner)
    key = (cls.__module__, cls.__name__)
    if key in contracts and getattr(sys.modules.get(key[0]), key[1], None) is cls:
        return contracts[key]
    from g6.core.producer_contracts import builtin_result_contract
    builtin = builtin_result_contract(owner)
    if builtin is not None:
        return builtin
    try:
        from g6.core.result import Result
        annotation = get_type_hints(fn).get("return")
        if getattr(annotation, "__origin__", None) is Result:
            return next(iter(get_args(annotation)), None)
    except (NameError, TypeError):
        pass
    return None


def exception_text(error):
    """An exception's (or a failure value's) text for a reply; nothing it runs can escape.

    Foreign code owns ``__str__`` and ``__repr__``: either may raise, or return a
    ``str`` subclass with methods of its own. The text is always a plain ``str``,
    or the type's name when no text can be read.
    """
    for render in (str, repr):
        try:
            return "".join((render(error),))
        except Exception:
            continue
    try:
        name = "".join((type(error).__name__,))
    except Exception:
        name = "object"
    return f"{name} (its text could not be read)"


def component_call(fn, *args, **kwargs):
    """Composition's direct block boundary, including its own exceptions."""
    from g6.core.result import Result
    try:
        outcome = fn(*args, **kwargs)
    except Exception as exc:
        return Result.fail(external_value(exception_text(exc)))
    if isinstance(outcome, Result):
        return (Result.fail(external_value(outcome.error)) if outcome.is_fail() else
                Result.ok(component_value(outcome.value, in_result=True, _producer=fn)))
    return outcome


def caller_echo(value):
    return external_value(value)


#: The most values (containers, keys and leaves) ``plain_text`` reads in one value.
MAX_PLAIN_VALUES = 100_000


class PlainTextLimit(ValueError):
    """A value G6 takes as data holds itself, or is past the depth or the count of values G6 reads."""


def plain_text(value):
    """``value`` with G6's own text receipts (external, guidance, mixed) as plain ``str``, at a validator boundary.

    A validator that admits only exact ``str`` admits caller text that crossed G6's ingress; the receipt is a reply
    role, never part of a stored record. Any other ``str`` subclass is left as it is, for the validator to refuse.
    Containers are copied (dict, list, tuple); nothing else changes. Only those exact three types are unwrapped: a
    caller's subclass of one is a ``str`` subclass from outside G6 and is left as it is too. Types are compared by
    identity, never by equality, which a foreign metaclass defines.

    The walk is bounded: a container that holds itself, more than ``_MAX_GUIDANCE_DEPTH`` levels, or more than
    ``MAX_PLAIN_VALUES`` values read (a value held twice is read twice) raise ``PlainTextLimit``, before any more is read.
    """
    budget = [MAX_PLAIN_VALUES]
    return _plain(value, 0, set(), budget)


def _plain(value, depth, active, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise PlainTextLimit(f"a value G6 takes as data holds more than {MAX_PLAIN_VALUES} values")
    kind = type(value)
    if kind is _ExternalText or kind is _GuidanceText or kind is _MixedText:
        return str.__str__(value)
    if not (kind is dict or kind is list or kind is tuple):
        return value
    if depth >= _MAX_GUIDANCE_DEPTH:
        raise PlainTextLimit(f"a value G6 takes as data is nested deeper than {_MAX_GUIDANCE_DEPTH} levels")
    if id(value) in active:
        raise PlainTextLimit("a value G6 takes as data holds itself")
    active.add(id(value))
    try:
        if kind is dict:
            return {_plain(key, depth + 1, active, budget): _plain(child, depth + 1, active, budget)
                    for key, child in value.items()}
        children = [_plain(child, depth + 1, active, budget) for child in value]
        return children if kind is list else tuple(children)
    finally:
        active.discard(id(value))


def quoted_content(value):
    return external_value(value)


def knowledge_quotes(value, *, schema=None):
    """Copy declared knowledge data without discovering contracts inside it.

    None declares no route roles; registered runtime models retain their own
    declaration. SearchResponse's declared fields include the open payload:
    everything below it is data except payload.reason. The MCP knowledge helper
    also declares passage/items as fetch/context data.
    """
    return _reply_value(value, schema, knowledge=True)


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
    try:
        decoded = json.loads(body)
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None
    shown = reply_for_agent(restore(decoded, saved))
    return json.dumps(shown, separators=(',', ':'), allow_nan=False), sources, metadata


def detached(value):
    body, sources, metadata = freeze_output(value)
    try:
        decoded = json.loads(body)
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None
    return restore(decoded, receipts(value)), sources, metadata


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
                    owner.failure = external_value(exception_text(exc))
                    return Result.fail(owner.failure)
                if isinstance(outcome, Result):
                    if outcome.is_fail():
                        owner.failure = external_value(outcome.error)
                        return Result.fail(owner.failure)
                    owner.value = component_value(outcome.value, in_result=True, _producer=fn)
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
            self.failure = external_value(exception_text(exc))
            return Failure(self.failure)
        if outcome.__class__.__name__ == "Success":
            self.value = component_value(outcome.unwrap(), in_result=True, _producer=self.target.invoke)
            return Success(self.value)
        self.failure = external_value(exception_text(outcome.failure()))
        return Failure(self.failure)


def component_failure_reply(message, *, request_id=""):
    """Keep the raw diagnostic in data; envelope errors and cards are guidance."""
    from g6.core.door import blocked_reply
    text = exception_text(message)
    reply = blocked_reply(text, request_id=request_id)
    reply["detail"] = external_value(text)
    return reply_for_agent(reply)
