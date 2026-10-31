# SPDX-License-Identifier: Apache-2.0
"""The Claude Code hooks that drive the completion loop, and the plugin that carries them. ``advance`` is a stand-in here
(``test_completion_loop`` tests it), so nothing runs but the hooks, and no model is called."""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import g6
from g6.cli.setup import SERVER_ARGS
from g6.harness import completion_hook as hook
from g6.harness import completion_loop as loop

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "g6"
STATUS = ("Early alpha. Individual capabilities have been tested; general application-level improvements have not yet "
          "been established.")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    return root


def hooked(monkeypatch, capsys, name: str, event: dict) -> tuple[int, str, str]:
    """``name``'s hook on ``event``, read as Claude Code writes it: UTF-8 on standard input, whatever the console's code."""
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(event, ensure_ascii=False).encode("utf-8")), encoding="ascii")
    monkeypatch.setattr(sys, "stdin", stdin)
    code = hook.main([name])
    out, err = capsys.readouterr()
    return code, out, err


def prompt(repo: Path, text: str = "fix the parser", session: str = "s1", cwd: Path | None = None) -> dict:
    return {"session_id": session, "cwd": str(cwd or repo), "prompt": text, "hook_event_name": "UserPromptSubmit"}


def stop(repo: Path, session: str = "s1") -> dict:
    return {"session_id": session, "cwd": str(repo), "hook_event_name": "Stop", "stop_hook_active": False}


def recorded(session: str = "s1") -> dict:
    return json.loads((hook.folder(session) / "task.json").read_text(encoding="utf-8"))


def test_the_first_prompt_is_the_task_and_head_then_the_base(repo, monkeypatch, capsys):
    code, out, _ = hooked(monkeypatch, capsys, "prompt", prompt(repo, "fix the parser: é"))
    assert (code, out) == (0, "")      # anything printed here would be added to the agent's context
    assert recorded() == {"repo": str(repo.resolve()), "task": "fix the parser: é", "base": git(repo, "rev-parse", "HEAD")}


def test_prompts_before_the_loop_starts_add_to_the_task_and_never_move_the_base(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo, "look at the parser"))
    before = recorded()
    (repo / "new.txt").write_text("n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "later")
    hooked(monkeypatch, capsys, "prompt", prompt(repo, "now fix its bug"))
    assert recorded() == {**before, "task": "look at the parser\n\nnow fix its bug"}


def test_once_the_loop_has_begun_testing_a_prompt_changes_nothing(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo, "the task"))
    before = recorded()
    (hook.folder("s1") / "state.json").write_text('{"started": 1}', encoding="utf-8")
    hooked(monkeypatch, capsys, "prompt", prompt(repo, "thanks"))
    assert recorded() == before


def test_the_first_prompt_runs_the_scouts_and_the_agent_starts_with_what_they_found(repo, monkeypatch, capsys):
    calls = []

    def prepare(repo_path, task, base, session):
        calls.append((repo_path, task, base, session))
        return "WHAT THE AGENT READS FIRST"

    monkeypatch.setattr(loop, "prepare", prepare)
    code, out, _ = hooked(monkeypatch, capsys, "prompt", prompt(repo, "fix it"))
    assert code == 0
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                      "additionalContext": "WHAT THE AGENT READS FIRST"}}
    assert calls == [(str(repo.resolve()), "fix it", git(repo, "rev-parse", "HEAD"), hook.folder("s1"))]
    hooked(monkeypatch, capsys, "prompt", prompt(repo, "and more"))
    assert len(calls) == 1      # the first task's scouts only


def test_scouts_with_nothing_to_say_leave_the_prompt_alone(repo, monkeypatch, capsys):
    monkeypatch.setattr(loop, "prepare", lambda *args: "")
    assert hooked(monkeypatch, capsys, "prompt", prompt(repo))[:2] == (0, "")


def test_a_failure_before_the_fix_never_holds_the_agents_turn_and_the_task_is_kept(repo, monkeypatch, capsys):
    def prepare(*args):
        raise RuntimeError("the scouts broke")

    monkeypatch.setattr(loop, "prepare", prepare)
    code, out, err = hooked(monkeypatch, capsys, "prompt", prompt(repo, "fix it"))
    assert (code, out) == (1, "") and "the scouts broke" in err      # exit 2 would block the prompt, and this never does
    assert recorded()["task"] == "fix it"


def test_a_task_whose_prompt_was_missed_is_read_from_the_transcript_at_the_stop(repo, monkeypatch, capsys, tmp_path):
    lines = [{"type": "user", "message": {"role": "user", "content": "fix the parser"}},
             {"type": "assistant", "message": {"content": [{"type": "text", "text": "not the person"}]}},
             {"type": "user", "isMeta": True, "message": {"content": "not the person either"}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "content": "a tool's output"}]}},
             {"type": "user", "message": {"content": [{"type": "text", "text": "and the lexer"}]}}]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n", encoding="utf-8")
    calls = advance_says(monkeypatch, loop.Step(loop.STOP, "THE REPORT"))
    code, out, _ = hooked(monkeypatch, capsys, "stop", {**stop(repo), "transcript_path": str(transcript)})
    assert code == 0 and json.loads(out) == {"systemMessage": "THE REPORT"}
    assert calls[0][1] == "fix the parser\n\nand the lexer" and calls[0][2] == git(repo, "rev-parse", "HEAD")


