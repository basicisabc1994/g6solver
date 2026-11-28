# SPDX-License-Identifier: Apache-2.0
"""Select task drafts are cue-free and complete, while remaining ineligible to run."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "validation/routing"
spec = importlib.util.spec_from_file_location("routing_select_draft_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)


def draft():
    return run.freeze.read(HERE / "amendments/2026-11-17/select_tasks.unbound.json")


def test_select_drafts_cover_both_cohorts_and_clients_without_cues():
    data = draft()
    assert data["routes"] == {"select": None}
    assert data["binding_status"] == "unbound"
    assert len(data["cases"]) == 9
    seen = set()
    missing = []
    for case in data["cases"]:
        task = case["task"]
        assert task["route"] == "select"
        assert not run.score.NAMES_A_TOOL.search(task["prompt"])
        assert case["source_ids"] and case["rationale"]
        assert all(re.fullmatch(r"S(?:[1-9]|1[0-3])", source) for source in case["source_ids"])
        for file in run.fixture_dir(task["fixture"]).rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts:
                assert not re.search(r"\b(?:g6|routing|trials?|measurements?)\b", file.read_text(encoding="utf-8"), re.I)
        # A current digest is used in memory only to validate task shape, never written to the draft.
        run.score.validate_tasks({"format": run.score.FORMAT_TASKS,
                                  "routes": {"select": run.route_digests()["select"]}, "tasks": [task]})
        if task["polarity"] == "missing_tool":
            missing.append(case)
            assert task["withhold"] == ["g6_select"]
            assert case["clients"] == ["claude_code", "codex"]
        else:
            assert len(case["clients"]) == 1
            seen.add((task["cohort"], case["clients"][0], task["polarity"]))
            if task["polarity"] == "positive":
                assert task["expected_calls"][0]["tool"] == "g6_select"
                assert "task" in task["expected_calls"][0]["args_present"]
            else:
                assert task["forbidden"] == "any"
    assert seen == {(cohort, client, polarity) for cohort in ("development", "final")
                    for client in ("claude_code", "codex") for polarity in ("positive", "negative")}
    assert len(missing) == 1


def test_unbound_select_drafts_cannot_be_admitted_as_a_task_list():
    data = draft()
    with pytest.raises(run.score.TaskError, match="sha256"):
        run.score.validate_tasks({"format": run.score.FORMAT_TASKS, "routes": data["routes"],
                                  "tasks": [case["task"] for case in data["cases"]]})
