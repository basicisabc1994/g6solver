"""Reproductions for review 4. All execution state is held in memory."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger
from g6.harness import outcome_history as h
from g6.harness import verification_search as vs
from g6.workforce import receipt_store

REPOSITORY = "c" * 64


def memory_stores(monkeypatch):
    stores = {}
    receipt_path = SimpleNamespace(is_file=lambda: True)

    def ledger(path):
        return stores.setdefault(str(path), ChainLedger())

    monkeypatch.setattr(receipt_store, "ChainLedger", ledger)
    monkeypatch.setattr(h, "ChainLedger", ledger)
    monkeypatch.setattr(receipt_store, "_locked", lambda path: nullcontext())
    monkeypatch.setattr(receipt_store, "store_path",
                        lambda: receipt_path)
    monkeypatch.setattr(receipt_store, "state_path",
                        lambda *parts: Path("review-state").joinpath(*parts))
    monkeypatch.setattr(h, "state_path",
                        lambda *parts: Path("review-state").joinpath(*parts))
    monkeypatch.setattr(receipt_store, "repository_id", lambda root: REPOSITORY)
    monkeypatch.setattr(h, "repository_id", lambda root: REPOSITORY)


def assessment_for(defects):
    return {
        "scope": {"base_commit": "b" * 40, "import_roots": ["lib"]},
        "requirements": [
            {"id": "R", "text": "Keep edge behavior", "checks": [],
             "status": "passed_on_stated_checks"}
        ],
        "completion": {"defects_reproduced": {"proposed_checks": defects}},
    }


@pytest.mark.parametrize("params,allowed,answer,expected", [
    ('{"repo":"fixture","approved":true}', True, "accept", "G6_E_INPUT_VALIDATION"),
    ('{"repo":[]}', True, "accept", "G6_E_INPUT_VALIDATION"),
    ('{"repo":"fixture"}', False, "accept", "G6_E_SENSITIVE_COMPONENT_DENIED"),
    ('{"repo":"fixture"}', True, "decline", "declined"),
    ('{"repo":"fixture"}', True, "cancel", "cancelled"),
], ids=["caller-consent", "malformed-repo", "sensitive-denial", "decline", "cancel"])
def test_forget_controls_remain_closed(monkeypatch, params, allowed, answer, expected):
    import asyncio
    from g6.mcp import learning_history as adapter

    monkeypatch.setattr(
        adapter, "_read", lambda repo: (Path.cwd(), h.History(REPOSITORY), "d" * 64))
    monkeypatch.setattr(
        h, "forget", lambda *args, **kwargs: pytest.fail("unauthorized deletion"))

    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action=answer, content={"value": "Fixture person"})

    ctx = SimpleNamespace(session=Session(), request_id="review-fixture")
    reply = asyncio.run(adapter.forget_reply(
        ctx, params,
        allow=frozenset({"learning_history.forget"}) if allowed else frozenset()))
    assert reply["ok"] is False
    assert expected in reply.get("error", "") or reply.get("consent_status") == expected


@pytest.mark.parametrize("reading,expected", [
    ({"result": "passed", "verdict": "passed"}, "pass"),
    ({"result": "failed", "verdict": "failed", "collection": False}, "defect"),
    ({"result": "failed", "verdict": "failed", "collection": True}, "check_error"),
    ({"result": "failed", "verdict": "collection_error"}, "check_error"),
    ({"result": "error", "verdict": "passed"}, "check_error"),
    ({"result": "failed", "verdict": "passed"}, "check_error"),
])
def test_evidence_classes_stay_conservative(reading, expected):
    assert vs.evidence_class(reading) == expected


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows UNC path")
def test_nonsensitive_history_list_rejects_unc_before_filesystem_access(monkeypatch):
    from g6.core.registry import ComponentRegistry

    network_path = "\\\\review-host.invalid\\share\\repo"
    touched = []
    path_type = type(Path())
    original = path_type.is_dir

    def is_dir(path):
        if str(path) == network_path:
            # Stand in for the OS probe; never actually contact the share.
            touched.append(str(path))
            return False
        return original(path)

    monkeypatch.setattr(path_type, "is_dir", is_dir)
    reply = ComponentRegistry().invoke(
        "learning_history", "list", {"repo": network_path}, block_sensitive=True)
    assert reply.__class__.__name__ == "Failure"
    assert not touched, ("nonsensitive operation probed a network path", touched)


def test_history_change_during_dialog_blocks_deletion(monkeypatch):
    import asyncio
    from g6.mcp import learning_history as adapter

    state = {"digest": "before", "deleted": False}
    root = Path.cwd()
    monkeypatch.setattr(
        adapter, "_read", lambda repo: (root, h.History(REPOSITORY), "before"))
    monkeypatch.setattr(h, "repository_id", lambda repo: REPOSITORY)
    monkeypatch.setattr(h, "fingerprint", lambda repo: state["digest"])
    monkeypatch.setattr(
        h, "store_path", lambda repo: SimpleNamespace(
            unlink=lambda **kwargs: state.update(deleted=True)))
    monkeypatch.setattr(receipt_store, "_locked", lambda path: nullcontext())

    class Session:
        async def elicit_form(self, **kwargs):
            state["digest"] = "changed while open"
            return SimpleNamespace(action="accept", content={"value": "Fixture person"})

    ctx = SimpleNamespace(session=Session(), request_id="review-race")
    reply = asyncio.run(adapter.forget_reply(
        ctx, '{"repo":"fixture"}', allow=frozenset({"learning_history.forget"})))
    assert reply["ok"] is False and not state["deleted"]
    assert "history unavailable or changed" in reply["error"]


#: Network and device path forms, refused on their words before any file system call (``storage._elsewhere``).
ELSEWHERE = ["\\\\review-host.invalid\\share\\repo", "//review-host.invalid/share/repo", "\\\\?\\C:\\repo",
             "\\\\.\\pipe\\repo", "\\??\\C:\\repo"] if __import__("os").name == "nt" else [
             "//review-host.invalid/share/repo"]


def _no_filesystem(monkeypatch):
    def probe(*args, **kwargs):
        raise AssertionError(f"a network or device path reached the file system: {args!r}")

    path_type = type(Path())
    for name in ("is_dir", "exists", "resolve", "stat", "is_file"):
        monkeypatch.setattr(path_type, name, probe)


@pytest.mark.parametrize("path", ELSEWHERE)
def test_checked_repo_refuses_a_network_or_device_path_before_any_filesystem_call(monkeypatch, path):
    """``checked_repo`` is the one gate of every op that takes a repository: learning_history list and forget,
    g6_review_patch, the review loop, the K loop, g6_confirm_behaviour, missions, the proposer's context, the workflow
    record, swe_diagnostics.patch_check, the agent_change bundle and the algorithm mission."""
    from g6.workforce import repo_tests

    _no_filesystem(monkeypatch)
    with pytest.raises(repo_tests.RepoRefused) as refused:
        repo_tests.checked_repo(path)
    assert refused.value.code == "G6_E_INPUT_VALIDATION"


@pytest.mark.parametrize("path", ELSEWHERE)
def test_history_list_refuses_a_network_or_device_path_before_any_filesystem_call(monkeypatch, path):
    from g6.core.registry import ComponentRegistry

    _no_filesystem(monkeypatch)
    reply = ComponentRegistry().invoke("learning_history", "list", {"repo": path}, block_sensitive=True)
    assert reply.__class__.__name__ == "Failure", reply
    assert "G6_E_INPUT_VALIDATION" in str(reply.failure()), reply


@pytest.mark.parametrize("path", ELSEWHERE)
def test_the_completion_hook_never_asks_git_about_a_network_or_device_folder(monkeypatch, path):
    from g6.harness import completion_hook
    from g6.workforce import repo_tests

    monkeypatch.setattr(repo_tests, "_git", lambda *a, **kw: pytest.fail("git ran on a network or device folder"))
    assert completion_hook._here(path) is None