def test_a_transcript_that_cannot_be_read_leaves_no_task_for_the_loop_to_report(repo, monkeypatch, capsys):
    calls = advance_says(monkeypatch, loop.Step(loop.STOP))
    hooked(monkeypatch, capsys, "stop", {**stop(repo), "transcript_path": str(repo / "absent.jsonl")})
    assert calls[0][1] == ""


def test_a_session_started_in_a_subfolder_is_the_repositorys(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo, cwd=repo / "pkg"))
    assert recorded()["repo"] == str(repo.resolve())


@pytest.mark.parametrize("session", ["s1", "../../outside", "a/b", "C:\\x"])
def test_a_sessions_folder_is_always_inside_gs_own(repo, session):
    assert hook.folder(session).parent == hook.folder("other").parent


def test_outside_a_repository_nothing_is_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    (tmp_path / "plain").mkdir()
    code, out, _ = hooked(monkeypatch, capsys, "prompt", prompt(tmp_path / "plain"))
    assert (code, out) == (0, "")
    assert not (hook.folder("s1") / "task.json").exists()


def test_a_repository_with_no_commit_records_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    (tmp_path / "empty").mkdir()
    git(tmp_path / "empty", "init", "-q")
    assert hooked(monkeypatch, capsys, "prompt", prompt(tmp_path / "empty"))[0] == 0
    assert not (hook.folder("s1") / "task.json").exists()


def advance_says(monkeypatch, step) -> list:
    calls = []

    def advance(repo, task, base, session):
        calls.append((repo, task, base, session))
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(loop, "advance", advance)
    return calls


def test_stop_outside_a_repository_does_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    (tmp_path / "plain").mkdir()
    calls = advance_says(monkeypatch, loop.Step(loop.STOP))
    assert hooked(monkeypatch, capsys, "stop", stop(tmp_path / "plain"))[:2] == (0, "")
    assert not calls


