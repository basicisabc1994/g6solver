# SPDX-License-Identifier: Apache-2.0
"""The worked examples' scripts (validation/worked_examples, PLAN.md), with fakes only: no network, no Docker, no model,
no key."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from worked_examples import run as we
from worked_examples import selection

c = we.c
PLAN = Path(we.__file__).with_name("PLAN.md")
KEY = "sk-ant-api03-" + "FAKE" * 6   # the same fake value, written so the export scan reads no key


# --- the selection rule ------------------------------------------------------------------------------------------------
def test_project_rules_keep_and_drop_by_the_plan():
    top = ["boto3", "attrs", "requests"]
    kept = selection.project_rules("python-attrs/attrs", {"stargazers_count": 5800, "license": {"spdx_id": "MIT"},
                                                          "full_name": "python-attrs/attrs"}, top)
    assert kept["kept"] and kept["P1"] and kept["pypi_rank"] == 2
    swe = selection.project_rules("psf/requests", {"stargazers_count": 50000, "license": {"spdx_id": "Apache-2.0"}},
                                  top)
    assert not swe["kept"] and "SWE-bench" in swe["why"]
    noassert = selection.project_rules("networkx/networkx", {"stargazers_count": 16000,
                                                             "license": {"spdx_id": "NOASSERTION"}}, top)
    assert not noassert["kept"] and "NOASSERTION" in noassert["why"]
    few = selection.project_rules("marshmallow-code/marshmallow", {"stargazers_count": 7000,
                                                                   "license": {"spdx_id": "MIT"}}, top)
    assert not few["kept"] and few["why"].startswith("P1")
    moved = selection.project_rules("encode/starlette", {"stargazers_count": 12000, "license": {"spdx_id": "BSD-3-Clause"},
                                                         "full_name": "Kludex/starlette"}, top)
    assert moved["kept"] and moved["full_name"] == "Kludex/starlette"


@pytest.mark.parametrize("path,test", [("tests/test_x.py", True), ("src/a/test_b.py", True), ("a/b_test.py", True),
                                       ("tests/data/cases/x.py", True), ("conftest.py", True),
                                       ("src/black/trans.py", False), ("tests/README.md", False),
                                       ("testing/helpers.py", False)])
def test_is_test(path, test):
    assert selection.is_test(path) is test


def test_t3_needs_source_and_test_and_no_other_language():
    assert selection.t3(["src/a.py", "tests/test_a.py", "CHANGES.md"]) == (True, "")
    assert not selection.t3(["tests/test_a.py", "docs/x.md"])[0]
    assert not selection.t3(["src/a.py", "docs/x.md"])[0]
    assert "another language" in selection.t3(["src/a.py", "src/a.c", "tests/test_a.py"])[1]


def test_t1_window_and_branch():
    assert selection.t1("2026-06-01T00:00:00Z", "main", "main")[0]
    assert selection.t1("2026-10-29T23:00:00Z", "main", "main")[0]
    assert not selection.t1("2026-05-31T23:59:59Z", "main", "main")[0]
    assert not selection.t1("2026-10-30T00:00:01Z", "main", "main")[0]
    assert not selection.t1("2026-07-01T00:00:00Z", "8.x", "main")[0]


def test_t4_parent_of_merge_and_of_squash():
    assert selection.t4({"parents": [{"sha": "a"}, {"sha": "b"}]}, ["x.py"]) == ("a", "")
    one = {"parents": [{"sha": "p"}], "files": [{"filename": "x.py"}, {"filename": "tests/test_x.py"}]}
    assert selection.t4(one, ["tests/test_x.py", "x.py"]) == ("p", "")
    assert selection.t4(one, ["x.py"])[0] is None


# --- prices, the tally and the stop --------------------------------------------------------------------------------------
def test_usage_cost_at_published_prices():
    one_m = 1_000_000
    assert c.usage_cost({"input_tokens": one_m}) == pytest.approx(2.00)
    assert c.usage_cost({"output_tokens": one_m}) == pytest.approx(10.00)
    assert c.usage_cost({"cache_read_input_tokens": one_m}) == pytest.approx(0.10)
    # a write whose duration is not given is charged at the 1-hour price
    assert c.usage_cost({"cache_creation_input_tokens": one_m}) == pytest.approx(4.00)
    split = {"cache_creation_input_tokens": one_m,
             "cache_creation": {"ephemeral_5m_input_tokens": one_m, "ephemeral_1h_input_tokens": 0}}
    assert c.usage_cost(split) == pytest.approx(2.50)
    assert c.usage_cost({"output_tokens": one_m}, "claude-haiku-4-5") == pytest.approx(5.00)
    assert c.usage_cost({"output_tokens": one_m}, "some-new-model") == pytest.approx(50.00)


def test_tally_takes_the_larger_count():
    t = c.Tally()
    t.feed({"type": "assistant", "message": {"id": "m1", "model": c.MODEL, "usage": {"output_tokens": 100_000}}})
    t.feed({"type": "assistant", "message": {"id": "m1", "model": c.MODEL, "usage": {"output_tokens": 100_000}}})
    assert t.model_calls == 1 and t.stream_usd == pytest.approx(1.0)
    t.feed({"type": "result", "modelUsage": {c.MODEL: {"outputTokens": 100_000},
                                             "claude-haiku-4-5": {"outputTokens": 100_000}}})
    assert t.cost_usd == pytest.approx(1.5)


def test_the_stop_counts_unended_runs_at_their_whole_stop():
    rows = [{"event": "started", "run": "run/a/1", "kind": "run"},
            {"event": "ended", "run": "run/a/1", "cost_usd": 4.0},
            {"event": "started", "run": "run/b/1", "kind": "run"}]
    assert c.spent(rows) == pytest.approx(4.0 + c.RUN_STOP_USD)
    assert c.may_start(89.5)[0] and not c.may_start(89.6)[0]


def test_redactor_masks_the_key():
    r = c.Redactor(KEY)
    assert KEY not in r(f"x {KEY} y") and r.leaks(KEY)


# --- the image and the agent's command -----------------------------------------------------------------------------------
TASK = we.Task(id="black-5634", project="psf/black", full_name="psf/black", licence="MIT", pr=5634, issue=496,
               merge_commit="m" * 40, parent="p" * 40, merged_at="2026-10-28T19:18:12Z",
               test_modules=("tests/test_black.py",), install=(".venv/bin/pip install -q -e .",),
               env={"SETUPTOOLS_SCM_PRETEND_VERSION": "26.10.0"})


def test_base_dockerfile_installs_g6_as_the_readme_says():
    text = we.base_dockerfile()
    assert "cd /opt/g6src && python3 -m venv --copies .venv" in text
    assert '/opt/g6src/.venv/bin/pip install -q --no-cache-dir ".[all]"' in text and "FROM g6-a2-linux" in text
    assert we.G6PY == "/opt/g6src/.venv/bin/python3"
    assert "round2_g6" not in text
    r2 = we.base_dockerfile(we.ROUND2)
    change = r2.index("round2_g6.py /opt/g6src/src/g6/harness &&")
    assert change < r2.index('pip install -q --no-cache-dir ".[all]"')    # made to the source G6 is installed from
    assert we.base_tag("a" * 40, we.ROUND2) == "g6we-base:aaaaaaaaaaaa-r2"


def test_task_dockerfile_keeps_the_fix_out_of_reach():
    text = we.task_dockerfile(TASK, "g6we-base:abc")
    assert text.startswith("FROM g6we-base:abc")
    final = text
    # upstream's base commit fetched alone, so git tracks exactly what upstream tracks (PLAN.md section 9, run 6 on)
    assert "fetch -q --depth=1 --no-tags https://github.com/psf/black.git " + "p" * 40 in text
    assert "checkout -q --detach " + "p" * 40 in text and 'rev-list --all --count)" = 1' in text
    assert "git add" not in text and "m" * 40 not in text   # no re-made commit; the merge commit is never named
    assert "/.venv/" in final and "python3 -m venv .venv" in final
    assert "--copies" not in final                   # the project's .venv is an ordinary one, with links
    assert "SETUPTOOLS_SCM_PRETEND_VERSION=26.10.0" in final and "ENV SETUPTOOLS" not in final
    # the dependencies as they were when the fix was merged, and the installer gone from the image afterwards
    assert "UV_EXCLUDE_NEWER=2026-10-28T19:18:12Z" in final and "rm -rf /tmp/we-uv" in final


def test_oracle_dockerfile_holds_the_fix_apart():
    text = we.oracle_dockerfile(TASK, "g6we-task:x")
    assert "checkout -q " + "m" * 40 in text and "--no-renames" in text and "FROM g6we-task:x" in text


def test_claude_args_follow_the_plan():
    args = we.claude_args()
    assert args[:2] == [we.CLAUDE, "-p"]
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert args[args.index("--max-turns") + 1] == "200" and args[args.index("--max-budget-usd") + 1] == "10.00"
    assert args[-3:] == ["--disallowedTools", "WebFetch", "WebSearch"]
    assert "--strict-mcp-config" not in args and "--effort" not in args and "--dangerously-skip-permissions" in args


def test_prompt_is_the_plans_text():
    block = PLAN.read_text(encoding="utf-8").split("Its prompt is this text", 1)[1]
    lines = [line[6:] if line.startswith("      ") else line for line in block.splitlines()[2:]]
    plan_text = "\n".join(lines).split("\n\n- **The per-run cap:**", 1)[0].strip()
    assert we.PROMPT.strip() == plan_text
    filled = we.prompt(TASK, "Empty lines trimmed", "The text.")
    assert "a clone of psf/black" in filled and filled.rstrip().endswith("The text.")
    assert "Issue #496: Empty lines trimmed" in filled


def test_hook_says_nothing_and_keeps_the_index():
    hook = we.hook_script(f"{we.HOME}/work/black", "b" * 40)
    assert "GIT_INDEX_FILE=" in hook and "git diff --cached --binary " + "b" * 40 in hook
    assert hook.rstrip().endswith("exit 0") and "echo" not in hook
    settings = json.loads(we.hook_settings())
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "mcp__g6__g6_review_patch"


# --- validation and the independent check --------------------------------------------------------------------------------
JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="tests.test_a" name="test_new"><failure message="x"/></testcase>
<testcase classname="tests.test_a" name="test_old"/>
<testcase classname="tests.test_a" name="test_skip"><skipped/></testcase>
<testcase classname="tests.test_a" name="test_err"><error message="e"/></testcase>
</testsuite></testsuites>"""


