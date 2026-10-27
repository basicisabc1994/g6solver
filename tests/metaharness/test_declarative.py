# SPDX-License-Identifier: Apache-2.0
import json

from g6.metaharness.surface import Surface


def spec(name="tiny"):
    return {"name": name, "task": "Return a label for {input}",
            "cases": [{"input": f"case {i}", "expected": f"label {i}"} for i in range(6)],
            "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"}}


def test_start_persists_data_only_workflow_and_reloads_without_registration(tmp_path):
    surface = Surface(workflow_root=tmp_path)
    got = surface.start(spec())
    assert got["completion_state"] == "qualified-draft"
    assert got["mode"] == "explore" and got["held_out_cases"] < 30
    assert got["workflow_digest"]
    stored = json.loads((tmp_path / "tiny" / "workflow.json").read_text())
    assert stored["name"] == "tiny"
    again = Surface(workflow_root=tmp_path)
    harness = again.harness("tiny")
    assert harness.workflow.name == "tiny"
    assert harness.store.confirmed() is None
    assert harness.workflow.policy.min_cases == 30


def test_workflow_creation_refuses_paths_code_escape_and_replacement(tmp_path):
    surface = Surface(workflow_root=tmp_path)
    for name in ("../escape", "/absolute"):
        assert surface.start(spec(name))["completion_state"] == "blocked-escalated"
    bad = spec()
    bad["baseline_code"]["../evaluator.py"] = "pass"
    assert surface.start(bad)["completion_state"] == "blocked-escalated"
    surface.start(spec())
    assert surface.start(spec())["completion_state"] == "blocked-escalated"


def test_code_proposals_keep_hypothesis_and_cannot_edit_the_evaluator(tmp_path):
    surface = Surface(workflow_root=tmp_path)
    surface.start(spec())
    harness = surface.harness("tiny")
    harness.confirm("Ada")
    proposed = surface.propose_code("tiny", {"main.py": "def solve(task):\n    return 'new'\n"},
                                    hypothesis="the old constant misses all categories", session="builder")
    assert proposed["proposal_digest"]
    assert proposed["proposal"]["claim"]["hypothesis"] == "the old constant misses all categories"
    refused = surface.propose_code("tiny", {"../gate.py": "pass"}, hypothesis="cheat", session="builder")
    assert refused["completion_state"] == "blocked-escalated"


def test_confirmation_is_bound_to_workflow_digest(tmp_path):
    surface = Surface(workflow_root=tmp_path)
    start = surface.start(spec())
    refused = surface.confirm("tiny", "wrong", answer="Ada")
    assert refused["completion_state"] == "blocked-escalated"
    assert surface.harness("tiny").store.confirmed() is None
    assert surface.confirm("tiny", start["workflow_digest"], answer=None)["completion_state"] == "blocked-escalated"
    assert surface.confirm("tiny", start["workflow_digest"], answer="Ada")["completion_state"] == "qualified-draft"


def test_baseline_cannot_gain_unregistered_files(tmp_path):
    surface = Surface(workflow_root=tmp_path)
    surface.start(spec())
    (tmp_path / "tiny" / "code" / "helper.py").write_text("pass")
    assert surface.harness("tiny")["completion_state"] == "blocked-escalated"


def test_portable_filenames_and_empty_contains_oracle_are_rejected(tmp_path):
    surface = Surface(workflow_root=tmp_path)
    bad = spec()
    bad["baseline_code"]["MAIN.py"] = "pass"
    assert surface.start(bad)["completion_state"] == "blocked-escalated"
    bad = spec()
    bad["check"] = "contains"
    bad["cases"][0]["expected"] = ""
    assert surface.start(bad)["completion_state"] == "blocked-escalated"


def test_declarative_evaluator_is_in_frozen_judging_manifest():
    from g6.metaharness.api import JUDGING_PATTERNS
    from pathlib import PurePath
    assert any(PurePath("metaharness/declarative.py").match(pattern) for pattern in JUDGING_PATTERNS)


def test_global_discovery_and_project_precedence(tmp_path, monkeypatch):
    from g6.metaharness.declarative import create
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    create(spec(), tmp_path / "home" / "workflows")
    surface = Surface(workflow_root=tmp_path / "project")
    assert surface.harness("tiny").workflow.name == "tiny"
    local = spec()
    local["task"] = "Local labels for {input}"
    surface.start(local)
    assert surface.harness("tiny").workflow.task == local["task"]


def test_generated_application_is_measured_but_small_split_cannot_activate(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_ALLOW_UNCONFINED", "1")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    surface = Surface(workflow_root=tmp_path / "workflows")
    created = surface.start(spec())
    surface.confirm("tiny", created["workflow_digest"], answer="Ada")
    candidate = surface.propose_code("tiny", {"main.py":
        "def solve(task):\n    return 'label ' + task.rsplit(' ', 1)[-1]\n"},
        hypothesis="Extracting the case number replaces the incorrect constant", session="builder")
    evaluated = surface.evaluate("tiny", candidate["proposal_digest"])
    assert "report_digest" in evaluated, evaluated
    assert evaluated["report"]["success"]["wins"] == 5, evaluated["report"]["cases"]
    decision = surface.decide("tiny", evaluated["report_digest"], candidate["proposal_digest"])
    assert decision["decision"]["verdict"] != "recommend"
    denied = surface.approve("tiny", decision["decision_digest"], session="builder", answer="Ada")
    assert denied["completion_state"] == "blocked-escalated"
    assert len(surface.harness("tiny").store.versions()) == 1
