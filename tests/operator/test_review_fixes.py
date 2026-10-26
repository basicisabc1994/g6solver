# SPDX-License-Identifier: Apache-2.0
"""Keep caller recovery instructions without returning credential text."""
from g6.core.llm import LLMError
from g6.operator import propose_tasks


def test_missing_extra_keeps_install_instructions_without_key_text(queue, venture):
    # Removal makes this fail: `_model_diagnostic(exc)` in propose_tasks's caller exception handler.
    class MissingExtra:
        def complete(self, system, user):
            raise LLMError("the Anthropic provider needs the SDK: pip install 'g6[anthropic]'\n"
                           "api_key=sk-test-do-not-disclose; Authorization: Bearer private-credential")

    report = propose_tasks(queue, venture, "Summarise the refund policy", llm=MissingExtra())
    assert report["completion_state"] == "blocked-escalated" and queue.list(venture) == []
    message = report["warning_card"]["message"]
    assert "LLMError" in message and "pip install 'g6[anthropic]'" in message
    assert "sk-test-do-not-disclose" not in message and "private-credential" not in message
