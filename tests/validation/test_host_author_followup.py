# SPDX-License-Identifier: Apache-2.0
"""The host session's review 4, its open findings removed at their roots (PLAN.md, 2026-11-04 night): one framing of
newline-delimited JSON, one recogniser of a model refusal, a value checked as well as a name, and a pack checked whole before
anything is sent. Fakes only: no docker, no Claude Code, no network, no model. The mirror script runs for real, with this Python,
on local folders."""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
WE_DIR = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(WE_DIR))

import box_mcp as box  # noqa: E402
import run as we  # noqa: E402

c = we.c
_spec = importlib.util.spec_from_file_location("host_author_helpers", Path(__file__).with_name("test_host_author.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
SONNET = helpers.SONNET
#: What ``str.splitlines`` splits at and a JSON string holds as it is (json.dumps escapes the control characters, not these).
SEPARATORS = "a\u2028b\u2029c\x85d"


# --- 1. one framing of newline-delimited JSON (review 4, finding 5: a class met before) ---------------------------------------
def test_review_4_finding_5_a_line_separator_in_model_text_does_not_change_a_finished_run_to_error(monkeypatch, tmp_path):
    lines = helpers.stream("s1")
    lines.insert(2, json.dumps({"type": "assistant", "session_id": "s1", "message": {
        "id": "text", "model": SONNET, "content": [{"type": "text", "text": "A JSON example:\u2028null\u2028end"}]}},
        ensure_ascii=False))
    record, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "", lines)])
    assert record["status"] == "finished", record.get("error")
    assert record["observed"].get("error") is None, record["observed"]


def _assistant(text: str, tool: str = "") -> dict:
    content = [{"type": "text", "text": text}] + ([{"type": "tool_use", "id": "u1", "name": tool, "input": {}}] if tool else [])
    return {"type": "assistant", "session_id": "s1", "message": {"id": "m1", "model": SONNET, "content": content,
                                                                 "usage": {"input_tokens": 1, "output_tokens": 1}}}


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")


def _archive_rows(tmp_path):
    archive = c.Archive(tmp_path / "a")
    archive.append({"event": "started", "run": "run/t/1", "note": SEPARATORS})
    return archive.rows(), [{"event": "started", "run": "run/t/1", "note": SEPARATORS}]


def _ledger(tmp_path):
    import distill
    row = {"run": "run/t/1", "status": "finished", "note": SEPARATORS}
    _jsonl(tmp_path / "ledger.jsonl", [row])
    return distill.main_row(tmp_path / "ledger.jsonl"), [row]


def _observed(tmp_path):
    import observe
    _jsonl(tmp_path / "stream.jsonl", [_assistant(SEPARATORS, "ToolSearch")])
    return observe.observe_run(tmp_path)["tool_use"]["tool_search"], 1


def _progress(tmp_path):
    archive = c.Archive(tmp_path / "a")
    _jsonl(archive.root / "run" / "t" / "1" / "stream.jsonl", [_assistant("x\u2028null\u2028y", "Grep")])
    got = we.progress(archive, "t")
    return (got["model_calls"], got["last_tool"]), (1, "Grep")


def _rounds_command(tmp_path, capsys):
    _jsonl(tmp_path / "a" / "run" / "t" / "1" / "stream.jsonl", [_assistant(SEPARATORS)])
    assert we.main(["rounds", "--archive", str(tmp_path / "a"), "--run", "run/t/1"]) == 0
    return json.loads(capsys.readouterr().out), []


def _egress(tmp_path):
    dest = tmp_path / "egress.jsonl"
    _jsonl(dest, [{"host": "api.anthropic.com", "port": 443, "allowed": True, "note": SEPARATORS}])
    got = we.egress(SimpleNamespace(docker=lambda *a, **kw: None), "px", dest)
    return got, {"requests": 1, "allowed": ["api.anthropic.com:443"], "refused": []}


