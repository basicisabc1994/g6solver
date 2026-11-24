# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for review 3 of wire-typed-fu-2 (tip ad9e6b4d). Storage, locks and git stay real; only the scouts,
the reviewer and the patch review are stood in, as tests/harness/test_completion_review2_on_disk.disk_run does."""
import json
import os

from g6.core.model_calls import note_model_call
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness.workflow import policy

TASK = "double returns twice its argument"


def left_out(where):
    return {"left_out": "OSError: host could not launch", "where": str(where), "at_base": None, "refused": []}


def quiet_scouts(monkeypatch, *, sends=False):
    """Scouts that leave every set out; with ``sends`` each first reserves one request, as a real host call does."""
    def scout(change, task, text, where, host, deadline):
        if sends:
            note_model_call()
        return left_out(where)

    monkeypatch.setattr(loop, "_scout", scout)


def clean_reviewer(monkeypatch, key_file):
    """The review-until-clean loop with a reviewer that reads the patch once and names no defect."""
    key_file("")
    monkeypatch.setattr(loop, "critic_host", lambda: object())

    def review_round(change, task, critic, shipped, session, reviews, ends):
        note_model_call()
        patch = loop.repo_tests.diff_read(change).text
        reviews.append({"verdict": "none", "rule": "", "why": "", "test": "", "reviewer": "stand-in",
                        "n": len(reviews) + 1, "patch": loop.digest(patch), "file": loop.critic_file(len(reviews) + 1),
                        "results": {}, "failed": {}})
        return set(), ""

    monkeypatch.setattr(loop, "review_round", review_round)


def ended_clean(repo, base, run, *, owed):
    """A run that ended clean on the patch as it stands; with ``owed`` its row is still to be delivered."""
    row = json.dumps({"session": run.name, "cause": "review_found_nothing"})
    state = {"attempted": 1, "stop": "clean", **({"row_due": row} if owed else {})}
    with loop._writing(run):
        loop._finish(run, state, "CLEAN REPORT of the first patch", "", "clean", patch=loop._patch_now(repo, base))


def hard_link(tmp_path, target):
    """A second name at ``target`` for a file kept elsewhere."""
    victim = tmp_path / f"victim-{target.name}"
    victim.write_bytes(b"keep")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(victim, target)
    return victim


def hook_session(monkeypatch, repo, name):
    """A Stop-hook session bound to ``repo``, with the optional workflow record off: its event and its run folder."""
    monkeypatch.setattr(policy, "enabled", lambda: (False, ""))
    session = hook.folder(name)
    hook._record(session, str(repo), TASK)
    return {"session_id": name, "cwd": str(repo), "last_assistant_message": "done"}, hook.current(name, hook._task(session))


def log_rows():
    log = loop.store.home_path(*loop.FAILURES)
    return log.read_text(encoding="utf-8") if log.is_file() else ""