def test_parse_junit():
    got = we.parse_junit(JUNIT)
    assert got == {"tests.test_a::test_new": "failed", "tests.test_a::test_old": "passed",
                   "tests.test_a::test_skip": "skipped", "tests.test_a::test_err": "error"}


def test_fail_to_pass_and_pass_to_pass():
    base = {"a": "failed", "b": "passed", "c": "passed", "d": "error"}
    fixed = {"a": "passed", "b": "passed", "c": "failed", "d": "passed", "e": "passed"}
    f2p, p2p = we.fail_to_pass(base, fixed)
    assert f2p == ["a", "d", "e"] and p2p == ["b"]
    assert we.verdict({"a": "passed", "d": "passed", "e": "passed", "b": "passed"}, f2p, p2p)["passes"]
    bad = we.verdict({"a": "passed", "d": "failed", "b": "passed"}, f2p, p2p)
    assert not bad["passes"] and bad["failing"] == ["d", "e"]
    assert not we.verdict({}, [], ["b"])["passes"]            # no FAIL_TO_PASS: nothing shows the fix


def test_test_changes_are_the_test_paths_of_the_fix():
    files = "M\tsrc/black/comments.py\nM\ttests/test_black.py\nA\ttests/data/cases/new.py\nD\ttests/old_test.py\nM\tCHANGES.md\n"
    assert we.test_changes(files) == [("M", "tests/test_black.py"), ("A", "tests/data/cases/new.py"),
                                      ("D", "tests/old_test.py")]


# --- the key check ---------------------------------------------------------------------------------------------------------
def test_key_check_needs_a_confined_pass_and_a_control_that_sees_the_key():
    good = {"confined": {"R1": "passed_on_stated_checks", "backend": "bubblewrap"},
            "control": {"exit": 1, "channels": ["own_env_name", "proc_env_name"]}}
    assert we.key_check_ok(good)
    assert not we.key_check_ok({**good, "confined": {"R1": "failed", "backend": "bubblewrap"}})
    assert not we.key_check_ok({**good, "confined": {"R1": "passed_on_stated_checks", "backend": None}})
    assert not we.key_check_ok({**good, "control": {"exit": 0, "channels": []}})


def test_key_probe_names_channels_never_values():
    assert "os.environ.values()" in we.KEYPROBE_TEST and "print(" not in we.KEYPROBE_TEST
    assert "seen: " in we.KEYPROBE_TEST
    assert "channels" in we.keyprobe_driver() and "ANTHROPIC_API_KEY" not in we.keyprobe_driver().split("PROBE =")[0]