def _checks(tmp_path):
    import export_reviewer_cases as ex
    _jsonl(tmp_path / "runs.jsonl", [{"event": "started", "run": "check/t/1", "checked_run": "run/t/1", "note": SEPARATORS}])
    (tmp_path / "check" / "t" / "1").mkdir(parents=True)
    (tmp_path / "check" / "t" / "1" / "run.json").write_text(json.dumps({"checks": [{"patch": "final"}]}), encoding="utf-8")
    return ex._checks(tmp_path, "t"), {"run/t/1": [{"patch": "final"}]}


@pytest.mark.parametrize("reader", [_archive_rows, _ledger, _observed, _progress, _rounds_command, _egress, _checks])
def test_every_reader_of_newline_delimited_json_reads_a_record_whole_whatever_its_strings_hold(tmp_path, capsys, reader):
    got, expected = reader(tmp_path, capsys) if reader is _rounds_command else reader(tmp_path)
    assert got == expected


#: What the scan reads as splitting text into lines: str.splitlines, a stream's readline or readlines, a split at a line break
#: (``split``, ``rsplit``, ``re.split``), and a for over an open file or a standard stream.
STREAMS = {"stdin", "stdout", "stderr"}
#: The one framing of newline-delimited JSON (``wecommon.stream_lines``, and ``read_stream`` for a file): Claude Code's stream
#: and a host session's stderr, ``runs.jsonl``, the ledger and the egress log are all split by it, at "\n" alone.
FRAMING = ("wecommon.py", "stream_lines", "text.split('\\n')")
PIPE = "a pipe of bytes: readline and iteration split at b'\\n' alone, as stream_lines splits text"
ASCII_JSON = ("the last line a script of the worked examples printed with json.dumps and its default ensure_ascii, so no line "
              "separator can be inside it")
#: Every other line split in the worked examples, by (module, the function it is in, its source), with why it reads no
#: newline-delimited JSON, or reads it as the framing does.
NOT_A_STREAM = {
    ("box_mcp.py", "serve", "for ... in stdin"): "the box's JSON-RPC requests on standard input: " + PIPE,
    ("box_mcp.py", "main", "__doc__.splitlines"): "the module's own docstring",
    ("export_reviewer_cases.py", "context", "text.splitlines"): "a source file of the bug's repository, shown by its lines",
    ("final_review.py", "reproduction", "mask_paths(scrub(now.texts.get(case, ''))).splitlines"):
        "a failing test's report, as pytest wrote it",
    ("headroom.py", "main", "__doc__.splitlines"): "the module's own docstring",
    ("layers.py", "_tokens", "io.StringIO(body).readline"): "one line of Python source, given to Python's tokenizer",
    ("layers.py", "change", "text.split('\\n')"): "a patch, read as git apply reads it",
    ("library.py", "chunks", "re.split('\\\\n\\\\s*\\\\n', path.read_text('utf-8', errors='replace'))"):
        "Markdown, split into paragraphs",
    ("probe_replay.py", "summary", "output.strip().splitlines"): "pytest's own output",
    ("product_review.py", "touched", "patch.split('\\n')"): "a patch, read as git apply reads it",
    ("round2_g6.py", "patch", "old.splitlines"): "an edit's anchor, Python source, named in an error",
    ("run.py", "crashes", "output.splitlines"): "pytest's own output",
    ("run.py", "critic_report", "said[said.rfind('[codex]'):].split('\\n', 1)"): "Codex's own report, its first line dropped",
    ("run.py", "test_changes", "name_status.splitlines"): "git's --name-status output",
    ("run.py", "key_check", "out(done).splitlines"): ASCII_JSON,
    ("run.py", "hook_selftest", "out(ctx.docker.sh(agent, script, workdir=task.repo)).splitlines"):
        "the hook self-test's echo of numbers",
    ("run.py", "run_session", "proc.stdout.readline"): "a container session's stream: " + PIPE,
    ("run.py", "host_session.lines", "proc.stdout.readline"): "a host session's stream: " + PIPE,
    ("run.py", "_run_task", "out(ctx.docker.sh(agent, TREE_STATE, workdir=task.repo)).splitlines"):
        "TREE_STATE's printf of git's hashes and counts",
    ("run.py", "_run_task", "out(ctx.docker.sh(agent, ARM_STATE)).splitlines"): ASCII_JSON,
    ("run.py", "_run_task", "proc.stdout.readline"): "a container run's stream: " + PIPE,
    ("run.py", "g6replay", "out(done).splitlines"): ASCII_JSON,
    ("run.py", "modelcheck", "out(done).splitlines"): ASCII_JSON,
    ("run.py", "main", "__doc__.splitlines"): "the module's own docstring",
    ("selection.py", "main", "__doc__.splitlines"): "the module's own docstring",
}


