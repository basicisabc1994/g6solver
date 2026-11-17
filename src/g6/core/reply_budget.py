# SPDX-License-Identifier: Apache-2.0
"""Encoded reply limits without cutting lexical answers or stable refusal codes."""
from __future__ import annotations

from copy import deepcopy
import json

MAX_REPLY_BYTES = 256 * 1024
TRUNCATED = "[truncated]"


def oversize_refusal(reply=None) -> dict:
    from g6.core.door import blocked_reply

    refused = blocked_reply("[G6_E_INPUT_VALIDATION] The reply exceeds 256 KB; pass fewer or shorter inputs.")
    if isinstance(reply, dict):
        for key in ("request_id", "run_id", "job_id", "status"):
            value = reply.get(key)
            if isinstance(value, str):
                refused[key] = value if len(value) <= 256 else value[:256] + TRUNCATED
    return refused


def fit_reply(reply, *, limit=MAX_REPLY_BYTES, measure=None, refuse=True):
    """Shorten advisory reasons and echoed ids first; otherwise return a fixed refusal.

    ``measure`` lets the final transport include rendering and protocol wrappers.
    Only a detached copy changes. Candidates, calls, fixed hypotheses, checks,
    consent instructions, error codes and model execution facts are never cut.
    """
    measure = measure or (lambda value: len(json.dumps(value).encode("utf-8")))
    if measure(reply) <= limit:
        return reply
    out = deepcopy(reply)

    def objects(value):
        if isinstance(value, dict):
            yield value
            # Recurse only through reply envelopes, not caller data or calls.
            for key in ("result", "results"):
                yield from objects(value.get(key))
        elif isinstance(value, list):
            for child in value:
                yield from objects(child)

    envelopes = list(objects(out))
    for ceiling in (150, 75, 37, 18, 9, 4, 2, 1, 0):
        for envelope in envelopes:
            for step in envelope.get("mechanisms", []):
                if step.get("stage") == "model_rerank" and step.get("reasons"):
                    step["reasons"] = {key: text[:ceiling] for key, text in step["reasons"].items()}
                    step["reasons_note"] = "model reasons shortened to fit the encoded reply budget"
                    if ceiling == 0:
                        step["reasons"] = {}
                        step["reasons_note"] = "model reasons shortened then dropped to fit the encoded reply budget"
        if measure(out) <= limit:
            return out
    # Caller ids are metadata, never a lexical answer or a refusal's code.
    for ceiling in (256, 64, 16):
        for envelope in envelopes:
            metadata = [(envelope, key) for key in ("run_id", "request_id")]
            # An unissued outcome-looking string is copied into selection
            # diagnostics; it is caller metadata, not a refusal's stable code.
            if "candidates" in envelope and (envelope.get("remedy") or {}).get("key") == "unknown_code":
                metadata.append((envelope.get("remedy") or {}, "code"))
                metadata.extend((step.get("facts", {}), "last_outcome")
                                for step in envelope.get("mechanisms", []))
            for holder, key in metadata:
                value = holder.get(key)
                if isinstance(value, str) and len(value) > ceiling:
                    holder[key] = value[:ceiling] + TRUNCATED
        if measure(out) <= limit:
            return out
    # Shorten explanation prose, preserving ran/model/model_calls and all calls.
    for ceiling in (150, 75, 37, 18, 9, 4, 2, 1, 0):
        for envelope in envelopes:
            steps = [*envelope.get("mechanisms", []), envelope.get("model_step", {})]
            for step in steps:
                for key in ("why", "reasons_note"):
                    if step.get("stage") == "model_rerank" and key == "why":
                        continue  # retain the actual skip/fallback cause
                    text = step.get(key)
                    if isinstance(text, str) and len(text) > ceiling + len(TRUNCATED):
                        step[key] = text[:ceiling] + TRUNCATED
        if measure(out) <= limit:
            return out
    return oversize_refusal(out) if refuse else out
