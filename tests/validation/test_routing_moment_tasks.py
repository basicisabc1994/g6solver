# SPDX-License-Identifier: Apache-2.0
"""The moment-of-need cohort as pre-registered: the frozen task list and bars, what a person grants before a run, the
guidance each session runs with and records, and the reading of both arms from their kept event logs. No client is
started and no model is called: a fake stands where ``CliAgentRunner`` is."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from g6.mcp import registrations

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", HERE / "score.py")
run = _load("g6_validation_routing_run", HERE / "run.py")
freeze = _load("g6_validation_freeze", ROOT / "validation" / "freeze.py")
RECORDED = registrations.recorded()
SENSITIVE, CONSENT = "G6_E_SENSITIVE_COMPONENT_DENIED", "G6_E_CLOUD_CONSENT_REQUIRED"


def test_the_moment_list_and_bars_are_frozen_and_pinned() -> None:
    tasks, gates = run.load_moment(), run.load_moment_gates()
    pins = json.loads(run.MOMENT_PINS.read_text(encoding="utf-8"))
    assert {key: pins[key] for key in ("tasks_digest", "bars_digest")} == {
        "tasks_digest": freeze.file_digest(run.MOMENT_TASKS), "bars_digest": freeze.file_digest(run.MOMENT_GATES)}
    assert freeze.freeze(run.MOMENT_TASKS, tasks) == pins["tasks_digest"]     # written as freeze writes, unchanged
    assert freeze.freeze(run.MOMENT_GATES, gates) == pins["bars_digest"]
    readme = " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
    assert pins["tasks_digest"] in readme and pins["bars_digest"] in readme
    assert "may tighten" in readme


def test_the_bars_are_the_pre_registered_ones() -> None:
    gates = run.load_moment_gates()
    assert {bar: gates[bar] for bar in score.MOMENT_BARS} == {
        "min_positive_moment_hits": 0.60, "min_positive_lower_bound": 0.35, "max_negative_unnecessary": 0.125,
        "max_malformed": 0.10, "min_missing_tool_recovery": 0.75}
    assert gates["release_rule"] == "at_least_one_client_passes"
    assert gates["comparison"] == "paired_exact_mcnemar_no_claim"


def test_the_list_has_the_registered_shape_and_no_prompt_is_an_instruction() -> None:
    tasks = run.load_moment()
    assert Counter(task["kind"] for task in tasks["tasks"]) == {"before_done": 4, "stuck": 4, "after_refusal": 4,
                                                                "negative": 8, "missing_tool": 4}
    assert {task["cohort"] for task in tasks["tasks"]} == {"moment"}
    prompts = [task["prompt"] for task in tasks["tasks"]]
    assert len(set(prompts)) == len(prompts)
    assert not [p for p in prompts if score.NAMES_A_TOOL.search(p)]
    # and nothing the development cohort asked is asked here
    development = {task["prompt"] for task in json.loads(run.TASKS.read_text(encoding="utf-8"))["tasks"]}
    assert not development & set(prompts)


def test_every_tool_a_task_names_is_registered_or_planned() -> None:
    tasks = run.load_moment()
    assert tasks["planned_tools"] == ["g6_select"]
    known = set(RECORDED["g6"]) | set(tasks["planned_tools"])
    for task in tasks["tasks"]:
        named = [p["tool"] for p in task.get("expected_calls", ())] + list(task.get("withhold", ()))
        named += (task.get("completion") or {}).get("refusal", {}).get("tools", [])
        assert set(named) <= known, task["id"]
        assert all(p.get("server", "g6") == "g6" for p in task.get("expected_calls", ())), task["id"]


def test_each_refusal_is_set_up_by_what_the_session_starts_with() -> None:
    """A sensitive refusal needs a server that does not allow the review; a consent refusal needs one that does, and
    a source the session never starts with. Every session starts with the fixture's grants only."""
    tasks = run.load_moment()
    assert {"component_catalogue", "component_data", "host_configuration", "jobs",
            "completion_history"} <= set(tasks["ungranted"])
    for task in tasks["tasks"]:
        assert task["grants"] == list(score.GRANTS), task["id"]
        if task["kind"] != "after_refusal":
            assert task["permissions"] == ["patch_review.review"], task["id"]
            continue
        codes = task["completion"]["refusal"]["codes"]
        if codes == [SENSITIVE]:
            assert task["permissions"] == [] and "patch_review.review" in task["completion"]["tell_any"], task["id"]
        else:
            assert codes == [CONSENT] and task["permissions"] == ["patch_review.review"], task["id"]
            assert set(task["completion"]["tell_any"]) <= set(tasks["ungranted"]), task["id"]
            assert all(p["tool"] == "g6_cloud_grant" for p in task["expected_calls"]), task["id"]
    kinds = Counter(tuple(t["completion"]["refusal"]["codes"]) for t in tasks["tasks"] if t["kind"] == "after_refusal")
    assert kinds == {(SENSITIVE,): 2, (CONSENT,): 2}


