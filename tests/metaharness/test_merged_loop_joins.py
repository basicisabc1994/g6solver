"""Where the 0.3 integration joined the refusals, freeze, stop and week-span follow-ups in ``metaharness/loop.py``: each
test here is a rule one follow-up kept that the joined code must still keep. Fakes only: no model, no host program, no
network."""
from __future__ import annotations

import pytest

from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop
from g6.self_maintenance import envelope
from g6.self_maintenance.usage import MODEL_REFUSED


def test_a_lost_cycle_that_stops_the_folder_stops_it_on_the_record_it_found_with_no_second_lookup(tmp_path, monkeypatch):
    """``lost_cycle`` finds the folder's record once, before anything else, and a stop it decides on is stored there: a
    second lookup that fails (the folder renamed or gone) must not take the stop with it, here or in the STOP file."""
    shadow = object.__new__(loop.ShadowLoop)
    shadow.root = tmp_path
    found = loop._RECORD.folder(tmp_path)
    lookups = []

    def folder(root):
        lookups.append(root)
        if len(lookups) > 1:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: loop folder cannot be opened safely")
        return found

    monkeypatch.setattr(loop._RECORD, "folder", folder)
    reply = shadow.lost_cycle(ValueError("lost log"), refusal={"said": "G6_E_MODEL_REFUSED: policy"})
    assert reply["warning_card"]["code"] == loop.CANNOT_RECORD
    assert found.cannot_record, "the stop was not kept on the record lost_cycle found"
    assert len(lookups) == 1, f"the stop looked the folder up again: {len(lookups)} lookups"
    assert (tmp_path / envelope.STOP_FILE).exists()


def test_a_tick_whose_close_fails_still_keeps_the_refusal_it_latched(tmp_path, monkeypatch):
    """The refusals follow-up closes a tick in one step that keeps what it latched (``_Requests.close``); the freeze
    follow-up keeps what a cycle met before anything that can fail (``_met``). A close that raises must not take the
    latched refusal with it: the tick leaves by the raise, its way out writes the pause, and a restart is held."""
    from tests.metaharness import test_freeze_holds_review2 as rig

    def refuse(system, user):
        raise LLMError(f"{MODEL_REFUSED}: permission denied")

    shadow, domain = rig.enrolled(tmp_path, proposer=FakeLLM(refuse))

    def close():
        raise OSError("the close was interrupted")

    monkeypatch.setattr(shadow.requests, "close", close)
    with pytest.raises(OSError, match="the close was interrupted"):
        shadow.tick()
    assert shadow._met is not None and shadow._met[1] is not None, "the latched refusal was lost with the close"
    assert shadow.log.paused()
    monkeypatch.setattr(loop, "_RECORD", loop._Record())     # a restart
    restarted = loop.ShadowLoop(domain, shadow.root, rig.SETTINGS, now=lambda: rig.NOW, clock=rig.domains.Clock(),
                                proposer=rig.domains.proposes({"prompt": rig.domains.WORKING}))._refused()
    assert restarted is not None and restarted["warning_card"]["code"] in (loop.PAUSED, envelope.STOPPED), restarted
