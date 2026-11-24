# SPDX-License-Identifier: Apache-2.0
"""Review 2 of wire-typed-fu-2 (tip 634e31b8): a write from an operation no longer active that still replaces records.

The change's rule: a write from an operation no longer active may only add sends, and never removes or replaces reviews,
debt, rows or retry reasons. It holds while the retained state can be read. When it cannot (``write_retained`` replaces
the content before its digest, so a failed or interrupted second replace leaves exactly this), ``_load`` answers with a
made-up refusal state, and the expired writer publishes THAT over the run's bytes, with a fresh digest and token.
"""
import contextvars
import json

import pytest

from g6.core import cloud_consent as cloud
from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop

STATE = {"attempted": 1, "reviews": [{"patch": "kept"}], "row_due": "the owed row", "row": "why it is still owed"}


@pytest.fixture
def expired(tmp_path):
    """A run with retained work, and the copied context of its ended operation (a late worker's)."""
    session = tmp_path / "run"
    state = json.loads(json.dumps(STATE))
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        loop._save(session, state, sources=())
        copied = contextvars.copy_context()
    return session, copied


def _torn(session):
    """The content replaced and its digest not: what an interrupted ``write_retained`` leaves (its docstring says so)."""
    body = json.loads((session / "state.json").read_bytes())
    body["reviews"].append({"patch": "newer"})
    (session / "state.json").write_bytes(json.dumps(body).encode("utf-8"))
    return (session / "state.json").read_bytes()


def test_control_expired_reservation_keeps_readable_work(expired):
    session, copied = expired
    copied.run(note_model_call)
    retained = loop._load(session)
    assert retained["attempted"] == 2
    assert all(retained[key] == STATE[key] for key in ("reviews", "row_due", "row"))


def test_control_readers_withhold_a_torn_state_and_leave_its_bytes(expired):
    session, _ = expired
    torn = _torn(session)
    assert loop._load(session)["code"] == cloud.REQUIRED
    assert (session / "state.json").read_bytes() == torn


def test_expired_reservation_does_not_replace_a_state_it_cannot_read(expired):
    """FAILS at the tip: the late worker's reservation writes the made-up refusal state over the run's own bytes (reviews,
    the owed row and its retry reason, and the count itself are gone), and the request is let go with no count kept."""
    session, copied = expired
    torn = _torn(session)
    try:
        copied.run(note_model_call)
    except Exception:  # noqa: BLE001 - refusing the send is a fine answer; only the retained bytes are judged
        pass
    after = (session / "state.json").read_bytes()
    assert after == torn, json.loads(after)


def test_expired_save_does_not_replace_a_state_it_cannot_read(expired):
    """FAILS at the tip: the same through ``_save`` from the expired context (the stale-snapshot path of this change)."""
    session, copied = expired
    torn = _torn(session)
    stale = json.loads(json.dumps(STATE))
    try:
        copied.run(loop._save, session, stale, sources=())
    except Exception:  # noqa: BLE001
        pass
    after = (session / "state.json").read_bytes()
    assert after == torn, json.loads(after)