def test_every_fixture_exists_and_is_synthetic() -> None:
    for task in run.load_moment()["tasks"]:
        readme = run.fixture_dir(task["fixture"]) / run.fixture_paths(task)["readme"]
        assert readme.is_file() and "synthetic" in readme.read_text(encoding="utf-8").lower(), task["id"]
    assert run.fixture_dir("ledger") == run.MOMENT_FIXTURES / "ledger" and run.fixture_dir("shop") == run.FIXTURES / "shop"


def test_a_moment_list_with_a_changed_task_is_refused(tmp_path: Path) -> None:
    tasks = run.load_moment()
    fewer = dict(tasks, tasks=tasks["tasks"][:2])
    (tmp_path / "fewer.json").write_text(json.dumps(fewer), encoding="utf-8")
    assert run.load_moment(tmp_path / "fewer.json") == fewer                  # a list may hold fewer tasks
    changed = json.loads(json.dumps(fewer))
    changed["tasks"][0]["prompt"] += " Please."
    (tmp_path / "changed.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(freeze.FrozenError, match="not the frozen"):
        run.load_moment(tmp_path / "changed.json")
    (tmp_path / "header.json").write_text(json.dumps(dict(fewer, ungranted=[])), encoding="utf-8")
    with pytest.raises(freeze.FrozenError, match="not the frozen"):
        run.load_moment(tmp_path / "header.json")


def test_what_a_person_grants_for_the_moment_cohort_is_listed(tmp_path: Path) -> None:
    """Beside the client's environment, each fixture and each workspace, the person grants the three sources a
    review's reply reads, so that a review can answer where the task is not about consent. The catalogue and its data
    are still never granted."""
    tasks, withheld = run.load_moment(), run.load_withheld()
    rows = run.sources(tasks, tmp_path, "codex", "moment")
    assert {row["provider"] for row in rows} == {"openai:codex"}
    named = {row["source"] for row in rows}
    fixtures = {task["fixture"] for task in tasks["tasks"]}
    assert named == {"agent_environment", "completion_history", "host_configuration", "jobs",
                     *(f"routing-fixture.{name}" for name in fixtures),
                     *(row["source"] for row in rows if row["source"].startswith("repository:"))}
    # one workspace folder per fixture, and per task the folder its session's G6 home keeps reviews in, which a
    # review's reply names
    from g6.core import cloud_consent as cloud

    reviews = {cloud.repository_source(run.home_for(tmp_path, f"{task['id']}.0") / run.REVIEWS) for task in tasks["tasks"]}
    assert sum(source.startswith("repository:") for source in named) == len(fixtures) + len(tasks["tasks"])
    assert reviews <= named and len(rows) == 1 + 3 + 2 * len(fixtures) + len(tasks["tasks"])
    assert set(withheld["granted"]) == {"completion_history", "host_configuration", "jobs"}
    assert not named & (set(tasks["ungranted"]) - set(withheld["granted"]))
    assert {"component_catalogue", "component_data"} <= set(tasks["ungranted"]) - named


def test_only_the_consent_refusal_tasks_start_without_the_sources_a_review_reads() -> None:
    """The per-task withheld sources are their own frozen file, pinned beside the list, so the frozen list is
    untouched: exactly the tasks whose refusal under test is the consent refusal withhold the three sources."""
    tasks, withheld = run.load_moment(), run.load_withheld()
    assert run.moment_pins()["withheld_digest"] == freeze.file_digest(run.MOMENT_WITHHELD)
    assert freeze.freeze(run.MOMENT_WITHHELD, withheld) == run.moment_pins()["withheld_digest"]
    readme = " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
    assert run.moment_pins()["withheld_digest"] in readme and "G6_E_ROUTING_NOT_GRANTED" in readme
    consent = sorted(t["id"] for t in tasks["tasks"] if t["kind"] == "after_refusal"
                     and t["completion"]["refusal"]["codes"] == [CONSENT])
    assert consent == ["moment.after_refusal.3", "moment.after_refusal.4"]
    assert withheld["withheld"] == {task_id: sorted(withheld["granted"]) for task_id in consent}
    assert set(withheld["granted"]) <= set(tasks["ungranted"])
    for task_id in consent:
        task = next(t for t in tasks["tasks"] if t["id"] == task_id)
        assert set(task["completion"]["tell_any"]) <= set(withheld["withheld"][task_id])
    # a withheld file that withholds a source the person is not asked to grant, or names no task, is refused
    for bad in (dict(withheld, withheld={"moment.after_refusal.3": ["component_catalogue"]}),
                dict(withheld, withheld={"moment.nowhere.1": ["jobs"]}),
                dict(withheld, granted={**withheld["granted"], "input:x": "an exact input"})):
        with pytest.raises(score.TaskError):
            score.validate_moment_withheld(bad, tasks)


def stand_in_probe(home: Path, workspace: Path, client: str) -> dict[str, Any]:
    """In the place of the runner's review probe (``tests/validation/test_routing_moment_reply.py`` holds the real
    one): a reply that comes back, or, in the home of a task that withholds the three sources, one withheld on them."""
    withheld = run.load_withheld()["withheld"]
    three = next((names for task_id, names in withheld.items() if f"{task_id}." in Path(home).name), [])
    return {"reply": "withheld" if three else "returned", "code": "", "named": list(three), "unshared": list(three)}

# --- a moment arm, run by a fake client --------------------------------------------------------------------------

def _edit_and_review(repo: str) -> str:
    return "\n".join(json.dumps(event) for event in (
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": f"{repo}/x.py", "kind": "update"}]}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "g6", "tool": "g6_review_patch",
                                            "arguments": {"repo": f"/work/{repo}", "task": "the change"},
                                            "status": "completed", "result": {"content": []}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}}, {"type": "turn.completed"}))


