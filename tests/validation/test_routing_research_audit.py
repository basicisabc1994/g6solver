# SPDX-License-Identifier: Apache-2.0
"""Pre-arm research amendment: offline logs and registration integrity."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


m = _load("routing_audit_logs", HERE / "test_routing_moment.py")
score, run = m.score, m.run


@pytest.mark.parametrize("client", m.CLIENTS)
def test_refusal_select_then_remedy_then_retry(client):
    refusal = ("call", "g6_review_patch", m.REVIEW, m.WITHHELD)
    select = ("call", "g6_select", {"task": "allow reply", "last_outcome": "G6_E_CLOUD_CONSENT_REQUIRED"}, m.STARTED)
    remedy = ("call", "g6_cloud_grant", {"source": "host_configuration", "provider": "openai:codex"}, m.GRANTED)
    retry = ("call", "g6_review_patch", m.REVIEW, m.STARTED)
    assert m.judged(client, m.WITHHOLDING, [refusal, select, remedy, retry])["moment_hit"]
    assert not m.judged(client, m.WITHHOLDING, [refusal, retry, remedy])["moment_hit"]
    wrong = ("call", "g6_select", {"task": "allow reply", "last_outcome": "G6_E_OTHER"}, m.STARTED)
    assert not m.judged(client, m.WITHHOLDING, [refusal, wrong, remedy])["moment_hit"]
    failed = (*remedy[:3], m.WITHHELD)
    assert not m.judged(client, m.WITHHOLDING, [refusal, failed, retry])["moment_hit"]
    cancelled = (*remedy[:3], json.dumps({"status": "cancelled"}))
    assert not m.judged(client, m.WITHHOLDING, [refusal, cancelled, retry])["moment_hit"]
    assert not m.judged(client, m.WITHHOLDING, [refusal, cancelled])["moment_hit"]
    envelope = (*remedy[:3], json.dumps({"ok": True, "result": {"status": "granted"},
        "completion_state": "qualified-draft", "warning_card": {"code": "G6_E_CLOUD_CONSENT"}}))
    assert m.judged(client, m.WITHHOLDING, [refusal, select, envelope, retry])["moment_hit"]


@pytest.mark.parametrize("client", m.CLIENTS)
def test_refusal_escalation_requires_an_action_in_the_same_sentence(client):
    refusal = ("call", "g6_review_patch", m.REVIEW, m.WITHHELD)
    assert not m.judged(client, m.WITHHOLDING, [refusal], "host_configuration.")["moment_hit"]
    assert not m.judged(client, m.WITHHOLDING, [refusal], "Please allow it. host_configuration.")["moment_hit"]
    assert m.judged(client, m.WITHHOLDING, [refusal], "Please share host_configuration.")["moment_hit"]


@pytest.mark.parametrize("client", m.CLIENTS)
def test_stuck_counts_only_failures_after_first_python_edit(client):
    call = ("call", "g6_premortem", {"repo": "/work/shop", "task": "totals"}, m.STARTED)
    prefix = [("test", True), ("edit", "README.md"), ("test", True), ("edit", "shop/prices.py")]
    one = m.judged(client, m.STUCK, [*prefix, ("test", True), call])
    assert not one["moment_reached"] and one["failed_test_runs"] == 1
    two = m.judged(client, m.STUCK, [*prefix, ("test", True), ("test", True), call])
    assert two["moment_reached"] and two["moment_hit"] and two["failed_test_runs"] == 2


@pytest.mark.parametrize("client", m.CLIENTS)
@pytest.mark.parametrize("steps,text,attempted,recovered", [
    ([], "Done.", False, False),
    ([], "g6_review_patch could help. Another package is missing.", True, False),
    ([], "g6_review_patch is not available here.", True, True),
    ([], "g6_review_patch is not\navailable here.", True, True),
    ([], "g6_review_patch could help\n\nAnother package is missing", True, False),
    ([], "g6_review_patch_extra is absent.", False, False),
    ([("call", "g6_help", {}, m.STARTED)], "Done.", True, False),
    ([("call", "g6_find", {"query": "review"}, m.STARTED)], "Done.", True, True),
    ([("call", "g6_review_patch", m.REVIEW, m.STARTED)], "Done.", True, False),
])
def test_missing_attempt_and_sentence_local_absence(client, steps, text, attempted, recovered):
    row = m.judged(client, m.MISSING, steps, text)
    assert row.get("attempted") is attempted
    assert row["recovered"] is recovered


def test_attempted_is_beside_recovered_and_bar_reads_every_task():
    tasks = m.cohort()
    report = score.score_moment(tasks, [], m.GATES, client="codex")
    assert report["overall"]["missing_tool"].get("attempted") == 0
    assert report["overall"]["missing_tool"]["tasks"] == 4
    assert report["gates"]["min_missing_tool_recovery"]["value"] == 0.0


@pytest.mark.parametrize("word", ["review", "second opinion", "check my work", "independent", "verify", "audit",
                                  "get an independent opinion on the change before you tell me it is done",
                                  "get a second, independent opinion on it"])
def test_prompt_filter_rejects_paraphrases_and_old_wording(word):
    assert score.NAMES_A_TOOL.search(word)


def test_both_actual_old_refusal_prompts_are_flagged_and_revisions_are_clean():
    old = run.freeze.read(run.HERE / "moment_tasks.retired-1976b25f8d88.json")["retired"]["tasks"]
    current = {t["id"]: t for t in run.load_moment()["tasks"]}
    for task in old:
        if task["id"] in ("moment.after_refusal.3", "moment.after_refusal.4"):
            assert score.NAMES_A_TOOL.search(task["prompt"])
            assert not score.NAMES_A_TOOL.search(current[task["id"]]["prompt"])


@pytest.mark.parametrize("program", ["python", "python3", "py"])
def test_test_command_forms_are_allowed_and_read(program):
    assert f"Bash({program} -m pytest:*)" in run.TEST_RUNS
    assert run.TEST_COMMAND.search(f"{program} -m pytest tests/test_one.py")


def test_moment_fixtures_have_no_cues_and_do_not_share_development():
    tasks = run.load_moment()["tasks"]
    development = {t["fixture"] for t in run.freeze.read(run.TASKS)["tasks"]}
    assert not development & {t["fixture"] for t in tasks}
    cues = re.compile(r"\b(?:g6|routing|trials?|measurements?)\b", re.I)
    for task in tasks:
        for path in run.fixture_dir(task["fixture"]).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                assert not cues.search(path.read_text(encoding="utf-8")), (task["id"], path.name)


def test_development_fixture_binding_uses_its_frozen_inventory():
    assert set(run.development_fixtures()) == {"shop", "classify", "notes", "rules"}
    assert all(path.parent == run.FIXTURES for path in run.development_fixtures().values())


def test_research_provenance_and_isolated_stuck_tests():
    tasks = {t["id"]: t for t in run.load_moment()["tasks"]}
    for task in tasks.values():
        assert "source_ids" in task
        assert all(re.fullmatch(r"S(?:[1-9]|1[0-3])", s) for s in task["source_ids"])
        assert task["source_ids"] or task["kind"] == "negative"
    for n in (3, 4):
        task = tasks[f"moment.stuck.{n}"]
        fixture = run.fixture_dir(task["fixture"])
        visible = Path(run.fixture_paths(task)["tests"])
        file = visible.name
        assert [p.name for p in (fixture / visible.parent).glob("test_*.py")] == [file]
        check = (run.OUTCOME / f"moment.stuck.{n}" / "test_outcome.py").read_text(encoding="utf-8")
        text = (fixture / visible).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() in check


@pytest.mark.parametrize("sources", [[{}], [[]]])
def test_malformed_provenance_is_a_task_error(sources):
    tasks = m.cohort()
    tasks["tasks"][0]["source_ids"] = sources
    with pytest.raises(score.TaskError, match="source_ids"):
        score.validate_moment_tasks(tasks)


def test_dated_amendment_binds_all_pins_and_preserves_bars():
    amendment = run.freeze.read(run.HERE / "moment_amendment_2026-11-17.json")
    assert amendment["before_any_arm"] is True
    assert amendment["date"] == "2026-11-17"
    assert set(amendment["reasons"]) == {"research audit", "lead scoring rulings", "reply delivery merged"}
    assert amendment["pins"] == run.moment_pins()
    assert amendment["previous_pins"]["bars_digest"] == run.moment_pins()["bars_digest"]
    assert amendment["previous_pins"]["outcome_bars_digest"] == run.moment_pins()["outcome_bars_digest"]
    assert run.outcome_checks() == amendment["pins"]["checks_digest"]
    assert run.moment_fixtures() == amendment["pins"]["fixtures_digest"]
    run.load_withheld()
