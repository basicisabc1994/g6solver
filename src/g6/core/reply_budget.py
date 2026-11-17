# SPDX-License-Identifier: Apache-2.0
"""Compact only producer-declared reply positions; application values are opaque."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re

MAX_REPLY_BYTES = 256 * 1024
TRUNCATED = "[truncated]"
WITHHELD = "[withheld for size]"
_CODE = re.compile(r"G6_E_[A-Z0-9_]+")
_IDENTITY = ("completion_state", "request_id", "run_id", "job_id", "status")


@dataclass(frozen=True)
class ReplySchema:
    kind: str = "envelope"
    result: ReplySchema | None = None

    def to_data(self):
        return {"kind": self.kind, "result": self.result.to_data() if self.result is not None else None}

    @classmethod
    def from_data(cls, value):
        # Only host receipt metadata reaches this decoder, never reply JSON.
        if not isinstance(value, dict):
            return ENVELOPE
        return cls(value["kind"], cls.from_data(value["result"]) if value.get("result") is not None else None)


ENVELOPE = ReplySchema()
SELECTOR = ReplySchema("selector")
PREMORTEM = ReplySchema("premortem")
JOB = ReplySchema("job")


class _DeclaredReply(dict):
    """A producer's process-local declaration, never a key in application JSON."""


def declare_reply(value, schema=ENVELOPE):
    if not isinstance(value, dict):
        return value
    result = _DeclaredReply(value)
    result.reply_schema = schema
    return result


def schema_of(value):
    return declared_schema(value) or ENVELOPE


def declared_schema(value):
    return value.reply_schema if isinstance(value, _DeclaredReply) else None


def _positions(value, schema):
    """The only descent: a result position explicitly declared by host code."""
    if isinstance(value, dict):
        yield value, schema
        if schema.result is not None:
            yield from _positions(value.get("result"), schema.result)


def _steps(envelope, schema):
    if schema.kind == "selector":
        steps = envelope.get("mechanisms", [])
        return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
    if schema.kind == "premortem":
        step = envelope.get("model_step")
        return [step] if isinstance(step, dict) else []
    return []


def _controls(envelope, schema):
    """Lossless control projection. Never inspect a lexical/application value."""
    kept = {key: deepcopy(envelope[key]) for key in (*_IDENTITY, "warning_card") if key in envelope}
    for key in ("code", "cloud_consent_code"):
        if isinstance(envelope.get(key), str) and _CODE.fullmatch(envelope[key]):
            kept[key] = envelope[key]
    error = envelope.get("error")
    if isinstance(error, str):
        match = re.match(r"\[?(G6_E_[A-Z0-9_]+)\]?", error)
        if match:
            kept["error"] = f"[{match.group(1)}] Reply withheld for size."
    elif isinstance(error, dict) and isinstance(error.get("code"), str):
        kept["error"] = {"code": error["code"]}
    for key in ("repair", "remedy"):
        holder = envelope.get(key)
        if isinstance(holder, dict) and isinstance(holder.get("code"), str):
            kept[key] = {"code": holder["code"]}
    if schema.kind == "selector":
        diagnostics = []
        for step in _steps(envelope, schema):
            facts = step.get("facts")
            code = facts.get("last_outcome") if isinstance(facts, dict) else None
            if isinstance(code, str) and _CODE.fullmatch(code):
                diagnostics.append({"stage": step.get("stage"), "facts": {"last_outcome": code}})
        if diagnostics:
            kept["mechanisms"] = diagnostics
    if schema.result is not None and isinstance(envelope.get("result"), dict):
        kept["result"] = _controls(envelope["result"], schema.result)
    elif "result_control" in envelope:
        # Jobs publishes this host-built projection beside its opaque snapshot.
        # It is copied as one declared control value, never parsed as data.
        kept["result"] = deepcopy(envelope["result_control"])
    return kept


def result_control(reply):
    """The minimal projection of a producer-declared wrapped result, or None."""
    schema = schema_of(reply)
    if schema.result is not None:
        result = reply.get("result")
        if isinstance(result, dict):
            return _controls(result, schema.result)
    return None


