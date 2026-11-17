# SPDX-License-Identifier: Apache-2.0
"""One guarded, bounded completion for non-authoritative semantic proposals.

Only the explicit payload is rendered. Never render repository context, exceptions,
or caller state. Provider failures leave the deterministic caller's work intact.
"""
from __future__ import annotations

import json
from typing import Any

from g6.core import consent_callers, llm
from g6.core.cloud_consent import REQUIRED, request_sources
from g6.core.consent_declarations import contract
from g6.core.model_calls import counting

MAX_TOKENS = 2048
MAX_REPLY_BYTES = 32 * 1024
TIMEOUT_S = 15


def _one_call(caller):
    # The factory's empty-answer retries are inappropriate for optional advice.
    while isinstance(caller, llm.EmptyRetry):
        caller = caller._inner
    if isinstance(caller, llm.NoModel):
        return caller
    if isinstance(caller, llm.AnthropicCaller):
        # The SDK's native client keeps its recipient; disable retries/fallbacks
        # and give each request the same finite timeout as the HTTP/host callers.
        caller._client = caller._get_client().with_options(timeout=TIMEOUT_S, max_retries=0)
        caller.fallbacks = False
    elif hasattr(caller, "timeout"):
        caller.timeout = min(caller.timeout, TIMEOUT_S)
    elif not isinstance(caller, (llm.FakeLLM, llm.UnusableModel, llm._NoKey)):
        raise llm.LLMError("optional proposals need a caller with a bounded timeout")
    return consent_callers.guard(caller)


def model_json(payload: dict[str, Any], system: str, *, enabled: bool = True,
               disabled_reason: str = "not requested") -> tuple[dict | None, dict]:
    """JSON object or a skipped step. Reasons never echo a provider's private error."""
    step: dict[str, Any] = {"ran": False, "why": disabled_reason, "model_calls": 0}
    if not enabled:
        return None, step
    try:
        caller = _one_call(consent_callers.provider_from_env())
        if isinstance(caller, llm.NoModel):
            step["why"] = "no model configured; using local proposals"
            return None, step
        step["model"] = llm._caller_name(caller)
        with request_sources(contract("client_request").sources(payload)), counting() as sent:
            try:
                text = caller.complete(system, json.dumps(payload, ensure_ascii=True), max_tokens=MAX_TOKENS)
            finally:
                step["model_calls"] = sent()
    except Exception as exc:  # Optional advice never prevents the local answer.
        step["why"] = "consent withheld" if REQUIRED in str(exc) else "model failed"
        return None, step
    try:
        if not isinstance(text, str) or len(text) > MAX_REPLY_BYTES or len(text.encode("utf-8")) > MAX_REPLY_BYTES:
            raise ValueError("reply size")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("reply shape")
    except (ValueError, TypeError, RecursionError, MemoryError, UnicodeError):
        step["why"] = "reply unreadable"
        return None, step
    step.update(ran=True, why="one model proposal; advice only")
    return value, step
