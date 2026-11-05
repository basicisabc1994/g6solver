# SPDX-License-Identifier: Apache-2.0
"""A refusal reads the patch it ends on after its failure row is written (review-kfu4, finding 4). Reviewers are recorded,
and no test of a reviewer's ever runs."""
import json

import pytest

from g6.harness import completion_hook, completion_loop as loop
from g6.harness import verification_proposer as proposer
from g6.mcp import review_loop
from tests.harness.test_completion_loop_followup import rig as source_rig  # noqa: F401 - isolated, Git-free fixture


@pytest.fixture
def rig(source_rig):  # noqa: F811 - the fixture it builds on
    # As review-kfu4's probes: the session's own folders made first, with inherited access.
    source_rig.session.mkdir(parents=True)
    source_rig.home.joinpath(*loop.OWING).mkdir(parents=True)
    completion_hook.folder("mcp:probe-repeat").mkdir(parents=True)
    return source_rig


def test_finding_4_a_refusals_ending_is_read_after_its_row_is_written(rig, monkeypatch):
    monkeypatch.setattr(proposer, "boundary", lambda _: "boundary unavailable")
    original_write = loop._write_row
    edited = False

    def write_with_concurrent_edit(session, state):
        nonlocal edited
        original_write(session, state)
        if not edited:
            # Deterministic interleaving: an editor changes the repository while this
            # refusal finishes writing its failure row, before the session is done.
            rig.source.write_bytes(b"value = 99\n")
            edited = True

    monkeypatch.setattr(loop, "_write_row", write_with_concurrent_edit)
    args = (str(rig.repo), "Keep value one", "probe-repeat")
    first = review_loop.step(*args, base=rig.base)
    assert edited and not rig.calls and first["ending"] == "untested"
    assert rig.source.read_bytes() == b"value = 99\n"
    # No file changes between the end of the first call and this retry.
    repeated = review_loop.step(*args, base=rig.base)
    assert repeated["report"] == first["report"], "unchanged ending patch was falsely reported as an earlier patch"
    assert repeated["note"] == loop.ALREADY_ENDED


def test_a_refusal_cut_short_before_its_end_ends_on_the_patch_as_it_stands_when_it_ends(rig, monkeypatch):
    monkeypatch.setattr(proposer, "boundary", lambda _: "boundary unavailable")
    real = loop._finish

    def killed(*args, **kwargs):
        raise KeyboardInterrupt("the hook was stopped after the row, before the end was kept")

    monkeypatch.setattr(loop, "_finish", killed)
    args = (str(rig.repo), "Keep value one", "probe-repeat")
    with pytest.raises(KeyboardInterrupt):
        review_loop.step(*args, base=rig.base)
    rig.source.write_bytes(b"value = 7\n")
    monkeypatch.setattr(loop, "_finish", real)
    first = review_loop.step(*args, base=rig.base)
    repeated = review_loop.step(*args, base=rig.base)
    assert first["ending"] == "untested" and repeated["report"] == first["report"]
    assert repeated["note"] == loop.ALREADY_ENDED and review_loop.CHANGED not in repeated["report"]
    rows = (rig.home / "completion_loop" / "failures.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["cause"] == loop.UNCONFINED
    rig.source.write_bytes(b"value = 8\n")
    assert review_loop.CHANGED in review_loop.step(*args, base=rig.base)["report"]