QUIET = "\n".join(json.dumps(e) for e in ({"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
                                          {"type": "turn.completed"}))
KEPT = ("moment.before_done.1", "moment.negative.1", "moment.after_refusal.1")


def _fake(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[dict[str, Any]]:
    """A fake in the place of ``CliAgentRunner``: what each session was started with, as the client would see it."""
    from g6.agents._runtime import cli

    seen: list[dict[str, Any]] = []

    class FakeCli(cli.CliAgentRunner):
        def locate(self) -> str:
            return self.program.command

        def require(self, *, stop: Any = None) -> Any:
            return SimpleNamespace(found=True, detail=f"{self.program.command} 0.0.0")

        def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path,
                stop: Any = None) -> Any:
            plugin = Path(argv[argv.index("--plugin-dir") + 1]) if "--plugin-dir" in argv else None
            seen.append({"argv": list(argv), "env": self.environment(scratch), "prompt": prompt, "cwd": cwd,
                         "plugin": sorted(p.relative_to(plugin).as_posix() for p in plugin.rglob("*") if p.is_file())
                         if plugin else None})
            (scratch / cli.OUTPUT_LOG).write_text(answer(prompt), encoding="utf-8")
            return cli.CliRun(argv=tuple(argv), exit_code=0, output_tail="", output_bytes=0, elapsed_s=3.0,
                              cleanup_confirmed=True, env_names=())

    monkeypatch.setattr(cli, "CliAgentRunner", FakeCli)
    return seen


@pytest.fixture()
def arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Three of the frozen moment tasks, a G6 home of the test's own with every grant ``sources`` lists, and a home
    folder whose G6 guidance the record reads."""
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    person = tmp_path / "person"
    (person / ".codex").mkdir(parents=True)
    (person / ".codex" / "AGENTS.md").write_bytes(b"# the person's own notes\n")
    monkeypatch.setattr(run, "user_home", lambda: person)
    monkeypatch.setattr(run, "reply_probe", stand_in_probe)
    tasks = run.load_moment()
    path = tmp_path / "few.json"
    path.write_text(json.dumps(dict(tasks, tasks=[t for t in tasks["tasks"] if t["id"] in KEPT])), encoding="utf-8")
    for out in ("before", "after"):
        for client in ("codex", "claude_code"):
            for row in run.sources(run.load_moment(path), tmp_path / out, client, "moment"):
                assert cloud._grant(row["source"], row["provider"], cloud.preview(row["source"], row["provider"])[1],
                                    "the test's stand-in person") == "granted"
    return SimpleNamespace(path=path, tmp=tmp_path, person=person, by_id={t["id"]: t for t in tasks["tasks"]})


def test_a_claude_code_session_runs_with_the_shipped_plugin_and_may_run_tests(arm: SimpleNamespace,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    done = run.run(arm.path, arm.tmp / "before", client="claude_code", cohort="moment")
    assert len(done) == len(seen) == len(KEPT)
    shipped = sorted(p.relative_to(run.PLUGIN).as_posix() for p in run.PLUGIN.rglob("*") if p.is_file())
    for session in seen:
        argv = session["argv"]
        allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
        denied = argv[argv.index("--disallowedTools") + 1:argv.index("--max-turns")]
        assert set(run.TEST_RUNS) <= set(allowed) and "Bash" not in allowed and "Bash" not in denied
        assert {"WebFetch", "WebSearch", "Task", "Agent", "PowerShell"} <= set(denied)
        assert "--strict-mcp-config" in argv and "--setting-sources" in argv
        # the plugin as this tree ships it: its hooks and skills, and the trial server where its own server was
        assert session["plugin"] == [p for p in shipped if p != ".mcp.json"] and "hooks/hooks.json" in session["plugin"]
        # the plugin's hooks start G6 in the client's environment: they get the session's own G6 home
        home = Path(session["env"]["G6_HOME"])
        assert home.parent == (arm.tmp / "before").resolve() / "g6_homes" and not home.exists()
    rows = [json.loads(line) for line in (arm.tmp / "before" / "transcripts-claude_code-moment.jsonl").read_text(
        encoding="utf-8").splitlines()]
    record = rows[0]["session"]
    assert set(record) == {"run", "bound", "served", "guidance", "final_diff_sha256", "withheld_sources",
                           "reply_probe"}
    assert record["bound"]["tasks"] == run.moment_pins()["tasks_digest"]
    assert record["bound"]["bars"] == run.moment_pins()["bars_digest"]
    guidance = record["guidance"]
    assert set(guidance) == {"workspace_skill", "plugin", "servers", "user"} and guidance["plugin"]
    from g6.core import skills

    assert guidance["workspace_skill"] == freeze.tree_digest(skills.guide_dir())
    assert guidance["user"] == {"~/.claude/skills/using-g6": "", "~/.codex/skills/using-g6": "",
                                "~/.codex/AGENTS.md": hashlib.sha256(b"# the person's own notes\n").hexdigest()}
    assert str(arm.person) not in json.dumps(guidance)                  # digests and labels, no folder of the person's


def test_a_codex_session_gets_the_permissions_its_task_states_and_no_more(arm: SimpleNamespace,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    run.run(arm.path, arm.tmp / "before", client="codex", cohort="moment")
    by_prompt = {task["prompt"]: task for task in arm.by_id.values()}
    for session in seen:
        task = by_prompt[session["prompt"]]
        lead = f"mcp_servers.g6.env.{run.SENSITIVE_ENV}="
        stated = [arg[len(lead):] for arg in session["argv"] if arg.startswith(lead)]
        assert stated == [json.dumps(",".join(task["permissions"]))], task["id"]      # stated even when empty
        assert "--plugin-dir" not in session["argv"] and session["env"]["G6_HOME"]


def test_a_moment_session_starts_only_without_the_sources_it_must_lack(arm: SimpleNamespace,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import cloud_consent as cloud

    assert cloud._grant("component_catalogue", "openai:codex", cloud.preview("component_catalogue", "openai:codex")[1],
                        "the test's stand-in person") == "granted"
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    done = run.run(arm.path, arm.tmp / "before", client="codex", cohort="moment")
    assert seen == [] and {(t.state, t.code) for t in done} == {("refused", run.NOT_FRESH)}


CONSENT_KEPT = ("moment.before_done.1", "moment.after_refusal.3")


def _ledger(home: Path) -> list[dict[str, Any]]:
    """The rows of the sharing decisions in a G6 home, read through G6's own reader with their checkpoint."""
    from g6.core import cloud_consent as cloud
    from g6.core.ledger import ChainLedger

    folder = home / "cloud_consent"
    if not (folder / "events.jsonl").is_file():
        return []
    head = (folder / "head").read_text(encoding="ascii")
    ledger = ChainLedger(folder / "events.jsonl", expect_head=head, validate=cloud._validate)
    assert not ledger.verify()
    return ledger.entries()


def _shared(home: Path, provider: str) -> set[str]:
    from g6.core import cloud_consent as cloud
    from g6.core.ledger import ChainLedger

    rows = _ledger(home)
    if not rows:
        return set()
    folder = home / "cloud_consent"
    ledger = ChainLedger(folder / "events.jsonl", expect_head=(folder / "head").read_text(encoding="ascii"),
                         validate=cloud._validate)
    return {source for (source, given), row in cloud._decisions(ledger).items()
            if given == provider and row["kind"] == "grant"}


def test_a_sessions_copy_of_the_decisions_only_loses_the_sharing_its_task_withholds(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay removes the withheld sources' grants and revokes from the session's copy and nothing else: every
    other row is the person's own, with its own body; nothing is added; the person's decisions are untouched."""
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    for source, provider in (("jobs", "openai:codex"), ("host_configuration", "openai:codex"),
                             ("host_configuration", "anthropic:claude_code"), ("agent_environment", "openai:codex")):
        assert cloud._grant(source, provider, cloud.preview(source, provider)[1], "the stand-in person") == "granted"
    cloud.revoke("jobs", "openai:codex")
    person = (tmp_path / "home" / "cloud_consent" / "events.jsonl").read_bytes()
    rows = _ledger(tmp_path / "home")
    plain = run.trial_home(tmp_path / "out", "moment.before_done.1.0")
    assert _ledger(plain) == rows                                        # nothing withheld: the copy is the person's
    home = run.trial_home(tmp_path / "out", "moment.after_refusal.3.0",
                          withhold=("completion_history", "host_configuration", "jobs"))
    left = _ledger(home)
    gone = [r for r in rows if r["kind"] in ("grant", "revoke")
            and r["body"]["source"] in ("completion_history", "host_configuration", "jobs")]
    assert len(gone) == 4
    assert [(r["kind"], r["id"], r["body"]) for r in left] == [(r["kind"], r["id"], r["body"]) for r in rows
                                                              if r not in gone]
    assert _shared(home, "openai:codex") == {"agent_environment"}
    assert _shared(home, "anthropic:claude_code") == set()
    # completion_history was never granted: withholding it adds nothing, and the person's own file is as it was
    assert (tmp_path / "home" / "cloud_consent" / "events.jsonl").read_bytes() == person
    with pytest.raises(ValueError):
        run.trial_home(tmp_path / "out", "moment.after_refusal.4.0", withhold=("input:" + "0" * 64,))


@pytest.fixture()
def consent_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A non-consent task and a consent-refusal task, with every grant ``sources`` lists given by a stand-in person."""
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(run, "user_home", lambda: tmp_path / "person")
    monkeypatch.setattr(run, "reply_probe", stand_in_probe)
    tasks = run.load_moment()
    path = tmp_path / "consent.json"
    path.write_text(json.dumps(dict(tasks, tasks=[t for t in tasks["tasks"] if t["id"] in CONSENT_KEPT])),
                    encoding="utf-8")
    rows = run.sources(run.load_moment(path), tmp_path / "before", "codex", "moment")
    by_prompt = {task["prompt"]: task["id"] for task in tasks["tasks"]}
    return SimpleNamespace(path=path, tmp=tmp_path, rows=rows, by_prompt=by_prompt,
                           grant=lambda row: cloud._grant(row["source"], row["provider"],
                                                          cloud.preview(row["source"], row["provider"])[1],
                                                          "the test's stand-in person"))


def test_a_consent_task_starts_without_the_three_sources_and_every_other_task_with_them(
        consent_arm: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    for row in consent_arm.rows:
        assert consent_arm.grant(row) == "granted"
    person = (consent_arm.tmp / "home" / "cloud_consent" / "events.jsonl").read_bytes()
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    shared: dict[str, set[str]] = {}
    from g6.agents._runtime import cli

    fake = cli.CliAgentRunner

    class Looking(fake):                                   # the fake above, reading the copy while its session runs
        def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path,
                stop: Any = None) -> Any:
            shared[consent_arm.by_prompt[prompt]] = _shared(Path(self.environment(scratch)["G6_HOME"]), "openai:codex")
            return super().run(argv, prompt=prompt, cwd=cwd, seconds=seconds, scratch=scratch, stop=stop)

    monkeypatch.setattr(cli, "CliAgentRunner", Looking)
    done = run.run(consent_arm.path, consent_arm.tmp / "before", client="codex", cohort="moment")
    assert len(seen) == 2
    assert {(t.task_id, t.state) for t in done} == {(task_id, "completed") for task_id in CONSENT_KEPT}
    three = {"completion_history", "host_configuration", "jobs"}
    assert three <= shared["moment.before_done.1"]
    assert not three & shared["moment.after_refusal.3"]
    assert shared["moment.before_done.1"] - three == shared["moment.after_refusal.3"]
    rows = {r["task_id"]: r for r in map(json.loads, (consent_arm.tmp / "before" / "transcripts-codex-moment.jsonl")
                                         .read_text(encoding="utf-8").splitlines())}
    assert rows["moment.before_done.1"]["session"]["withheld_sources"] == []
    assert rows["moment.after_refusal.3"]["session"]["withheld_sources"] == sorted(three)
    assert (consent_arm.tmp / "home" / "cloud_consent" / "events.jsonl").read_bytes() == person
    # a row whose record does not name the sources its task withholds is no evidence
    log = consent_arm.tmp / "before" / "transcripts-codex-moment.jsonl"
    changed = [dict(r, session=dict(r["session"], withheld_sources=[])) for r in rows.values()]
    log.write_text("".join(json.dumps(r) + "\n" for r in changed), encoding="utf-8")
    assert set(run.moment_reading(consent_arm.tmp / "before", client="codex")["evidence"]["unevidenced"]) == {
        "moment.after_refusal.3"}


def test_a_task_that_needs_a_source_the_person_has_not_granted_is_refused_before_it_starts(
        consent_arm: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    for row in consent_arm.rows:
        if row["source"] != "jobs":
            assert consent_arm.grant(row) == "granted"
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    done = {t.task_id: t for t in run.run(consent_arm.path, consent_arm.tmp / "before", client="codex",
                                          cohort="moment")}
    assert (done["moment.before_done.1"].state, done["moment.before_done.1"].code) == ("refused", run.NOT_GRANTED)
    assert done["moment.after_refusal.3"].state == "completed"          # it starts without jobs anyway
    assert [consent_arm.by_prompt[s["prompt"]] for s in seen] == ["moment.after_refusal.3"]


def test_a_changed_fixture_byte_refuses_a_moment_run_before_anything_starts(
        arm: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = run.load_moment()
    used = {task["fixture"] for task in tasks["tasks"]}
    assert run.fixtures_digest() == freeze.digest({name: freeze.tree_digest(run.fixture_dir(name))
                                                   for name in sorted(used)})
    assert run.moment_fixtures() == run.moment_pins()["fixtures_digest"]
    assert run.moment_pins()["fixtures_digest"] in " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
    shared, own = tmp_path / "fixtures", tmp_path / "moment_fixtures"
    shutil.copytree(run.FIXTURES, shared, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(run.MOMENT_FIXTURES, own, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(run, "FIXTURES", shared)
    monkeypatch.setattr(run, "MOMENT_FIXTURES", own)
    assert run.moment_fixtures() == run.moment_pins()["fixtures_digest"]  # copies of the same bytes: the same pin
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    # Every active fixture is bound, including the isolated and renamed audit copies.
    for task in tasks["tasks"]:
        roles = run.fixture_paths(task)
        changed = run.fixture_dir(task["fixture"]) / roles.get("source", roles["readme"])
        data = changed.read_bytes()
        changed.write_bytes(data + b" ")
        with pytest.raises(freeze.FrozenError, match="fixtures"):
            run.run(arm.path, arm.tmp / "before", client="codex", cohort="moment")
        assert seen == [] and not (arm.tmp / "before" / "transcripts-codex-moment.jsonl").exists()
        changed.write_bytes(data)


def test_both_arms_are_read_again_from_their_kept_logs_and_paired(arm: SimpleNamespace,
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    fixture = {task["prompt"]: task["fixture"] for task in arm.by_id.values()}
    _fake(monkeypatch, lambda prompt: QUIET)
    run.run(arm.path, arm.tmp / "before", client="codex", cohort="moment")
    _fake(monkeypatch, lambda prompt: _edit_and_review(fixture[prompt]))
    run.run(arm.path, arm.tmp / "after", client="codex", cohort="moment")
    before = run.moment_reading(arm.tmp / "before", client="codex")
    after = run.moment_reading(arm.tmp / "after", client="codex")
    for reading in (before, after):
        assert reading["evidence"]["rederived"] == len(KEPT) and reading["evidence"]["unevidenced"] == {}
        assert reading["overall"]["scheduled"] == 24 and reading["overall"]["unavailable"] == 24 - len(KEPT)
        assert reading["arm"]["g6"] == [run.package_digest()] and len(reading["arm"]["guidance"]) == 1
        assert reading["decision"] == "fail"                          # 21 tasks were not run: full denominators
    row = {r["task_id"]: r for r in after["tasks"]}
    assert row["moment.before_done.1"]["moment_hit"] and not row["moment.negative.1"]["quiet"]
    found = run.compare(arm.tmp / "before", arm.tmp / "after", client="codex")
    pair = {p["task_id"]: p for p in found["pairs"]}
    assert pair["moment.before_done.1"] == {"task_id": "moment.before_done.1", "kind": "before_done", "before": False,
                                            "after": True}
    assert pair["moment.negative.1"]["before"] and not pair["moment.negative.1"]["after"]
    assert found["claim"] is None and found["arms"]["before"]["g6"] == found["arms"]["after"]["g6"]
    assert run.main(["compare", "--client", "codex", "--out", str(arm.tmp / "before"), "--out",
                     str(arm.tmp / "after")]) == 0
    assert json.loads(capsys.readouterr().out)["claim"] is None
    assert run.main(["score", "--client", "codex", "--cohort", "moment", "--out", str(arm.tmp / "after")]) == 0
    written = json.loads((arm.tmp / "after" / "report-codex-moment.json").read_text(encoding="utf-8"))
    assert written == after
    assert run.release([arm.tmp / "before"], cohort="moment")["clients"] == {"codex": "fail"}


def test_a_moment_row_is_read_on_the_list_and_bars_it_ran_on(arm: SimpleNamespace,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, lambda prompt: QUIET)
    out = arm.tmp / "before"
    run.run(arm.path, out, client="codex", cohort="moment")
    log = out / "transcripts-codex-moment.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def write(changed: list[dict[str, Any]]) -> None:
        log.write_text("".join(json.dumps(r) + "\n" for r in changed), encoding="utf-8")

    for name in ("tasks", "bars", "task"):
        write([dict(r, session=dict(r["session"], bound=dict(r["session"]["bound"], **{name: "0" * 64}))) for r in rows])
        with pytest.raises(freeze.FrozenError, match="ran on another"):
            run.moment_reading(out, client="codex")
    write([dict(r, session=dict(r["session"], bound={k: v for k, v in r["session"]["bound"].items() if k != "g6"}))
           for r in rows])
    assert set(run.moment_reading(out, client="codex")["evidence"]["unevidenced"]) == set(KEPT)
    write([dict(r, session=dict(r["session"], served={"g6": {"g6": "0" * 64, "serves": True}})) for r in rows])
    assert set(run.moment_reading(out, client="codex")["evidence"]["unevidenced"]) == set(KEPT)
    # another g6 package than this one's is no fault: the arms of a comparison ran on two trees
    other = "1" * 64
    write([dict(r, session=dict(r["session"], bound=dict(r["session"]["bound"], g6=other),
                                served={"g6": {"g6": other, "serves": True}})) for r in rows])
    reading = run.moment_reading(out, client="codex")
    assert reading["evidence"]["unevidenced"] == {} and reading["arm"]["g6"] == [other]
    assert re.fullmatch(r"[0-9a-f]{64}", run.package_digest())
