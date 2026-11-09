"""The cannot-record latch belongs to a folder, including its extended DOS name."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core import stop
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, refused_requests
from g6.self_maintenance.usage import MODEL_REFUSED


def test_extended_folder_name_cannot_bypass_a_cannot_record_stop(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows extended DOS path")
    root = Path(__file__).resolve().parents[2] / ".review" / "finding-4" / uuid4().hex
    root.mkdir(parents=True)

    def handle(path):
        result = loop.ShadowLoop.__new__(loop.ShadowLoop)
        result.root = Path(path).resolve()  # same normalization as the real constructor
        result.log = CycleLog(result.root / CYCLE_LOG)
        result.unreadable = ""
        result.requests = loop._Requests(halted=result._halted, keeping=result._keep_refusal,
                                        on_record=lambda: refused_requests(result.root) | result._here.refused)
        return result

    first, second = handle(root), handle("\\\\?\\" + str(root))
    assert os.path.samefile(first.root, second.root)

    def disk_full(*args, **kwargs):
        raise OSError("neither the refusal nor the fallback STOP can be written")

    def refuse(system, user):
        raise LLMError(f"{MODEL_REFUSED}: policy")

    monkeypatch.setattr(stop, "engaged", lambda: None)
    monkeypatch.setattr(loop, "note_refusal", disk_full)
    monkeypatch.setattr(loop, "ask_to_stop", disk_full)
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        loop._Counted(FakeLLM(refuse), first.requests).complete("system", "refused request")
    assert first.cannot_record and not refused_requests(root)
    backend = FakeLLM(default="sent again")
    try:
        loop._Counted(backend, second.requests).complete("system", "refused request")
    except LLMError:
        pass
    assert backend.calls == [], "the extended path opened a second folder latch and resent an unpersisted refusal"
