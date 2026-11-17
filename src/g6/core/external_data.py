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

from g6.core.agent_guidance import (
    _ExternalText, _MixedText, _MAX_GUIDANCE_DEPTH, _structured, guidance_role, is_external, reply_for_agent,
    child_guidance_positions, guidance_positions, result_envelope,
)


class ExternalDataLimit(ValueError):
    """The host could not safely copy external text receipts."""


def external_value(value, *, _component=False):
    """Copy external JSON-like data, marking text without inspecting its words."""
    results, active = {}, set()
    todo = [(value, False, 0, False, True, frozenset())]
    while todo:
        item, opaque, guidance, done, at_root, pending = todo.pop()
        positions = pending | (guidance_positions(item) if _component and not opaque else frozenset())
        at_envelope = at_root or (not opaque and result_envelope(item))
        ident = (id(item), opaque, guidance, at_root, pending)
        if isinstance(item, str):
            results[ident] = str.__str__(item) if guidance else _ExternalText(item)
        elif not isinstance(item, (Mapping, list, tuple)):
            results[ident] = item
        elif done:
            results[ident] = ({key: results[(id(child), _component and (opaque or _structured(key)),
                                           guidance_role(key, guidance, opaque, at_root=at_envelope,
                                                         positions=positions)
                                           if _component else 0, False, child_guidance_positions(positions, key))]
                              for key, child in item.items()}
                             if isinstance(item, Mapping) else
                             [results[(id(child), opaque, guidance, False, pending)] for child in item])
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
                             guidance_role(key, guidance, opaque, at_root=at_envelope, positions=positions)
                             if _component else 0, False, False, child_guidance_positions(positions, key))
                            for key, child in item.items())
            else:
                todo.extend((child, opaque, guidance, False, False, pending) for child in item)
    return results[(id(value), False, 0, True, frozenset())]


def component_value(value, *, in_result=False):
    """Classify roles at their reply position before granting data receipts.

    A component's payload sits under result. Its own complete result envelope
    retains schema-defined guidance positions there; ordinary data role names
    stay data. Warning cards remain guidance in either position.
    """
    envelope = {"result": value} if in_result else value
    shown = reply_for_agent(external_value(envelope, _component=True))
    # A depth refusal replaces the whole envelope; retain that refusal as the
    # component result rather than trying to unwrap a result that is absent.
    return shown.get("result", shown) if in_result else shown


def component_call(fn, *args, **kwargs):
    """Composition's direct block boundary, including its own exceptions."""
    from g6.core.result import Result
    try:
        outcome = fn(*args, **kwargs)
    except Exception as exc:
        return Result.fail(external_value(str(exc)))
    if isinstance(outcome, Result):
        return (Result.fail(external_value(outcome.error)) if outcome.is_fail() else
                Result.ok(component_value(outcome.value, in_result=True)))
    return outcome


def caller_echo(value):
    return external_value(value)


def quoted_content(value):
    return external_value(value)


def knowledge_quotes(value):
    """Preserve knowledge data records, including paths and caller echoes.

    The search schema declares data fields, including open payloads. Fetch uses
    the same PassageItem/View, denied and withheld records. All their text is
    data, not just passage text, except schema-declared outcome guidance.
    Undeclared G6 prose retains the default filter;
    cards and complete component envelopes retain their guidance positions.
    """
    from pydantic import BaseModel
    from g6._context_engine._envelope import PassageItem, SearchResponse
    from g6._context_engine._view import View

    record_types = {name: field.annotation for name, field in PassageItem.model_fields.items()
                    if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)}

    def walk(item, schema=None, depth=0):
        if depth > _MAX_GUIDANCE_DEPTH:
            raise ExternalDataLimit("[G6_E_RESOURCE_LIMIT] knowledge quotations are too deeply nested")
        if isinstance(item, BaseModel):
            schema = type(item)
            item = {name: getattr(item, name) for name in
                    (*type(item).model_fields, *type(item).model_computed_fields)}
        if isinstance(item, Mapping):
            out = {key: walk(child, record_types.get(key), depth + 1)
                   for key, child in item.items()}
            passage_view = any(name in item and kind is View for name, kind in record_types.items())
            if (schema is not None or passage_view or guidance_positions(item) or
                    SearchResponse.model_fields.keys() <= item.keys()):
                return component_value(out, in_result=True)
            if depth == 0:
                # The knowledge result root follows SearchResponse's data
                # positions even when the engine returns a partial dictionary.
                out = {key: component_value(child, in_result=True)
                       if key in SearchResponse.model_fields else child for key, child in out.items()}
            return out
        if isinstance(item, (list, tuple)):
            children = [walk(child, schema, depth + 1) for child in item]
            return tuple(children) if isinstance(item, tuple) else children
        return item

    # MCP also supplies partial SearchResponse dictionaries. Supply that schema
    # explicitly rather than treating every reason-named data key as guidance.
    return reply_for_agent(walk(value), _schema=SearchResponse)


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
                    owner.value = component_value(outcome.value, in_result=True)
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
            self.value = component_value(outcome.unwrap(), in_result=True)
            return Success(self.value)
        self.failure = external_value(str(outcome.failure()))
        return Failure(self.failure)


def component_failure_reply(message, *, request_id=""):
    """Keep the raw diagnostic in data; envelope errors and cards are guidance."""
    from g6.core.door import blocked_reply
    reply = blocked_reply(str(message), request_id=request_id)
    reply["detail"] = external_value(str(message))
    return reply_for_agent(reply)
