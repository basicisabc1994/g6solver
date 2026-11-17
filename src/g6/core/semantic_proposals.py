# SPDX-License-Identifier: Apache-2.0
"""One guarded, bounded completion for non-authoritative semantic proposals.

Only the explicit payload is rendered. Never render repository context, exceptions,
or caller state. Provider failures leave the deterministic caller's work intact.
"""
from __future__ import annotations

import json
import os
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
               disabled_reason: str = "not requested", auto: bool = False,
               auto_reason: str = 'skipped: model rerank needs rerank="model" until the consent fix ships'
               ) -> tuple[dict | None, dict]:
    """JSON object or a skipped step. Reasons never echo a provider's private error."""
    step: dict[str, Any] = {"ran": False, "why": disabled_reason, "model_calls": 0}
    if not enabled:
        return None, step
    if os.environ.get("G6_DISABLE_LLM"):
        step["why"] = "disabled by G6_DISABLE_LLM"
        return None, step
    if auto:
        # Bound admission has no side-effect-free dry run for both the send
        # and the reply. Do not duplicate its decision or taint a local answer
        # with sources from refused optional advice.
        step["why"] = auto_reason
        return None, step
    try:
        caller = consent_callers.provider_from_env()
        while isinstance(caller, llm.EmptyRetry):
            caller = caller._inner
        if isinstance(caller, llm.NoModel):
            step["why"] = "no model configured; using local proposals"
            return None, step
        step["model"] = llm._caller_name(caller)
        sources = contract("client_request").sources(payload)
        caller = _one_call(caller)
        with request_sources(sources), counting() as sent:
            try:
                text = caller.complete(system, json.dumps(payload, ensure_ascii=True), max_tokens=MAX_TOKENS)
            finally:
                step["model_calls"] = sent()
    except Exception as exc:  # Optional advice never prevents the local answer.
        if REQUIRED in str(exc):
            step["why"] = "consent withheld"
        elif "G6_E_LLM_DISABLED" in str(exc):
            step["why"] = "disabled by G6_DISABLE_LLM"
        elif "G6_E_LLM_TIMEOUT" in str(exc) or isinstance(exc, TimeoutError):
            step["why"] = f"model timeout (configured bound: {TIMEOUT_S} s)"
        else:
            step["why"] = "model failed"
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