def test_stop_blocks_the_turn_with_the_repair_text(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    calls = advance_says(monkeypatch, loop.Step(loop.CONTINUE, "repair this: é"))
    code, out, _ = hooked(monkeypatch, capsys, "stop", stop(repo))
    assert code == 0
    assert json.loads(out) == {"decision": "block", "reason": "repair this: é"}
    assert calls == [(str(repo.resolve()), "fix the parser", git(repo, "rev-parse", "HEAD"), hook.folder("s1"))]


def test_when_the_stop_rule_fires_the_turn_ends_and_the_report_is_shown(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    advance_says(monkeypatch, loop.Step(loop.STOP, "THE REPORT"))
    code, out, _ = hooked(monkeypatch, capsys, "stop", stop(repo))
    assert code == 0
    assert json.loads(out) == {"systemMessage": "THE REPORT"}


def test_with_nothing_to_say_the_turn_just_ends(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    advance_says(monkeypatch, loop.Step(loop.STOP))
    assert hooked(monkeypatch, capsys, "stop", stop(repo))[:2] == (0, "")


def test_a_failure_of_the_loop_never_holds_the_turn(repo, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    advance_says(monkeypatch, RuntimeError("it broke"))
    code, out, err = hooked(monkeypatch, capsys, "stop", stop(repo))
    assert (code, out) == (1, "")      # any exit but 2 ends the turn, and Claude Code shows the first line
    assert "it broke" in err.splitlines()[0] and "not tested" in err.splitlines()[0]


def test_unreadable_input_never_holds_the_turn(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"not json")))
    code = hook.main(["stop"])
    assert code == 1 and capsys.readouterr().out == ""


def test_inside_a_scout_session_the_hooks_do_nothing(repo, monkeypatch, capsys):
    monkeypatch.setenv(loop.CHILD, "1")
    calls = advance_says(monkeypatch, loop.Step(loop.CONTINUE, "no"))
    assert hooked(monkeypatch, capsys, "prompt", prompt(repo))[:2] == (0, "")
    assert hooked(monkeypatch, capsys, "stop", stop(repo))[:2] == (0, "")
    assert not calls and not (hook.folder("s1") / "task.json").exists()


def test_the_hook_runs_as_claude_code_starts_it(repo, tmp_path):
    """Its own process, from the repository's folder, with a module there named like one G6 imports: -P keeps it off."""
    (repo / "json.py").write_text("raise SystemExit('the project replaced a module in the hook')\n", encoding="utf-8")
    [[command]] = [[one["hooks"][0]] for one in hooks()["Stop"]]
    event = json.dumps({"session_id": "fresh", "cwd": str(repo)})
    env = {**subprocess.os.environ, "G6_HOME": str(tmp_path / "g6"), "PYTHONPATH": str(ROOT / "src")}
    got = subprocess.run([sys.executable, *command["args"]], input=event, capture_output=True, text=True, cwd=repo, env=env,
                         timeout=60)
    assert got.returncode == 0, got.stderr
    assert "did not run" in json.loads(got.stdout)["systemMessage"]      # json.py is a change, and no task was seen


def plugin_json(*parts: str) -> dict:
    return json.loads(PLUGIN.joinpath(*parts).read_text(encoding="utf-8"))


def hooks() -> dict:
    return plugin_json("hooks", "hooks.json")["hooks"]


def test_the_manifest_names_the_plugin_and_this_version():
    manifest = plugin_json(".claude-plugin", "plugin.json")
    assert manifest["name"] == "g6" and manifest["version"] == g6.__version__
    assert manifest["author"] == {"name": "The G6 authors"} and manifest["license"] == "Apache-2.0"


def test_the_repository_is_a_marketplace_for_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    [entry] = market["plugins"]
    assert entry["name"] == "g6" and entry["source"] == "./plugins/g6"
    assert (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").is_file()
    assert market["owner"]["name"]


def test_the_hooks_are_exec_form_with_no_shell_so_they_read_the_same_on_windows_and_linux():
    events = hooks()
    assert set(events) == {"UserPromptSubmit", "Stop"}
    for event, name in (("UserPromptSubmit", "prompt"), ("Stop", "stop")):
        [[one]] = [entry["hooks"] for entry in events[event]]
        assert (one["type"], one["command"]) == ("command", "python")
        assert one["args"] == ["-P", "-m", "g6.harness.completion_hook", name]
        assert name in hook.HOOKS


def test_the_stop_hooks_timeout_leaves_room_for_what_follows_the_scouts():
    """A hook that times out loses its report, so the last scout's end, the critic and the review must fit, with 15
    minutes for the probes' runs."""
    [[one]] = [entry["hooks"] for entry in hooks()["Stop"]]
    assert one["timeout"] >= loop.STOP_S + 60
    assert loop.STOP_S >= loop.SCOUTS_S + loop.CRITIC_SECONDS + loop.REVIEW_S + 300


def test_the_prompt_hooks_timeout_outlasts_the_scouts_window():
    [[one]] = [entry["hooks"] for entry in hooks()["UserPromptSubmit"]]
    assert one["timeout"] >= loop.SCOUTS_S + 60


def test_the_server_starts_as_g6_setup_registers_it():
    server = plugin_json(".mcp.json")["mcpServers"]["g6"]
    assert server["command"] == "python" and tuple(server["args"]) == SERVER_ARGS


def test_installing_the_plugin_allows_no_sensitive_operation_and_the_docs_say_how_to():
    assert "G6_MCP_SENSITIVE" not in PLUGIN.joinpath(".mcp.json").read_text(encoding="utf-8")
    assert "env" not in plugin_json(".mcp.json")["mcpServers"]["g6"]
    text = " ".join((PLUGIN / "README.md").read_text(encoding="utf-8").split())
    assert "installing the plugin allows nothing" in text
    assert "G6_MCP_SENSITIVE=patch_review.review" in text and "g6 setup --client claude --allow patch_review.review" in text


@pytest.mark.parametrize("name", ["README.md", "skills/completion-loop/SKILL.md"])
def test_the_docs_say_which_loop_has_measurements_behind_it(name):
    text = " ".join((PLUGIN / name).read_text(encoding="utf-8").split())
    assert "G6_LOOP_SCOUTS=before_fix" in text
    assert "the configuration with measurements behind it" in text and "a different, cheaper loop" in text


def test_a_prompt_hook_says_nothing_of_scouts_where_none_may_run():
    [[one]] = [entry["hooks"] for entry in hooks()["UserPromptSubmit"]]
    assert "scout" not in one["statusMessage"].lower()      # it shows at every prompt, and by default none runs there


def test_the_skill_can_be_found_and_says_what_a_repair_message_is():
    text = (PLUGIN / "skills" / "completion-loop" / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---", 2)[1]
    assert re.search(r"^name: completion-loop$", head, re.M) and re.search(r"^description: .{40,}", head, re.M)
    assert "one careful reader's view" in text


@pytest.mark.parametrize("line", [
    "a convenience, not a security boundary",
    "by accident or habit",
    "not against code running as the same user",
    STATUS,
])
def test_the_docs_state_the_limits_plainly(line):
    assert " ".join((PLUGIN / "README.md").read_text(encoding="utf-8").split()).count(line) >= 1


def test_the_plugins_text_makes_no_claim_it_may_not_and_names_no_private_path():
    for path in [*PLUGIN.rglob("*"), ROOT / ".claude-plugin" / "marketplace.json"]:
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            for word in ("experimental", "proved", "proven"):
                assert word not in text, f"{path.name}: {word}"
            assert not re.search(r"\bverified\b", text), path.name
            assert not re.search(r"[a-z]:\\users\\|/home/\w+/|/users/\w+/", text), path.name