def line_splits(source: str) -> list[tuple[str, str]]:
    """Every line split in a module's source (``STREAMS``, and the kinds above), as (the function it is in, its source)."""
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, where: str, opened: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            where, opened = (node.name if where == "<module>" else f"{where}.{node.name}"), set()
        if isinstance(node, (ast.With, ast.AsyncWith)):
            opened |= {item.optional_vars.id for item in node.items if isinstance(item.optional_vars, ast.Name)
                       and isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Name)
                       and item.context_expr.func.id == "open"}
        if isinstance(node, ast.Attribute) and node.attr in ("splitlines", "readlines", "readline"):
            found.append((where, ast.unparse(node)))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("split", "rsplit")
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, (str, bytes))):
            pattern = node.args[0].value
            pattern = pattern.decode("latin-1") if isinstance(pattern, bytes) else pattern
            if "\n" in pattern or "\\n" in pattern:
                found.append((where, ast.unparse(node)))
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            it = node.iter
            last = it.attr if isinstance(it, ast.Attribute) else it.id if isinstance(it, ast.Name) else None
            if (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "open") or last in STREAMS \
                    or (isinstance(it, ast.Name) and it.id in opened):
                found.append((where, "for ... in " + ast.unparse(it)))
        for child in ast.iter_child_nodes(node):
            visit(child, where, opened)

    visit(ast.parse(source), "<module>", set())
    return found


def test_the_scan_sees_each_kind_of_line_split():
    source = ("def f(t, p, s):\n    t.splitlines()\n    p.readlines()\n    t.split('\\n')\n    t.rsplit(b'\\r\\n')\n"
              "    import re\n    re.split(r'\\n+', t)\n    [x for x in open(p)]\n    for x in s.stdin:\n        pass\n"
              "    with open(p) as h:\n        for x in h:\n            pass\n    t.split(',')\n")
    assert [src for _, src in line_splits(source)] == [
        "t.splitlines", "p.readlines", "t.split('\\n')", "t.rsplit(b'\\r\\n')", "re.split('\\\\n+', t)", "for ... in open(p)",
        "for ... in s.stdin", "for ... in h"]


def test_every_line_split_in_the_worked_examples_is_the_one_framing_or_named_with_why_it_reads_no_stream():
    found = Counter((path.name, where, src) for path in sorted(WE_DIR.glob("*.py"))
                    for where, src in line_splits(path.read_text("utf-8")))
    assert found[FRAMING] == 1, "the one framing, wecommon.stream_lines, splits at '\\n' alone"
    other = set(found) - {FRAMING}
    assert not other - set(NOT_A_STREAM), f"a line split that is not the one framing: {sorted(other - set(NOT_A_STREAM))}"
    assert not set(NOT_A_STREAM) - other, f"named but no longer there: {sorted(set(NOT_A_STREAM) - other)}"
    assert all(why.strip() for why in NOT_A_STREAM.values())


