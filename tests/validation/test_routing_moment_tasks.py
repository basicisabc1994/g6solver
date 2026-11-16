# SPDX-License-Identifier: Apache-2.0
"""The moment-of-need cohort as pre-registered: the frozen task list and bars, what a person grants before a run, the
guidance each session runs with and records, and the reading of both arms from their kept event logs. No client is
started and no model is called: a fake stands where ``CliAgentRunner`` is."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
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
    assert pins == {"tasks_digest": freeze.file_digest(run.MOMENT_TASKS), "bars_digest": freeze.file_digest(run.MOMENT_GATES)}
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
        readme = run.fixture_dir(task["fixture"]) / "README.md"
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
    tasks = run.load_moment()
    rows = run.sources(tasks, tmp_path, "codex", "moment")
    assert {row["provider"] for row in rows} == {"openai:codex"}
    named = {row["source"] for row in rows}
    fixtures = {task["fixture"] for task in tasks["tasks"]}
    assert named == {"agent_environment", *(f"routing-fixture.{name}" for name in fixtures),
                     *(row["source"] for row in rows if row["source"].startswith("repository:"))}
    assert sum(source.startswith("repository:") for source in named) == len(fixtures)
    assert not named & set(tasks["ungranted"])


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
    assert set(record) == {"run", "bound", "served", "guidance", "final_diff_sha256"}
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

    assert cloud._grant("host_configuration", "openai:codex", cloud.preview("host_configuration", "openai:codex")[1],
                        "the test's stand-in person") == "granted"
    seen = _fake(monkeypatch, lambda prompt: QUIET)
    done = run.run(arm.path, arm.tmp / "before", client="codex", cohort="moment")
    assert seen == [] and {(t.state, t.code) for t in done} == {("refused", run.NOT_FRESH)}


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