# --- the rounds, read from the transcript --------------------------------------------------------------------------------
def _stream():
    def use(i, name, inp):
        return {"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}

    def result(i, obj):
        return {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": [{"type": "text", "text": json.dumps(obj)}]}]}}

    return [use(1, "mcp__g6__g6_review_patch", {"repo": "/r", "task": "t"}), result(1, {"job_id": "j1"}),
            use(2, "mcp__g6__metaharness_job", {"job_id": "j1"}), result(2, {"status": "running"}),
            use(3, "mcp__g6__metaharness_job", {"job_id": "j1"}),
            result(3, {"result": {"assessment": {"outcome": "defect_detected", "report": "R one"}}}),
            use(4, "Edit", {}), result(4, "ok"),
            use(5, "mcp__g6__g6_review_patch", {"repo": "/r", "task": "t"}),
            result(5, {"assessment": {"outcome": "supported_within_scope", "report": "R two"}})]


def test_progress_reads_an_unfinished_run(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", TASK.id)
    lines = [*_stream(), {"type": "assistant", "message": {"id": "u1", "model": c.MODEL,
                                                          "usage": {"output_tokens": 100_000}}}]
    (run.path / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    got = we.progress(archive, TASK.id)
    assert not got["finished"] and got["rounds"] == ["defect_detected", "supported_within_scope"]
    assert got["cost_usd"] == pytest.approx(1.0) and got["last_tool"] == "mcp__g6__g6_review_patch"


def test_observe_records_what_the_w_fixes_changed():
    from worked_examples import observe

    report = "**Supported** (supported_within_scope).\n\n## Reproduced findings\n- None."
    init = {"type": "system", "subtype": "init", "mcp_servers": [{"name": "g6", "status": "connected"}],
            "tools": ["Bash", "mcp__g6__g6_review_patch", "mcp__g6__metaharness_job"]}

    def use(i, name, inp):
        return {"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}

    def result(i, text):
        return {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": [{"type": "text", "text": text}]}]}}

    refused = json.dumps({"status": "completed", "result": {"ok": False, "error":
                          "[G6_E_INPUT_VALIDATION] template_instances.0 (T1): bindings.relation: Field required"}})
    done = json.dumps({"status": "completed", "result": {"assessment": {"report": report, "outcome": "x"}}})
    stream = [init,
              use(1, "mcp__g6__g6_review_patch", {"template_instances": [{"id": "T1"}]}), result(1, refused),
              use(2, "mcp__g6__g6_review_patch", {"template_instances": [{"id": "T1"}]}), result(2, done),
              {"type": "assistant", "message": {"id": "m9", "content": [{"type": "text", "text": "G6:\n" + report}]}},
              {"type": "result", "result": "done"}]
    got = observe.observe(stream)
    assert got["server"] == {"g6": "connected", "g6_tools": 2}
    assert got["replies"] == [{"round": 1, "chars": len(refused), "saved_to_file": False, "refused": True},
                              {"round": 2, "chars": len(done), "saved_to_file": False, "refused": False}]
    assert got["report_shown_verbatim"] == [True]
    assert got["templates"] == {"rounds_with_instances": 2, "refused_for_bindings": 1,
                                "accepted_after_a_refusal": True}
    quoted = [*stream[:5], {"type": "result", "result": "\n".join("> " + line for line in report.splitlines())}]
    assert observe.observe(quoted)["report_shown_verbatim"] == [True]       # quoted whole is shown whole
    cut = [*stream[:5], {"type": "result", "result": report[:30]}]
    assert observe.observe(cut)["report_shown_verbatim"] == [False]


def test_proposer_summary_reads_what_g6s_model_proposed():
    from worked_examples import observe

    saved = {"ok": True, "assessment": {"outcome": "insufficient_evidence",
             "reasons": [{"stage": "search", "code": "counterexample"}],
             "next_actions": ["PYTHONPATH=src python -m pytest g6p-1_test.py"],
             "completion": {"activities": [
                 {"activity": "proposer stage", "stage": "templates", "result": "replied"},
                 {"activity": "model call", "stage": "templates", "result": "replied"},
                 {"activity": "model call", "stage": "free_form", "result": "error", "reason": "x"},
                 {"activity": "proposal", "stage": "templates", "requirement": "R1", "reason": "refused: y"},
                 {"check": "g6p-1", "template": "example.v1", "result": "counterexample"},
                 {"check": "g6p-2", "template": "", "result": "not_refuted"},
                 {"check": "T1", "template": "relation.v1", "result": "not_refuted"}],
                 "defects_reproduced": [{"check": "g6p-1", "said": "f(0) gave 1"}]}}}
    got = observe.proposer_summary(saved)
    assert got["outcome"] == "insufficient_evidence" and got["model_calls"] == 2 and got["model_call_errors"] == 1
    assert got["proposed"] == [{"check": "g6p-1", "template": "example.v1", "result": "counterexample"},
                               {"check": "g6p-2", "template": "", "result": "not_refuted"}]
    assert got["refused"] == [{"stage": "templates", "requirement": "R1", "reason": "refused: y"}]
    assert got["defects_reproduced"] == [{"check": "g6p-1", "said": "f(0) gave 1"}]
    assert got["next_actions"] == ["PYTHONPATH=src python -m pytest g6p-1_test.py"]


def test_extract_rounds():
    rounds = we.extract_rounds(_stream())
    assert [r["outcome"] for r in rounds] == ["defect_detected", "supported_within_scope"]
    assert rounds[0]["job_id"] == "j1" and rounds[1].get("job_id") is None    # the job whose saved review is read
    assert rounds[0]["report"] == "R one" and rounds[0]["polls"] == 2 and rounds[1]["input"]["task"] == "t"
    assert rounds[0]["assessment"]["outcome"] == "defect_detected"     # the whole assessment, for the judgements
    refused = [{"type": "assistant", "message": {"id": "m9", "content": [
        {"type": "tool_use", "id": "t9", "name": "mcp__g6__g6_review_patch", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t9", "content": [
            {"type": "text", "text": json.dumps({"ok": True, "status": "completed",
                                                 "result": {"ok": False, "error": "[G6_E_INPUT_VALIDATION] x"}})}]}]}}]
    assert we.extract_rounds(refused)[0]["error"] == "[G6_E_INPUT_VALIDATION] x"
    # a reply too large for Claude Code is saved to a file, and the transcript holds only where
    saved = f"{we.HOME}/.claude/projects/-home-tester-work-black/s1/tool-results/mcp-g6-metaharness_job-1.txt"
    big = [refused[0], {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t9",
           "content": f"Error: result (64,108 characters) exceeds maximum allowed tokens. Output has been saved to "
                      f"{saved}.\nFormat: JSON"}]}}]
    reply = json.dumps({"result": {"assessment": {"outcome": "test_integrity_compromised", "report": "R big"}}})
    got = we.extract_rounds(big, resolve=lambda p: reply if p == saved else None)
    assert got[0]["outcome"] == "test_integrity_compromised" and got[0]["saved_to_file"] is True


# --- one run, with a fake Docker --------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, lines):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(json.dumps(x).encode() + b"\n" for x in lines))
        self.stderr = io.BytesIO(b"")
        self.returncode = 0

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class Done:
    def __init__(self, out=b"", code=0):
        self.stdout, self.stderr, self.returncode = out, b"", code


class FakeDocker:
    """Records every docker call; answers as a healthy run would."""

    def __init__(self, keycheck_ok=True):
        self.calls, self.envs, self.keycheck_ok = [], [], keycheck_ok

    def __call__(self, *args, input=None, timeout=None, env=None):
        self.calls.append(list(args))
        self.envs.append(env)
        text = " ".join(args)
        if args[0] == "exec" and "keyprobe" in text:
            body = {"confined": {"R1": "passed_on_stated_checks" if self.keycheck_ok else "failed",
                                 "backend": "bubblewrap"}, "control": {"exit": 1, "channels": ["own_env_name"]}}
            return Done(json.dumps(body).encode())
        if args[0] == "exec" and "we-tree-state" in text:
            return Done(json.dumps({"head": TASK.parent, "tree": "t" * 40, "dirty": 0, "commits": 1}).encode())
        if args[0] == "exec" and "--version" in text:
            return Done(b"2.1.309 (Claude Code)")
        if args[0] == "image" and args[1] == "inspect":
            return Done(b"sha256:img")
        return Done(b"")

    def sh(self, ctr, script, *args, timeout=600, user="tester", workdir=None, env_names=()):
        return self("exec", "-u", user, ctr, "sh", "-c", script, "we", *args, timeout=timeout)

    def popen(self, *args, env=None):
        self.calls.append(list(args))
        self.envs.append(env)
        return FakeProc([{"type": "assistant", "message": {"id": "m1", "model": c.MODEL,
                                                           "usage": {"output_tokens": 1000}}},
                         {"type": "result", "subtype": "success", "result": "done", "num_turns": 1,
                          "modelUsage": {c.MODEL: {"outputTokens": 1000}}}])


def _ctx(tmp_path, docker, *, validated=True, g6_commit_ok=True, oracle_id="sha256:img"):
    archive = c.Archive(tmp_path / "archive")
    if validated:
        v = archive.start("validate", TASK.id)
        v.finish({"fail_to_pass": ["t::a"], "pass_to_pass": ["t::b"], "ok": True, "oracle_image_id": oracle_id},
                 "finished")
    return we.Context(archive=archive, docker=docker, key_reader=lambda: KEY, issue_reader=lambda t: ("T", "body"),
                      g6={"commit": "g" * 40, "image": "g6we-base:gggg", "contains_required": g6_commit_ok},
                      proxy_source=tmp_path / "proxy.py", environ={"PATH": "x"}, tree_reader=lambda t: "t" * 40)


def test_a_run_passes_the_key_by_name_only_and_cleans_up_its_own(tmp_path):
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    docker = FakeDocker()
    rec = we.run_task(_ctx(tmp_path, docker), TASK)
    assert rec["status"] == "finished", rec
    starts = [i for i, call in enumerate(docker.calls) if call[:2] == ["run", "-d"]]
    names = [docker.calls[i][docker.calls[i].index("--name") + 1] for i in starts]
    probe = next(i for i, n in zip(starts, names) if "keyprobe" in n)
    agent = next(i for i, n in zip(starts, names) if n.startswith("g6we-run-"))
    claude = next(i for i, call in enumerate(docker.calls) if we.CLAUDE in call and "-p" in call)
    assert probe < agent < claude
    for call, env in zip(docker.calls, docker.envs):
        assert KEY not in " ".join(call)                       # never on a command line
        if env is not None and KEY in env.values():
            assert "-e" in call and "ANTHROPIC_API_KEY" in call  # only by name, into that exec's environment
    removed = {call[-1] for call in docker.calls if call[:2] == ["rm", "-f"]}
    assert set(names) <= removed and all(n.startswith(("g6we-", "g6wepx-")) for n in removed)
    for path in (tmp_path / "archive").rglob("*"):
        if path.is_file():
            assert KEY not in path.read_text(encoding="utf-8", errors="replace")
    setup = next(call for call in docker.calls if "setup" in call)
    assert setup[-6:] == ["g6", "setup", "--client", "claude", "--allow", "patch_review.review"]
    assert any(call[-1] == we.hook_settings() or "settings.json" in " ".join(call) for call in docker.calls)


def test_a_run_refuses_before_any_start(tmp_path):
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    for ctx, why in ((_ctx(tmp_path / "a", FakeDocker(), validated=False), "validat"),
                     (_ctx(tmp_path / "b", FakeDocker(), g6_commit_ok=False), "commit"),
                     (_ctx(tmp_path / "d", FakeDocker(), oracle_id="sha256:older"), "images this run")):
        ctx.proxy_source = tmp_path / "proxy.py"
        rec = we.run_task(ctx, TASK)
        assert rec["status"] == "refused" and why in rec["why"]
        assert not any(call[:2] == ["run", "-d"] for call in ctx.docker.calls)
    held = _ctx(tmp_path / "e", FakeDocker())
    (held.archive.root / "HOLD").write_text("waiting for a fix", encoding="utf-8")
    rec = we.run_task(held, TASK)
    assert rec["status"] == "refused" and rec["why"] == "held: waiting for a fix"
    ctx = _ctx(tmp_path / "c", FakeDocker())
    ctx.proxy_source = tmp_path / "proxy.py"
    for _ in range(9):
        r = ctx.archive.start("run", "other")
        r.finish({"cost_usd": 10.0}, "finished")
    rec = we.run_task(ctx, TASK)
    assert rec["status"] == "refused" and "stop" in rec["why"]


def test_a_dry_run_reads_no_key_and_has_no_way_out(tmp_path):
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    docker = FakeDocker()
    ctx = _ctx(tmp_path, docker, validated=False, g6_commit_ok=False)

    def no_key():
        raise AssertionError("a dry run must not read the key")

    ctx.key_reader = no_key
    rec = we.run_task(ctx, TASK, dry=True)
    assert rec["status"] == "finished" and "hook_selftest" in rec
    claude = next(call for call in docker.calls if we.CLAUDE in call and "-p" in call)
    assert f"HTTPS_PROXY={we.DEAD_PROXY}" in claude
    assert all(r.get("kind") != "run" for r in ctx.archive.rows() if r.get("event") == "started")


class KeyFileDocker(FakeDocker):
    """The fake, with round 2's key check: the control sees G6's key file too."""

    def __init__(self):
        super().__init__()
        self.inputs = []

    def __call__(self, *args, input=None, timeout=None, env=None):
        self.inputs.append(input)
        if args[0] == "exec" and "keyprobe" in " ".join(args):
            self.calls.append(list(args))
            self.envs.append(env)
            body = {"confined": {"R1": "passed_on_stated_checks", "backend": "bubblewrap"},
                    "control": {"exit": 1, "channels": ["key_file", "own_env_name"]},
                    "g6_model_calls": [{"input_chars": 2000, "input_tokens": None, "output_tokens": None}]}
            return Done(json.dumps(body).encode())
        return super().__call__(*args, input=input, timeout=timeout, env=env)


def test_round2_gives_g6_its_key_file_only_through_stdin_and_takes_it_out(tmp_path):
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    docker = KeyFileDocker()
    ctx = _ctx(tmp_path, docker)
    ctx.variant = we.ROUND2
    rec = we.run_task(ctx, TASK)
    assert rec["status"] == "finished" and rec["key_check"]["ok"]
    writes = [i for i, call in enumerate(docker.calls) if any(we.KEY_FILE in a and "cat >" in a for a in call)]
    assert len(writes) == 2                                   # the key check's container and the agent's
    assert all(KEY not in " ".join(call) for call in docker.calls)
    assert sum(1 for x in docker.inputs if x and KEY.encode() in x) == 2   # only ever on standard input
    removed = next(i for i, call in enumerate(docker.calls) if call[-3:] == ["rm", "-f", we.KEY_FILE])
    copied = next(i for i, call in enumerate(docker.calls) if call[:1] == ["cp"] and call[1].endswith(":" + we.HOME + "/.g6"))
    assert writes[1] < removed < copied
    probe = next(call for call in docker.calls if call[:2] == ["run", "-d"] and "keyprobe" in call[call.index("--name") + 1])
    assert probe[probe.index("--network") + 1].startswith("g6wenet-")   # round 2's check reaches the model by the proxy
    assert rec["key_check_g6"]["calls"] == 1 and rec["cost_usd"] >= rec["key_check_g6"]["usd"] > 0


def test_a_tree_unlike_upstreams_starts_no_agent(tmp_path):
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    docker = FakeDocker()
    ctx = _ctx(tmp_path, docker)
    ctx.tree_reader = lambda t: "u" * 40            # upstream's tree differs from the image's
    rec = we.run_task(ctx, TASK)
    assert rec["status"] == "tree_mismatch" and rec["tree_state"]["upstream_tree"] == "u" * 40
    assert not any(we.CLAUDE in call and "-p" in call for call in docker.calls)
    assert not any("setup" in call for call in docker.calls)


def test_a_failed_key_check_starts_no_agent(tmp_path):
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    docker = FakeDocker(keycheck_ok=False)
    rec = we.run_task(_ctx(tmp_path, docker), TASK)
    assert rec["status"] == "key_check_failed"
    assert not any(we.CLAUDE in call and "-p" in call for call in docker.calls)
    assert not any(call[:2] == ["run", "-d"] and call[call.index("--name") + 1].startswith("g6we-run-")
                   for call in docker.calls)


def test_selfcheck_needs_the_fix_to_pass_and_nothing_to_fail(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, FakeDocker())
    seen = []

    def oracle_run(ctx_, task, *, patch=None, folder=None, label="", **_):
        seen.append(label)
        ok = {"t::a": "passed", "t::b": "passed"}
        return {"results": ok if label == "gold" else {"t::a": "failed", "t::b": "passed"}}

    monkeypatch.setattr(we, "_oracle_run", oracle_run)
    assert we.selfcheck(ctx, TASK)["ok"] and seen == ["gold", "empty"]
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **k: {"results": {"t::a": "passed", "t::b": "passed"}})
    assert not we.selfcheck(ctx, TASK)["ok"]          # a check an empty patch passes shows nothing


MINI_SEARCH = '''SEARCH_SETTINGS = {"templates": True, "test_adequacy": False}
PROPOSER_FREE_FORM, PROPOSER_TEMPLATES_FIRST = "free_form", "templates_first"
PROPOSER_SETTINGS = {"propose": False, "proposer": PROPOSER_TEMPLATES_FIRST, "max_model_calls": 10,
                     "max_input_chars": 60_000, "max_output_tokens": 4_096, "max_context_chars": 24_000}


def search_config(value) -> dict:
    settings = {**SEARCH_SETTINGS, **PROPOSER_SETTINGS}
    if value is None:
        return settings
    return {**settings, **value}
'''


def test_round2_forces_the_proposer_on_whatever_the_caller_passes():
    from worked_examples import round2_g6

    space: dict = {}
    exec(round2_g6.patch(MINI_SEARCH), space)  # noqa: S102 - the test's own text
    assert space["search_config"](None)["propose"] is True
    got = space["search_config"]({"propose": False, "max_model_calls": 64, "proposer": "free_form",
                                  "templates": False})
    assert got["propose"] is True and got["max_model_calls"] == 10 and got["proposer"] == "templates_first"
    assert got["templates"] is False                     # every other setting stays the caller's
    with pytest.raises(ValueError):
        round2_g6.patch(MINI_SEARCH.replace("        return settings\n", ""))   # an anchor gone: nothing written
    # and the review builds a search configuration even when the caller passes no search and no template instance
    review = ('def _inputs(search):\n    config = None\n    if search is not None:\n        config = search\n'
              '    return stated, assumed, unmapped, proposed, instances, config\n')
    patched = round2_g6.patch(review, round2_g6.REVIEW_EDITS)
    assert "config = config or verification_search.search_config(None)\n    return stated" in patched


def test_round2_patch_writes_nothing_unless_both_files_have_their_anchors(tmp_path):
    from worked_examples import round2_g6

    (tmp_path / "verification_search.py").write_text(MINI_SEARCH, encoding="utf-8")
    (tmp_path / "patch_review.py").write_text("no anchor here\n", encoding="utf-8")
    assert round2_g6.main(["x", str(tmp_path)]) == 1
    assert (tmp_path / "verification_search.py").read_text(encoding="utf-8") == MINI_SEARCH


def test_round2_key_file_and_cost():
    text = we.g6_key_file(KEY)
    assert text.splitlines() == ["G6_LLM_PROVIDER=anthropic", "G6_LLM_MODEL=claude-sonnet-5-5",
                                 f"ANTHROPIC_API_KEY={KEY}"]
    assessment = {"completion": {"activities": [
        {"activity": "model call", "input_tokens": 1_000_000, "output_tokens": 0, "answered_by": c.MODEL},
        {"activity": "model call", "input_tokens": 0, "output_tokens": 100_000, "answered_by": c.MODEL},
        {"activity": "proposer stage", "stage": "templates"}]}}
    got = we.g6_model_cost([assessment])
    assert got["calls"] == 2 and got["usd"] == pytest.approx(3.0) and got["calls_counted_at_upper_bound"] == 0
    # G6's own caller gives no token counts: characters over two in, the whole reply limit out
    unreported = {"completion": {"activities": [{"activity": "model call", "input_tokens": None,
                                                 "output_tokens": None, "input_chars": 20_000, "output_chars": 900,
                                                 "answered_by": "not given"}]}}
    got = we.g6_model_cost([unreported])
    assert got["calls_counted_at_upper_bound"] == 1 and got["input_tokens"] == 10_000
    assert got["usd"] == pytest.approx((10_000 * 2.00 + 4_096 * 10.00) / 1e6)
    assert we.key_check_ok({"confined": {"R1": "passed_on_stated_checks", "backend": "bubblewrap"},
                            "control": {"exit": 1, "channels": ["own_env_name", "key_file"]}}, key_file=True)
    assert not we.key_check_ok({"confined": {"R1": "passed_on_stated_checks", "backend": "bubblewrap"},
                                "control": {"exit": 1, "channels": ["own_env_name"]}}, key_file=True)


def test_the_task_list_is_in_the_plans_order():
    tasks = we.load_tasks()
    merged = [t.merged_at for t in tasks]
    assert merged == sorted(merged, reverse=True) and len({t.id for t in tasks}) == len(tasks)
    for project in {t.project for t in tasks}:
        assert sum(t.project == project for t in tasks) <= 2
    for t in tasks:
        assert len(t.merge_commit) == 40 and len(t.parent) == 40 and t.test_modules and t.install
        assert "2026-06-01" <= t.merged_at[:10] <= "2026-10-29"


def test_no_private_path_in_the_scripts():
    home = str(Path.home())
    for name in ("run.py", "wecommon.py", "selection.py", "PLAN.md"):
        text = Path(we.__file__).with_name(name).read_text(encoding="utf-8")
        assert home not in text and home.replace("\\", "/") not in text
    assert re.search(r"Path\.home\(\)", Path(we.__file__).with_name("wecommon.py").read_text(encoding="utf-8"))

def test_the_key_check_still_looks_in_the_containers_home_folder():
    # the export scan reads no literal home folder or key in these files; the scripts the container runs still name
    # the same home folder, byte for byte
    assert we.HOME == "/home/" + we.USER
    assert f'"{we.HOME}/.g6/.env"' in we.KEYPROBE_TEST and "@HOME@" not in we.KEYPROBE_TEST
    driver = we.keyprobe_driver()
    assert f'pathlib.Path("{we.HOME}/keyprobe/repo")' in driver and "@HOME@" not in driver
    assert KEY == "sk-ant-api03-" + "FAKE" * 6 and len(KEY) == 37


# --- round 3: formal methods (PLAN.md section 9, "Round 3 (2026-10-30)") ---------------------------------------------------
HAIKU, SONNET, FABLE = we.R3_MODELS


def test_observe_counts_what_the_agent_called_not_what_was_on_offer():
    from worked_examples import observe

    def use(name):
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": name, "name": name, "input": {}}]}}

    init = {"type": "system", "subtype": "init", "mcp_servers": [{"name": "g6", "status": "connected"}],
            "tools": ["mcp__g6__g6_review_patch", "mcp__g6__g6_invoke", "ToolSearch"]}
    offered = observe.observe([init, use("Bash"), use("ToolSearch")])
    assert offered["server"]["g6_tools"] == 2
    assert offered["tool_use"] == {"tool_search": 1, "g6_calls": 0, "g6_by_tool": {}}
    used = observe.observe([init, use("mcp__g6__g6_review_patch"), use("mcp__g6__g6_invoke"), use("mcp__g6__g6_invoke")])
    assert used["tool_use"] == {"tool_search": 0, "g6_calls": 3,
                                "g6_by_tool": {"g6_invoke": 2, "g6_review_patch": 1}}


def test_round3_tally_counts_claude_codes_reported_total_and_the_stop_uses_each_runs_own_stop():
    t = c.Tally(count_reported=True)
    t.feed({"type": "assistant", "message": {"id": "m1", "model": c.MODEL, "usage": {"output_tokens": 100_000}}})
    t.feed({"type": "result", "total_cost_usd": 3.25})
    assert t.cost_usd == pytest.approx(3.25)                 # the larger of the counts and Claude Code's own total
    assert c.Tally(result={"total_cost_usd": 3.25}).cost_usd == 0.0   # rounds 1 and 2 are counted as they were
    rows = [{"event": "started", "run": "run/a/1", "kind": "run", "stop_usd": 4.2}]
    assert c.spent(rows) == pytest.approx(4.2)
    assert "claude-fable-5-1" not in c.PRICES and c.price_of(FABLE) is c.UNKNOWN_PRICE   # no price in this repository


def test_round3_prompts_differ_only_as_the_plan_states():
    b = we.prompt(TASK, "T", "body", arm="B")
    assert b == we.prompt(TASK, "T", "body")                 # arm B is round 1's prompt, unchanged
    c_ = we.prompt(TASK, "T", "body", arm="C")
    assert we.ADDENDUM_C in c_ and we.ADDENDUM_C not in b
    assert c_.replace(we.ADDENDUM_C + "\n\n", "") == b      # C is B plus the addendum, nothing else
    a = we.prompt(TASK, "T", "body", arm="A")
    for word in ("g6", "G6", "skill", "review"):
        assert word not in a.split("Issue #")[0]             # arm A: Claude Code alone, told nothing of G6
    assert a.split("Issue #")[1] == b.split("Issue #")[1]    # the same issue text
    with pytest.raises(ValueError):
        we.prompt(TASK, "T", "body", arm="Z")


@pytest.mark.parametrize("addendum", ["ADDENDUM_C", "ADDENDUM_D"])
def test_round3_addendum_names_no_bug_and_no_diagnosis(addendum):
    text = getattr(we, addendum).lower()
    words = set()
    for t in we.load_tasks():
        words |= {t.id.lower(), t.full_name.lower(), t.name.lower(), str(t.issue), str(t.pr)}
    # the diagnoses RESULTS.md gives, which must never reach an agent
    words |= {"fmt: off", "root_path", "trailing slash", "bar.update", "nested", "_click_default_help", "help option",
              "_black_version", "docstring", "progress", "warning", "pydantic", "black", "click", "starlette"}
    assert not [w for w in words if w in text]
    assert len(getattr(we, addendum).split()) <= 260         # short and factual


def test_round3_claude_args_take_the_model_and_the_cap():
    args = we.claude_args(HAIKU, 4.5)
    assert args[args.index("--model") + 1] == HAIKU and args[args.index("--max-budget-usd") + 1] == "4.50"
    assert args[-3:] == ["--disallowedTools", "WebFetch", "WebSearch"]
    assert we.claude_args() == we.claude_args(c.MODEL, c.RUN_CAP_USD)   # round 1's, unchanged


def test_round3_schedule_order_and_noise_control():
    sched = we.r3_schedule(we.load_tasks())
    models = [m for m, *_ in sched]
    assert models == sorted(models, key=we.R3_MODELS.index)  # Haiku, then Sonnet, then Fable last
    assert len(sched) == 10 * 3 + 10 * 4 + 10 * 3
    assert sum(1 for m, t, a, r in sched if m == SONNET and a == "A") == 20   # Sonnet arm A twice per bug
    assert {r for m, t, a, r in sched if m != SONNET or a != "A"} == {1}


def test_round3_caps():
    assert we.r3_caps(HAIKU, spent=50.0, fable_left=30) == (10.0, 10.5)
    assert we.r3_caps(SONNET, spent=0.0, fable_left=30) == (10.0, 10.5)
    assert we.r3_caps(FABLE, spent=0.0, fable_left=30) == (5.71, pytest.approx(5.9955))   # $180 over 30, its stop
    assert we.r3_caps(FABLE, spent=179.0, fable_left=1)[1] <= 1.0                         # never past the $180
    assert we.r3_caps(FABLE, spent=0.0, fable_left=1) == (10.0, 10.5)                     # at most $10


class ArmDocker(FakeDocker):
    """The fake, with round 3's arm check: G6's server and skill are there only after ``g6 setup`` ran."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_up = False

    def __call__(self, *args, input=None, timeout=None, env=None):
        if "setup" in args:
            self.set_up = True
        if args[0] == "exec" and "we-arm-state" in " ".join(args):
            self.calls.append(list(args))
            self.envs.append(env)
            got = {"mcp_servers": ["g6"], "skills": ["using-g6"]} if self.set_up else {"mcp_servers": [], "skills": []}
            return Done(json.dumps(got).encode())
        return super().__call__(*args, input=input, timeout=timeout, env=env)

    def popen(self, *args, env=None):
        self.calls.append(list(args))
        self.envs.append(env)
        return FakeProc([{"type": "assistant", "message": {"id": "m1", "model": HAIKU, "usage": {"output_tokens": 10}}},
                         {"type": "result", "subtype": "success", "result": "done", "num_turns": 1,
                          "total_cost_usd": 0.25}])


def _r3(tmp_path, arm, *, model=HAIKU, repeat=1, docker=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "proxy.py").write_text("# proxy", encoding="utf-8")
    ctx = _ctx(tmp_path, docker or ArmDocker())
    ctx.variant, ctx.arm, ctx.model, ctx.repeat = we.ROUND3, arm, model, repeat
    ctx.tasks = [TASK]
    return ctx


def test_round3_arm_a_registers_no_mcp_server_and_b_and_c_do(tmp_path):
    for arm in ("A", "B", "C"):
        ctx = _r3(tmp_path / arm, arm)
        rec = we.run_task(ctx, TASK)
        assert rec["status"] == "finished", rec
        calls = ctx.docker.calls
        claude = next(call for call in calls if we.CLAUDE in call and "-p" in call)
        assert claude[claude.index("--model") + 1] == HAIKU
        assert claude[claude.index("--max-budget-usd") + 1] == "10.00"
        if arm == "A":
            assert not any("setup" in call for call in calls) and rec["arm_state"]["mcp_servers"] == []
            removed = [call for call in calls if call[-3:] == ["rm", "-f", "/usr/local/bin/g6"]]
            assert removed and calls.index(removed[0]) < calls.index(claude)   # no g6 on the agent's path
        else:
            assert any("setup" in call for call in calls) and rec["arm_state"]["mcp_servers"] == ["g6"]
        prompt = (next((tmp_path / arm / "archive" / "run" / TASK.id).glob("*")) / "prompt.txt").read_text(
            encoding="utf-8")
        assert (we.ADDENDUM_C in prompt) == (arm == "C") and ("g6_review_patch" in prompt) == (arm != "A")
        assert rec["arm"] == arm and rec["model"] == HAIKU and rec["cost_usd"] == pytest.approx(0.25)


def test_round3_an_arm_whose_setup_does_not_match_starts_no_agent(tmp_path):
    class NoServer(ArmDocker):
        def __call__(self, *args, **kw):
            out = super().__call__(*args, **kw)
            self.set_up = False                    # setup ran, but no server is registered
            return out

    ctx = _r3(tmp_path, "B", docker=NoServer())
    rec = we.run_task(ctx, TASK)
    assert rec["status"] == "arm_mismatch"
    assert not any(we.CLAUDE in call and "-p" in call for call in ctx.docker.calls)


def test_round3_ledger_accumulates_and_the_budget_stop_refuses(tmp_path):
    ctx = _r3(tmp_path, "A")
    first = we.run_task(ctx, TASK)
    ctx.arm = "B"
    second = we.run_task(ctx, TASK)
    assert first["status"] == second["status"] == "finished"
    ledger = [json.loads(x) for x in (ctx.archive.root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["arm"], r["cost_usd"]) for r in ledger] == [("A", 0.25), ("B", 0.25)]
    assert [r["spent_after"] for r in ledger] == [pytest.approx(0.25), pytest.approx(0.5)]
    # a run is refused when the round's remaining budget is below its stop
    for _ in range(9):
        r = ctx.archive.start("run", "other")
        r.finish({"cost_usd": 10.0}, "finished")
    ctx.arm = "C"
    rec = we.run_task(ctx, TASK)
    assert rec["status"] == "refused" and "$180" in rec["why"]
    assert len((ctx.archive.root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_round3_refuses_a_run_twice_and_out_of_order(tmp_path):
    ctx = _r3(tmp_path, "A")
    assert we.run_task(ctx, TASK)["status"] == "finished"
    again = we.run_task(ctx, TASK)
    assert again["status"] == "refused" and "already" in again["why"]
    ctx.arm, ctx.model = "A", SONNET                          # Haiku's B and C for this bug are not run yet
    later = we.run_task(ctx, TASK)
    assert later["status"] == "refused" and "order" in later["why"]
    ctx.model = "claude-opus-5-5"
    assert "model" in we.run_task(ctx, TASK)["why"]


def test_round3_only_the_two_smoke_runs_go_before_haikus_runs_are_done(tmp_path):
    # owner, 2026-10-30: Haiku's three pilot runs never called G6, so Sonnet's B and C on one bug go first
    assert we.R3_SMOKE == {(SONNET, "black-5634", "B", 1), (SONNET, "black-5634", "C", 1),
                           (SONNET, "attrs-1593", "C", 1), (SONNET, "click-3678", "C", 1),
                           (SONNET, "pydantic-14052", "C", 1)}
    ctx = _r3(tmp_path, "B", model=SONNET)
    assert we.run_task(ctx, TASK)["status"] == "finished"
    ctx.arm = "C"
    assert we.run_task(ctx, TASK)["status"] == "finished"
    ctx.arm = "A"
    assert "order" in we.run_task(ctx, TASK)["why"]
    ctx.arm, ctx.model = "B", FABLE
    assert "order" in we.run_task(ctx, TASK)["why"]


def test_round3_ledger_says_whether_g6_was_used(tmp_path):
    ctx = _r3(tmp_path, "B")
    record = {"run": "r", "observed": {"tool_use": {"tool_search": 2, "g6_calls": 3}}}
    assert (we.r3_ledger(ctx.archive, record)["g6_calls"], we.r3_ledger(ctx.archive, record)["tool_search"]) == (3, 2)
    assert we.r3_ledger(ctx.archive, {"run": "r"})["g6_calls"] is None     # a run that never got that far says so


def test_round3_arm_d_is_c_with_the_fallback_on_the_four_probe_bugs_only(tmp_path):
    assert we.ADDENDUM_D.startswith(we.ADDENDUM_C) and "proposed_checks" in we.ADDENDUM_D
    assert "expected value" in we.ADDENDUM_D
    assert we.prompt(TASK, "t", "x", "D") != we.prompt(TASK, "t", "x", "C")
    assert we.R3_PROBE == {(SONNET, t, "D", 1) for t in ("black-5634", "attrs-1593", "click-3678", "pydantic-14052")}
    assert all(arm != "D" for _, _, arm, _ in we.r3_schedule(we.load_tasks()))    # the schedule's 100 runs are unchanged
    ctx = _r3(tmp_path, "D", model=SONNET)
    assert "reviewed G6" in we.run_task(ctx, TASK)["why"]         # the shipped commit lacks the refusal's new text
    ctx.g6 = {**ctx.g6, "commit": we.R3_D_G6 + "0" * 33}
    assert we.run_task(ctx, TASK)["status"] == "finished"
    assert "already" in we.run_task(ctx, TASK)["why"]
    ctx.model = HAIKU
    assert "schedules no run" in we.run_task(ctx, TASK)["why"]


def test_round3_loop_runs_tune_on_train_and_look_once_at_the_held_out_bugs(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "HARNESS", tmp_path)
    (tmp_path / "G1.txt").write_text("Read the issue twice.\n", encoding="utf-8")
    assert "Read the issue twice." in we.prompt(TASK, "t", "x", "G1")
    assert we.prompt(TASK, "t", "x", "H1") == we.prompt(TASK, "t", "x", "G1")
    assert we.prompt(TASK, "t", "x", "E") == we.prompt(TASK, "t", "x", "D")
    held, train = we.R3_HELD[0], "black-5634"
    assert train not in we.R3_HELD
    assert we.r3_loop_key((SONNET, train, "G1", 2)) and not we.r3_loop_key((SONNET, held, "G1", 1))
    assert we.r3_loop_key((SONNET, held, "H1", 1)) and not we.r3_loop_key((SONNET, train, "H1", 1))
    assert we.r3_loop_key((SONNET, held, "E", 1)) and we.r3_loop_key((SONNET, held, "A", 2))
    assert not we.r3_loop_key((SONNET, train, "G1", 3)) and not we.r3_loop_key((HAIKU, train, "G1", 1))


def test_distillation_refuses_text_naming_a_project_a_test_or_a_file():
    pytest.importorskip("distill")
    import distill
    tests, files = ["tests/x.py::test_blank_lines[1]"], ["src/module_y.py"]
    assert distill.named("Read the issue. Check the edge cases.", tests, files) == []
    assert distill.named("In Pydantic fix #14052", tests, files) == ["pydantic", "#14052"]
    assert distill.named("run test_blank_lines on module_y.py", tests, files) == ["module_y.py", "test_blank_lines"]


def test_round3_fable_cap_comes_from_the_budget_left(tmp_path):
    ctx = _r3(tmp_path, "A", model=FABLE)
    ctx.tasks = [TASK]
    for arm in ("A", "B", "C"):                               # Haiku's and Sonnet's runs of this bug, all ended
        for model, reps in ((HAIKU, 1), (SONNET, 2 if arm == "A" else 1)):
            for rep in range(1, reps + 1):
                r = ctx.archive.start("run", TASK.id, arm=arm, model=model, repeat=rep, agent=True)
                r.finish({"cost_usd": 10.0, "agent_started": True}, "finished")
    rec = we.run_task(ctx, TASK)                              # $30 left for Fable's 3 runs: $10 stop, so cap 9.52
    assert rec["status"] == "finished", rec
    claude = next(call for call in ctx.docker.calls if we.CLAUDE in call and "-p" in call)
    assert claude[claude.index("--max-budget-usd") + 1] == "9.52" and rec["cap_usd"] == pytest.approx(9.52)


def test_round3_plan_states_the_addendum_and_arm_as_text_as_the_runner_has_them():
    plan = PLAN.read_text(encoding="utf-8").split("### Round 3 (2026-10-30): formal methods", 1)[1]
    assert "\n".join("      " + line for line in we.ADDENDUM_C.splitlines()) in plan
    second = we.PROMPT_A.split("\n\n")[1]
    assert " ".join(second.split()) in " ".join(plan.split())
    assert "development" in plan.lower() and "$100" in plan