def test_the_one_framing_splits_at_a_newline_alone_and_a_file_is_read_with_no_newline_translation(tmp_path):
    text = json.dumps({"t": SEPARATORS}, ensure_ascii=False) + "\n" + json.dumps({"u": 1}) + "\n"
    assert c.stream_lines(text) == [json.dumps({"t": SEPARATORS}, ensure_ascii=False), json.dumps({"u": 1}), ""]
    assert len(text.splitlines()) > 2
    (tmp_path / "s.jsonl").write_bytes(b'{"a": 1}\r\nnot json, a lone \r inside\n' + text.encode("utf-8"))
    assert c.read_stream(tmp_path / "s.jsonl") == ['{"a": 1}\r', "not json, a lone \r inside", *c.stream_lines(text)]


# --- 2. one recogniser of a model refusal (review 4, finding 4) ----------------------------------------------------------------
INIT = {"type": "system", "subtype": "init", "tools": sorted(we.HOST_TOOLS), "mcp_servers": [{"name": "box"}]}
SAID_NO = {"type": "assistant", "message": {"id": "refused", "model": SONNET, "stop_reason": "refusal",
                                             "content": [{"type": "text", "text": "I cannot help with that request."}]}}


def test_review_4_finding_4_an_assistant_refusal_stop_reason_ends_model_refused(tmp_path):
    stream = tmp_path / "stream.jsonl"
    records = [INIT, SAID_NO, {"type": "result", "subtype": "success", "is_error": True, "result": "I cannot help."}]
    stream.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    facts = we.host_facts(stream, SONNET)
    ended, _ = we.host_ending("", facts, code=0, limit=None, by_g6=False)
    assert ended == "model_refused", "assistant stop_reason=refusal was treated as an execution error"
    assert facts["refusals"] == [{"stop_reason": "refusal", "model": SONNET}]


REFUSAL_FALLBACK = helpers.REFUSAL
#: Records of every kind V7's ``run_arm.Tally`` reads, each with the answer to "the model refused".
V7_SHAPES = [
    (SAID_NO, True),
    ({"type": "assistant", "message": {"id": "a", "stop_reason": "refusal", "content": []}}, True),
    ({"type": "assistant", "message": {"id": "b", "stop_reason": "end_turn", "content": []}}, False),
    ({"type": "assistant", "message": {"id": "c", "stop_reason": "tool_use", "content": []}}, False),
    ({"type": "assistant", "message": {"id": "d", "stop_reason": None, "content": []}}, False),
    ({"type": "assistant", "message": {"id": "e", "content": []}}, False),
    ({"type": "assistant"}, False),
    ({"type": "assistant", "message": {"id": "f", "stop_reason": "Refusal", "content": []}}, False),
    ({"type": "user", "message": {"stop_reason": "refusal"}}, False),
    ({"type": "result", "subtype": "success", "is_error": True, "stop_reason": "refusal"}, False),
    (INIT, False),
]


@pytest.mark.parametrize(("record", "refused"), V7_SHAPES)
def test_the_recogniser_and_v7s_own_give_one_answer_on_every_record_v7_reads(record, refused):
    from v7_swebench import run_arm      # read only: V7 is paused, its code stays as it ran
    tally = run_arm.Tally()
    tally.feed(record)
    assert tally.refusal is refused
    assert (we.model_refusal(record) is not None) is refused


def test_the_recogniser_reads_claude_codes_refusal_record_too():
    assert we.model_refusal(REFUSAL_FALLBACK) == {k: REFUSAL_FALLBACK.get(k) for k in
                                                  ("subtype", "apiRefusalCategory", "originalModel", "fallbackModel")}
    assert we.model_refusal({"type": "system", "subtype": "compact_boundary"}) is None
    assert we.model_refusal(None) is None and we.model_refusal("refusal") is None and we.model_refusal([SAID_NO]) is None


