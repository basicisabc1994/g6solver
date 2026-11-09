"""A later successful parse must not erase a genuine earlier read failure."""
import json

import pytest

from g6.self_maintenance import usage
from tests.metaharness.refufu2r2.test_roots import FAILURES, answer


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("exception", FAILURES)
def test_a_transient_reader_exception_is_still_unknown(monkeypatch, provider, exception):
    printed = json.dumps(answer(provider))
    original = json.loads
    interrupted = False

    def loads(text, *args, **kwargs):
        nonlocal interrupted
        if not interrupted and text == printed:
            interrupted = True
            raise exception("one interrupted read")
        return original(text, *args, **kwargs)

    monkeypatch.setattr(usage.json, "loads", loads)
    try:
        result = usage._classify_reply(provider, 0, printed)
    except BaseException:
        pytest.fail("an exception escaped the reply classifier")
    assert result[0] == "unknown"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_jsonl_extra_data_is_a_format_choice_not_a_reader_failure(provider):
    printed = json.dumps({"type": "system" if provider == "claude" else "turn.started"})
    printed += "\n" + json.dumps(answer(provider))
    assert usage._classify_reply(provider, 0, printed)[0] == "answer"
