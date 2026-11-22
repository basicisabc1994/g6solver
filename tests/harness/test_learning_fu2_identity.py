"""Retained records own their origin, even when callers reuse ids or bytes."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.harness import outcome_history as h, verification_search as vs


@pytest.mark.parametrize("kind", ["stated", "free-form", "generic-template", "formal", "symbolic", "multi-step"])
@pytest.mark.parametrize("collision", ["id", "same-bytes", "modified-retained"])
def test_each_retained_kind_rejects_caller_identity(monkeypatch, kind, collision):
    source = "def test_kept():\n    assert True\n"
    template = {"template": {"formal": "state_invariant.v1", "multi-step": "access_rule.v1"}.get(kind, "relation.v1"),
                "requirement_id": "R", "intent": "preserve", "bindings": {}}
    check = {"file": "test_kept.py", "source": source, "sha256": digest(source), "test": "test_kept",
             "intent": "preserve"}
    if kind in {"generic-template", "formal", "symbolic", "multi-step"}:
        check["template_instance"] = template
    if kind in {"formal", "symbolic", "multi-step"}:
        check["judging"] = {"judge": {"call": "g6.harness.verification_formal._judge", "args": [1]},
                            "patch_only": True, "keep_observations": True}
    if kind == "symbolic":
        check["observation"] = {"input": 7, "output": False}
    row = {"classification": "counterexample", "finding": "R", "modules": [], "check": check}
    monkeypatch.setattr(h, "read", lambda root: h.History("c" * 64, (row,)))
    change = SimpleNamespace(repo=Path.cwd(), files=[])
    [retained], origins, skipped = h.retained_checks(change, "R")
    assert not skipped
    assert vs.not_run([retained], {retained.id: vs.placement(retained)}, "fixture", origins).activities[0]["origin"] == "retained"
    if collision == "modified-retained":
        retained.source = "def test_kept():\n    assert False\n"
        caller = retained
    else:
        caller = vs.proposed_checks([{**retained.model_dump(), "source": source if collision == "same-bytes"
                                    else "def test_kept():\n    assert False\n"}], {"R"})[0]
        assert caller._instance is None and caller._judging is None
    with pytest.raises(ValueError, match="retained"):
        vs.not_run([caller], {caller.id: vs.placement(caller)}, "fixture", origins)


def test_duplicate_ids_are_refused_before_placement():
    [one] = vs.proposed_checks([{"id": "retained-fixture", "requirement_id": "R", "intent": "preserve",
                                 "file": "test_kept.py", "source": "def test_kept(): pass"}], {"R"})
    with pytest.raises(ValueError, match="unique"):
        vs.placements(Path.cwd(), [one, one.model_copy()])