def test_the_word_refusal_and_the_stop_reason_are_read_only_by_the_recogniser():
    holders = set()
    for path in sorted(WE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(isinstance(n, ast.Constant) and n.value in ("refusal", "stop_reason") for n in ast.walk(fn)):
                    holders.add((path.name, fn.name))
    assert holders == {("run.py", "model_refusal")}


@pytest.mark.parametrize("arm", ["A", "K2"])
def test_an_assistant_refusal_ends_a_host_run_model_refused_scored_on_its_patch_and_never_resumed(monkeypatch, tmp_path, arm):
    helpers.k2_reviews(monkeypatch, lambda n: pytest.fail("a run a refusal ended starts no review and no repair"))
    lines = helpers.stream("s1", extra=(SAID_NO,))
    record, _, starts, ctx, *_ = helpers.host_run(monkeypatch, tmp_path, arm, [("s1", "def f():\n    return 1\n", lines),
                                                                              ("s1", "", None)])
    assert record["status"] == "model_refused" and len(starts) == 1 and "why" not in record
    assert record["model_refusals"] == [{"stop_reason": "refusal", "model": SONNET}] and c.scoreable(record)
    assert (ctx.archive.root / record["run"] / "final.patch").read_text("utf-8") == "def f():\n    return 1\n"


def test_an_init_refusal_still_ends_refused_whatever_the_model_said(monkeypatch, tmp_path):
    lines = helpers.stream("s1", tools=[*helpers.TOOLS, "Bash"], extra=(SAID_NO,))
    record, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "", lines)])
    assert record["status"] == "refused" and record.get("why") and not c.scoreable(record)


# --- 3. a value is checked as well as a name (review 4, finding 1) -------------------------------------------------------------
TOKEN = "review-only-proxy-token-12345678"


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"])
@pytest.mark.parametrize("value", [f"http://{TOKEN}:unused@proxy.invalid:8080", f"http://{TOKEN}@proxy.invalid:8080",
                                   f"https://user:{TOKEN}@proxy.invalid", f"{TOKEN}:pw@proxy.invalid:3128"])
def test_review_4_finding_1_no_proxy_credential_reaches_the_host_session(name, value):
    env = we.host_env({"PATH": "p", name: value})
    assert all(TOKEN not in v for v in env.values()), "proxy authentication token reached host environment"
    assert env == {"PATH": "p", **helpers.FIXED}


@pytest.mark.parametrize(("name", "value"), [("DOCKER_HOST", f"ssh://{TOKEN}@host.invalid"),
                                             ("docker_host", f"tcp://u:{TOKEN}@host.invalid:2376"),
                                             ("NODE_EXTRA_CA_CERTS", f"https://u:{TOKEN}@host.invalid/ca.pem"),
                                             ("NO_PROXY", f"localhost,http://u:{TOKEN}@host.invalid")])
def test_a_url_with_a_user_or_password_in_any_allowed_variable_is_dropped(name, value):
    assert we.host_env({"PATH": "p", name: value}) == {"PATH": "p", **helpers.FIXED}


def test_an_allowed_value_with_no_credential_passes_unchanged():
    kept = {"HTTPS_PROXY": "http://proxy.invalid:8080", "http_proxy": "http://proxy.invalid:3128/", "HTTP_PROXY": "proxy.invalid:80",
            "NO_PROXY": "localhost,127.0.0.1,.corp.invalid", "DOCKER_HOST": "npipe:////./pipe/docker_engine",
            "PATH": r"C:\Users\a@b\bin;C:\tools", "USERPROFILE": r"C:\Users\a@b", "SSL_CERT_FILE": r"C:\certs\ca@corp.pem"}
    assert we.host_env(kept) == {**kept, **helpers.FIXED}


def test_every_allowed_variable_read_as_a_url_has_its_value_checked_for_any_at_sign():
    assert we.HOST_ENV_URLS <= we.HOST_ENV_NAMES
    assert {n for n in we.HOST_ENV_NAMES if n.endswith("_PROXY") and n != "NO_PROXY"} | {"DOCKER_HOST"} == we.HOST_ENV_URLS


