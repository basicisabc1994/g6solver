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
REPLY_WITHHELD = "G6_E_REPLY_WITHHELD"
CODE_NOTICE = "The component's code was not a G6 code; it was withheld whole."
_CODE = re.compile(r"G6_E_[A-Z0-9_]{1,64}")
_IDENTITY = ("completion_state", "request_id", "run_id", "job_id", "status")
# Character limits include JSON's worst-case twelve-byte Unicode escaping.
# Containers also have an encoded-byte limit, covering their opaque extra fields.
IDENTITY_LIMITS = {"completion_state": 64, "request_id": 256, "run_id": 256, "job_id": 256, "status": 64}
CARD_TEXT_LIMIT = 2048
CARD_MAX_BYTES = 8192
CONTROL_MAX_BYTES = 32768
CONTROL_MAX_DEPTH = 16
STAGE_LIMIT = 64


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
_CONTROL = ReplySchema("controls")


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
    if schema.kind in ("selector", "controls"):
        steps = envelope.get("mechanisms", [])
        return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
    if schema.kind == "premortem":
        step = envelope.get("model_step")
        return [step] if isinstance(step, dict) else []
    return []


def _issued_code(value):
    return isinstance(value, str) and len(value) <= 69 and _CODE.fullmatch(value) is not None


def _error_code(error):
    if not isinstance(error, str):
        return None
    # Read the whole code token, never a valid-looking prefix of component data.
    code = error[1:].partition("]")[0] if error.startswith("[") and "]" in error else error.partition(" ")[0]
    return code if _issued_code(code) else None


def _bounded_text(value, maximum):
    return value if isinstance(value, str) and len(value) <= maximum else WITHHELD


def _controls(envelope, schema, depth=0):
    """Bounded control projection. Never inspect a lexical/application value."""
    if depth >= CONTROL_MAX_DEPTH:
        return WITHHELD
    kept = {key: _bounded_text(envelope[key], IDENTITY_LIMITS[key]) for key in _IDENTITY if key in envelope}

    def keep_code(code):
        if _issued_code(code):
            return code
        kept["code_notice"] = CODE_NOTICE
        return WITHHELD

    card = envelope.get("warning_card")
    if isinstance(card, dict):
        code = keep_code(card.get("code"))
        if code != WITHHELD:
            if (_measure(card) <= CARD_MAX_BYTES and all(
                    key not in card or isinstance(card[key], str) and len(card[key]) <= CARD_TEXT_LIMIT
                    for key in ("message", "suggested_action"))):
                kept["warning_card"] = deepcopy(card)
            else:
                kept["warning_card"] = {"code": code, "message": WITHHELD, "suggested_action": WITHHELD}
    elif card is not None:
        keep_code(None)
    for key in ("code", "cloud_consent_code"):
        if key in envelope:
            kept[key] = keep_code(envelope[key])
    error = envelope.get("error")
    if isinstance(error, str):
        code = keep_code(_error_code(error))
        kept["error"] = f"[{code}] Reply withheld for size." if code != WITHHELD else WITHHELD
    elif isinstance(error, dict) and "code" in error:
        kept["error"] = {"code": keep_code(error["code"])}
    for key in ("repair", "remedy"):
        holder = envelope.get(key)
        if isinstance(holder, dict) and "code" in holder:
            kept[key] = {"code": keep_code(holder["code"])}
    if schema.kind in ("selector", "controls"):
        diagnostics = []
        for step in _steps(envelope, schema):
            facts = step.get("facts")
            if isinstance(facts, dict) and "last_outcome" in facts:
                diagnostics.append({"stage": _bounded_text(step.get("stage"), STAGE_LIMIT),
                                    "facts": {"last_outcome": keep_code(facts["last_outcome"])}})
        if diagnostics:
            kept["mechanisms"] = diagnostics
    if (schema.result is not None or schema.kind == "controls") and isinstance(envelope.get("result"), dict):
        kept["result"] = _controls(envelope["result"], schema.result or _CONTROL, depth + 1)
    elif "result_control" in envelope:
        # Jobs publishes this host-built projection beside its opaque snapshot.
        # Its layout declares control positions, never the snapshot's data keys.
        control = envelope["result_control"]
        kept["result"] = (_controls(control, _CONTROL, depth + 1) if isinstance(control, dict)
                          and _measure(control) <= CONTROL_MAX_BYTES else WITHHELD)
    if "code_notice" in envelope and schema.kind == "controls":
        kept["code_notice"] = CODE_NOTICE
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
    if isinstance(card, dict) and _issued_code(card.get("code")):
        return card["code"]
    for key in ("code", "cloud_consent_code"):
        if _issued_code(controls.get(key)):
            return controls[key]
    error = controls.get("error")
    if isinstance(error, str):
        code = _error_code(error)
        if code:
            return code
    if isinstance(error, dict) and _issued_code(error.get("code")):
        return error["code"]
    if isinstance(controls.get("result"), dict):
        code = _original_code(controls["result"])
        if code:
            return code
    for key in ("remedy", "repair"):
        if isinstance(controls.get(key), dict) and _issued_code(controls[key].get("code")):
            return controls[key]["code"]
    for step in _steps(controls, SELECTOR):
        facts = step.get("facts")
        code = facts.get("last_outcome") if isinstance(facts, dict) else None
        if _issued_code(code):
            return code
    return None


def _fallback_code(controls):
    code = _original_code(controls)
    if code:
        return code
    current = controls
    while isinstance(current, dict):
        if "code_notice" in current:
            return REPLY_WITHHELD
        current = current.get("result")
    # Preserve the pre-existing no-code fallback. Component data in a code
    # position instead takes the distinct withholding code above.
    return "G6_E_INPUT_VALIDATION"


def fixed_refusal(code=REPLY_WITHHELD, *, controls=None):
    """Fixed last-resort template; only a bounded code and bounded identity vary.

    No door/registry-generated text or component card enters this body. The
    all-maxima MCP rendering is tested below MAX_REPLY_BYTES. With controls=None
    only the issued code varies, for the final egress guard.
    """
    code = code if _issued_code(code) else REPLY_WITHHELD
    refused = {"ok": False, "completion_state": "blocked-escalated",
               "error": f"[{code}] Reply withheld for size.",
               "warning_card": {"code": code, "message": "Reply withheld for size."},
               "size_notice": WITHHELD,
               "code_policy": "A component value that was not a G6 code was withheld whole."}
    for key in ("request_id", "run_id", "job_id", "status"):
        if controls is not None and key in controls:
            refused[key] = _bounded_text(controls[key], IDENTITY_LIMITS[key])
    return refused


def reply_code(reply, *, schema=None):
    """An issued code from declared controls, or the applicable fallback code."""
    return _fallback_code(_controls(reply, schema or schema_of(reply))) if isinstance(reply, dict) else _fallback_code({})


def oversize_refusal(reply=None, *, schema=None, limit=MAX_REPLY_BYTES, measure=None):
    schema = schema or schema_of(reply)
    measure = measure or _measure
    controls = _controls(reply, schema) if isinstance(reply, dict) else {}
    code = _fallback_code(controls)
    refused = fixed_refusal(code)
    refused.update(controls)
    refused["size_notice"] = WITHHELD
    if isinstance(reply, dict):
        # Every non-control field is one opaque unit, regardless of its shape.
        for key in reply:
            if key not in refused and key != "ok":
                refused[key] = WITHHELD
    if measure(refused) > limit:
        # The explicit last resort: no partial card or identity can be truthful.
        refused = fixed_refusal(code, controls=controls)
        if measure(refused) > limit:
            refused = fixed_refusal(code)
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
