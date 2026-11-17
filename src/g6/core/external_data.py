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

from g6.core.agent_guidance import _ExternalText, _MixedText, _MAX_GUIDANCE_DEPTH, is_external, reply_for_agent


class ExternalDataLimit(ValueError):
    """The host could not safely copy external text receipts."""


def external_value(value):
    """Copy external JSON-like data, marking text without inspecting its words."""
    results, active = {}, set()
    todo = [(value, False)]
    while todo:
        item, done = todo.pop()
        ident = id(item)
        if isinstance(item, str):
            results[ident] = _ExternalText(item)
        elif not isinstance(item, (Mapping, list, tuple)):
            results[ident] = item
        elif done:
            results[ident] = ({key: results[id(child)] for key, child in item.items()}
                             if isinstance(item, Mapping) else
                             [results[id(child)] for child in item])
            if isinstance(item, tuple):
                results[ident] = tuple(results[ident])
            active.remove(ident)
        elif ident in active:
            raise ExternalDataLimit("[G6_E_RESOURCE_LIMIT] cyclic external data")
        elif ident not in results:
            active.add(ident)
            todo.append((item, True))
            todo.extend((child, False) for child in (item.values() if isinstance(item, Mapping) else item))
    return results[id(value)]


def component_value(value):
    return external_value(value)


def component_call(fn, *args, **kwargs):
    """Composition's direct block boundary, including its own exceptions."""
    from g6.core.result import Result
    try:
        outcome = fn(*args, **kwargs)
    except Exception as exc:
        return Result.fail(external_value(str(exc)))
    if isinstance(outcome, Result):
        return (Result.fail(external_value(outcome.error)) if outcome.is_fail() else
                Result.ok(component_value(outcome.value)))
    return outcome


def caller_echo(value):
    return external_value(value)


def quoted_content(value):
    return external_value(value)


def knowledge_quotes(value):
    """Engine passage schemas: redacted view.text and authorised raw text only."""
    value = dict(value)
    items = list(value.get("items") or [])
    passage = value.get("passage")
    for item in [*items, *([passage] if isinstance(passage, dict) else [])]:
        if isinstance(item.get("view"), dict):
            item["view"] = {**item["view"], "text": quoted_content(item["view"]["text"])}
        if "text" in item:
            item["text"] = quoted_content(item["text"])
    return value


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
    """Pydantic mirrors a supplied card into envelope prose; retain its receipts."""
    if supplied is None:
        return value
    value = dict(value)
    card = value.get("warning_card")
    if isinstance(card, dict):
        # Restore text only, never overlay producer flags or add missing fields.
        card = restore(dict(card), receipts(supplied))
        value["warning_card"] = card
    message = supplied.get("message") or supplied.get("why")
    action = supplied.get("suggested_action") or supplied.get("next_step")
    risks = supplied.get("risks") if isinstance(supplied.get("risks"), list) else []

    def inherit(item, sources):
        if isinstance(item, str):
            for original in sources:
                if is_external(original) and item in (original, original.strip()):
                    return _ExternalText(item)
        elif isinstance(item, list):
            return [inherit(child, sources) for child in item]
        return item

    # These are the bound model's explicit copy/strip derivations, not a search
    # for external-looking text in arbitrary reply fields.
    for field, sources in (
        ("block_reason", [message]), ("degradation_reason", [message]),
        ("required_human_decision", [action]), ("safe_next_action", [action]),
        ("known_limitations", risks or [message]), ("recommended_next_steps", [action]),
    ):
        if field in value:
            value[field] = inherit(value[field], sources)
    if isinstance(card, dict):
        for field, sources in (("why", [message]), ("risks", risks or [message]), ("next_step", [action])):
            if field in card:
                card[field] = inherit(card[field], sources)
    return value


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
                    owner.value = component_value(outcome.value)
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
            self.value = component_value(outcome.unwrap())
            return Success(self.value)
        self.failure = external_value(str(outcome.failure()))
        return Failure(self.failure)


def component_failure_reply(message, *, request_id=""):
    """Only the component diagnostic and its envelope mirrors are external."""
    from g6.core.door import blocked_reply
    reply = blocked_reply(str(message), request_id=request_id)
    reply["error"] = external_value(str(message))
    reply["degradation_reason"] = external_value(str(message)[:300])
    card = reply.get("warning_card")
    if isinstance(card, dict):
        card["message"] = external_value(card["message"])
        # Only the message is borrowed; G6's fallback action stays unmarked.
        reply = borrowed_envelope(reply, card)
    return reply
