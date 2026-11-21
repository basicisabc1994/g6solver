"""Reproductions for review 4. All execution state is held in memory."""
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.harness import outcome_history as h, resolution
from g6.harness import verification_search as vs, verification_templates as vt
from g6.harness.behaviour_runner import ExecutableSource
from g6.workforce import receipt_store
from tests.harness.test_proposed_checks import result

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


def test_retained_differential_resolution_keeps_template_semantics(monkeypatch):
    memory_stores(monkeypatch)
    data = {
        "function": ["module", "calc", "edge"], "mode": "outputs",
        "calls": [{"args": [1], "kwargs": {}}],
    }
    instance = vt.TemplateInstance(
        id="keep-edge", requirement_id="R", intent="preserve",
        template="differential.v1",
        bindings={"function": "calc.edge", "mode": "outputs",
                  "calls": [{"args": [1]}]},
    )
    one, placed, origin = vt.compiled_check(Path.cwd(), instance, data, [])
    change = SimpleNamespace(repo=Path.cwd(), files=[], sources=(),
                             base_commit="b" * 40)

    def run(change, placed, source, node, *, at_base, **kwargs):
        observation = {"input": {"args": [1], "kwargs": {}},
                       "output": 1 if at_base else 2}
        raw = {
            "verdict": "passed", "said": "", "seconds": 0.0,
            "source_before": digest({"base": at_base}),
            "isolation": {"backend": "synthetic-review-fixture"},
            "g6_observation": {
                "result": "passed", "detail": "observed",
                "observations": [observation],
            },
        }
        # Custodied checks use the owned runner. The ordinary free-form replay
        # emits the same observation, as the portable reproduction does.
        if type(source) is ExecutableSource:
            raw["receipt"] = receipt_store._record_observation(
                change.repo, node, source, raw)
            return raw
        return {**result([node], output="G6-OBSERVATION " + json.dumps(observation)),
                "completed": True}

    monkeypatch.setattr(vs, "_run_check", run)
    first = vs.search(
        change, [one], {one.id: placed}, deadline=vs._clock() + 60,
        artifact="a" * 64, origins={one.id: origin},
    )
    assert first.activities[0]["result"] == "counterexample"
    for check, node, patch, base, classification, label, differs in first.outcomes:
        h.record_check(change, check, node, patch, base, classification,
                       origin=label, refuted_by_difference=differs)
    history = h.read(change.repo)
    assert len(history.rows) == len(history.retained) == 1, history

    retained, origins, skipped = h.retained_checks(change, "R")
    assert len(retained) == 1 and not skipped
    repeated = vs.search(
        change, retained, vs.placements(change.repo, retained),
        deadline=vs._clock() + 60, artifact="a" * 64, origins=origins,
    )
    assert repeated.activities[0]["result"] == "counterexample"
    args = resolution.build(
        assessment_for(repeated.defects), repo=str(change.repo), task="Keep edge"
    )[0]["cheapest_check"]["arguments"]

    if "template_instances" in args:
        replayed = vt.template_instances(args["template_instances"], {"R"})
        check, target, replay_origin = vt.compiled_check(
            change.repo, replayed[0], data, [])
        replay_origins = {check.id: replay_origin}
    else:
        check = vs.proposed_checks(args["proposed_checks"], {"R"})[0]
        target, replay_origins = vs.placement(check), {}
    final = vs.search(
        change, [check], {check.id: target}, deadline=vs._clock() + 60,
        artifact="a" * 64, origins=replay_origins,
    )
    observed = final.activities[0]["result"]
    assert observed == "counterexample", observed
    # The instance survives retention as recorded, and a retained re-run's own record keeps it again.
    assert args["template_instances"][0]["bindings"] == instance.bindings
    for check, node, patch, base, classification, label, differs in repeated.outcomes:
        assert check._instance == origin.instance


@pytest.mark.parametrize("instance", [
    {"template": "relation.v1", "requirement_id": "R", "intent": "preserve", "bindings": {"b": 1}},
    None,   # a row retained before instances were kept, or a free-form check: it replays as its text
], ids=["relation", "free-form"])
def test_a_retained_row_restores_the_instance_it_kept(monkeypatch, instance):
    source = "def test_kept():\n    assert True\n"
    row = {"classification": "defect_detected", "finding": "R", "modules": [],
           "check": {"file": "test_kept.py", "source": source, "test": "test_kept",
                     "sha256": digest(source), **({"template_instance": instance} if instance else {})}}
    monkeypatch.setattr(h, "read", lambda root: h.History(REPOSITORY, (row,)))
    checks, origins, skipped = h.retained_checks(SimpleNamespace(repo=Path.cwd(), files=[]), "R")
    assert len(checks) == 1 and not skipped
    assert checks[0]._instance == instance and origins[checks[0].id].instance == instance
    assert origins[checks[0].id].template == ""   # only the differential rule changes how a retained run is judged


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