def _original_code(controls):
    card = controls.get("warning_card")
    if isinstance(card, dict) and isinstance(card.get("code"), str) and _CODE.fullmatch(card["code"]):
        return card["code"]
    for key in ("code", "cloud_consent_code"):
        if key in controls:
            return controls[key]
    error = controls.get("error")
    if isinstance(error, str):
        match = re.match(r"\[(G6_E_[A-Z0-9_]+)\]", error)
        if match:
            return match.group(1)
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    if isinstance(controls.get("result"), dict):
        return _original_code(controls["result"])
    for key in ("remedy", "repair"):
        if key in controls:
            return controls[key]["code"]
    return "G6_E_INPUT_VALIDATION"


def oversize_refusal(reply=None, *, schema=None, limit=MAX_REPLY_BYTES, measure=None):
    from g6.core.door import blocked_reply

    schema = schema or schema_of(reply)
    measure = measure or _measure
    controls = _controls(reply, schema) if isinstance(reply, dict) else {}
    code = _original_code(controls)
    refused = blocked_reply(f"[{code}] Reply withheld for size.")
    refused.update(controls)
    refused["size_notice"] = WITHHELD
    if isinstance(reply, dict):
        # Every non-control field is one opaque unit, regardless of its shape.
        for key in reply:
            if key not in refused and key != "ok":
                refused[key] = WITHHELD
    if measure(refused) > limit:
        # The explicit last resort: no partial card or identity can be truthful.
        refused = blocked_reply(f"[{code}] Reply withheld for size.")
        # A job's own bounded handle/state must remain pollable even when its
        # declared child's card or identity makes the full control body too big.
        for key in ("job_id", "status"):
            if key in controls and measure({**refused, key: controls[key]}) <= limit:
                refused[key] = controls[key]
    return declare_reply(refused, schema)


def _measure(value):
    return len(json.dumps(value).encode("utf-8"))


def fit_reply(reply, *, limit=MAX_REPLY_BYTES, measure=None, refuse=True, schema=None):
    """Use only declared typed positions; never discover schemas from data shape.

    The default contract is G6's root envelope; its result, component outputs and
    metadata are opaque. Producers declare selector/premortem and wrapped replies
    in process, outside their JSON. Only a detached copy changes.
    """
    schema = schema or schema_of(reply)
    measure = measure or _measure
    if measure(reply) <= limit:
        return reply
    out = deepcopy(reply)
    positions = list(_positions(out, schema))
    for ceiling in (150, 75, 37, 18, 9, 4, 2, 1, 0):
        for envelope, declared in positions:
            for step in _steps(envelope, declared):
                reasons = step.get("reasons")
                if step.get("stage") == "model_rerank" and isinstance(reasons, dict) and reasons:
                    step["reasons"] = {key: text[:ceiling] for key, text in reasons.items()}
                    step["reasons_note"] = "model reasons shortened to fit the encoded reply budget"
                    if ceiling == 0:
                        step["reasons"] = {}
                        step["reasons_note"] = "model reasons shortened then dropped to fit the encoded reply budget"
        if measure(out) <= limit:
            return declare_reply(out, schema)
    for ceiling in (256, 64, 16):
        for envelope, declared in positions:
            metadata = [(envelope, key) for key in ("run_id", "request_id")]
            if declared.kind == "selector" and (envelope.get("remedy") or {}).get("key") == "unknown_code":
                metadata.append((envelope["remedy"], "code"))
                metadata.extend((step["facts"], "last_outcome") for step in _steps(envelope, declared)
                                if isinstance(step.get("facts"), dict))
            for holder, key in metadata:
                value = holder.get(key)
                if isinstance(value, str) and len(value) > ceiling:
                    holder[key] = value[:ceiling] + TRUNCATED
        if measure(out) <= limit:
            return declare_reply(out, schema)
    for ceiling in (150, 75, 37, 18, 9, 4, 2, 1, 0):
        for envelope, declared in positions:
            for step in _steps(envelope, declared):
                for key in ("why", "reasons_note"):
                    if step.get("stage") == "model_rerank" and key == "why":
                        continue
                    value = step.get(key)
                    if isinstance(value, str) and len(value) > ceiling + len(TRUNCATED):
                        step[key] = value[:ceiling] + TRUNCATED
        if measure(out) <= limit:
            return declare_reply(out, schema)
    return oversize_refusal(reply, schema=schema, limit=limit, measure=measure) if refuse else declare_reply(out, schema)
