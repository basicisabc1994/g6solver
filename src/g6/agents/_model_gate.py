# SPDX-License-Identifier: Apache-2.0
"""The send-time model gates the agents family's runtime applies, and nothing that calls a model.

``g6.agents._runtime`` makes no model call of its own and imports nothing from ``g6.core.llm``
(tests/agents/test_agent_runtime.py). Its admission of a coding session reaches the kill switch, the key-file
opt-out and the live consent recheck through here, as it reaches ``LLMError`` through ``g6.core.cloud_consent``.
"""
from g6.core.llm import _check_model_send, model_calls_disabled

__all__ = ["_check_model_send", "model_calls_disabled"]