# --- 4. a pack is checked whole before anything is sent (review 4, findings 2 and 6) -------------------------------------------
def _mirror_docker(repo: Path, prefix: str = ""):
    """A docker that runs the real MIRROR on a local folder (after ``prefix``, a fake of the container's file system), and
    answers a command with the call's own start line."""
    calls = []

    def docker(*args, input=None, timeout=None, merged=False):
        calls.append(args)
        if box.MIRROR in args:
            return subprocess.run([sys.executable, "-I", "-c", prefix + box.MIRROR, str(repo), "tester"], input=input,
                                  capture_output=True, timeout=30)
        return helpers.ok(args[-2].encode() + b"\nran\n")
    return docker, calls


NAME_MAX_255 = (
    "import os, errno\n"
    "original_open = os.open\n"
    "def container_open(path, *a, **kw):\n"
    "    if len(os.path.basename(path).encode('utf-8')) > 255:\n"
    "        raise OSError(errno.ENAMETOOLONG, 'File name too long', path)\n"
    "    return original_open(path, *a, **kw)\n"
    "os.open = container_open\n")


def test_review_4_finding_6_an_unsendable_name_is_refused_before_any_mirror_write(tmp_path):
    workspace, repo = tmp_path / "ws", tmp_path / "repo"
    workspace.mkdir()
    repo.mkdir()
    (workspace / "a.txt").write_text("new", encoding="utf-8")
    (repo / "a.txt").write_text("old", encoding="utf-8")
    (workspace / ("\u754c" * 86)).write_text("content", encoding="utf-8")      # 86 UTF-16 units, 258 UTF-8 bytes
    docker, calls = _mirror_docker(repo, NAME_MAX_255)
    opts = SimpleNamespace(container="fake", repo="/w/repo", workspace=str(workspace), user="tester", timeout=5)
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, docker)
    assert got["isError"] is True and "File name too long" in got["content"][0]["text"]
    assert calls == [], "an unsendable workspace runs no command"
    assert (repo / "a.txt").read_text("utf-8") == "old", "unsendable workspace partly changed the container"


def test_a_name_part_of_255_bytes_is_sent_and_one_of_256_is_refused(tmp_path):
    ws = tmp_path / "ws"
    (ws / ("\u754c" * 85)).mkdir(parents=True)          # 255 bytes
    (ws / ("\u754c" * 85) / "a.py").write_text("x\n", encoding="utf-8")
    names = tarfile.open(fileobj=io.BytesIO(box.pack(ws, "/w/r"))).getnames()
    assert names == ["\u754c" * 85, "\u754c" * 85 + "/a.py"]
    (ws / ("\u754c" * 85 + "x")).write_text("y\n", encoding="utf-8")   # 256 bytes
    with pytest.raises(box.Refused, match="File name too long"):
        box.pack(ws, "/w/r")


def test_a_path_the_container_cannot_open_is_refused_by_its_whole_length(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("x\n", encoding="utf-8")
    fits = "/" + "r" * (box.PATH_MAX - 1 - len("/a.txt") - 1)       # "<repo>/a.txt" is PATH_MAX - 1 bytes: with its NUL, PATH_MAX
    assert len(f"{fits}/a.txt".encode()) == box.PATH_MAX - 1
    assert tarfile.open(fileobj=io.BytesIO(box.pack(ws, fits))).getnames() == ["a.txt"]
    with pytest.raises(box.Refused, match="File name too long"):
        box.pack(ws, fits + "r")
    sent = []
    with pytest.raises(box.Refused):
        box.mirror(ws, lambda *a, **kw: sent.append(a), container="ctr", repo=fits + "r", user="tester", timeout=5)
    assert sent == []


def test_a_name_that_is_not_utf_8_is_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("x\n", encoding="utf-8")
    try:
        (ws / "bad\udc80.txt").write_text("x\n", encoding="utf-8")      # a lone surrogate: a name Windows holds, UTF-8 cannot
    except (OSError, UnicodeError):
        pytest.skip("this file system cannot hold a name with a lone surrogate")
    with pytest.raises(box.Refused, match="not UTF-8"):
        box.pack(ws, "/w/r")


def test_review_4_finding_2_a_file_added_after_the_walk_refuses_the_pack(monkeypatch, tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "old.txt").write_text("old", encoding="utf-8")
    checked = box._same_folder

    def add_after_walk(path, st, rel):
        (root / "new.txt").write_text("new", encoding="utf-8")
        return checked(path, st, rel)

    monkeypatch.setattr(box, "_same_folder", add_after_walk)
    try:
        data = box.pack(root)
    except box.Refused:
        return
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        sent = {m.name for m in tar.getmembers() if m.isfile()}
    assert sent == {p.name for p in root.iterdir() if p.is_file()}, "pack accepted a changed folder and silently omitted new.txt"


@pytest.mark.parametrize("change", ["same size, new bytes", "removed", "a folder made", "a file in a folder", "a file for a folder"])
def test_a_tree_changed_while_it_was_packed_is_refused_and_the_next_call_sends_it(monkeypatch, tmp_path, change):
    ws = tmp_path / "ws"
    helpers.write(ws, {"a.py": "one\n", "pkg/b.py": "two\n", "pkg2/c.py": "three\n"})
    checked, done = box._same_folder, []

    def changes(path, st, rel):
        if not done:
            done.append(change)
            if change == "same size, new bytes":
                (ws / "a.py").write_bytes((ws / "a.py").read_bytes().upper())
            elif change == "removed":
                (ws / "pkg" / "b.py").unlink()
            elif change == "a folder made":
                (ws / "pkg3").mkdir()
            elif change == "a file in a folder":
                (ws / "pkg2" / "d.py").write_text("four\n", encoding="utf-8")
            else:
                (ws / "pkg2" / "c.py").unlink()
                (ws / "pkg2" / "c.py").mkdir()
        return checked(path, st, rel)

    monkeypatch.setattr(box, "_same_folder", changes)
    sent = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, lambda *a, **kw: (sent.append(a), helpers.ok(b"{}"))[1])
    assert done and got["isError"] is True and sent == [] and "changed while the workspace was packed" in got["content"][0]["text"]
    again = tarfile.open(fileobj=io.BytesIO(box.pack(ws)))           # the next call: the tree as it is now
    names = {m.name: m for m in again.getmembers()}
    if change == "same size, new bytes":
        assert again.extractfile(names["a.py"]).read() == (ws / "a.py").read_bytes() and b"ONE" in (ws / "a.py").read_bytes()
    elif change == "removed":
        assert "pkg/b.py" not in names
    elif change == "a folder made":
        assert names["pkg3"].isdir()
    elif change == "a file in a folder":
        assert "pkg2/d.py" in names
    else:
        assert names["pkg2/c.py"].isdir()


def test_an_unchanged_tree_with_ordinary_names_packs_and_mirrors_as_before(tmp_path):
    ws, repo = tmp_path / "ws", tmp_path / "repo"
    helpers.write(ws, {"a.py": "new\n", "sub/b.py": "same\n", "sub/deep/c.py": "c\n", ".git/config": "x", ".venv/x.py": "v"})
    helpers.write(repo, {"a.py": "old\n", "sub/b.py": "same\n", "gone.py": "g\n", ".git/HEAD": "ref\n"})
    first = box.pack(ws, "/w/repo")
    assert first == box.pack(ws, "/w/repo") == box.pack(ws)
    assert tarfile.open(fileobj=io.BytesIO(first)).getnames() == ["sub", "a.py", "sub/deep", "sub/b.py", "sub/deep/c.py"]
    docker, calls = _mirror_docker(repo)
    opts = SimpleNamespace(container="ctr", repo="/w/repo", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "pytest -q"}}, docker)
    assert got["isError"] is False and got["content"][0]["text"] == "exit code 0\nran\n"
    assert [box.MIRROR in a for a in calls] == [True, False]
    assert (repo / "a.py").read_text() == "new\n" and (repo / "sub" / "deep" / "c.py").read_text() == "c\n"
    assert not (repo / "gone.py").exists() and (repo / ".git" / "HEAD").read_text() == "ref\n"
