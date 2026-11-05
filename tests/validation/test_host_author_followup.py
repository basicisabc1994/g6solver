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
import os
import shutil
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

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


# --- 1. one framing of newline-delimited JSON (review 4, finding 5: a class met before), each reader held to it by behaviour ---
def test_review_4_finding_5_a_line_separator_in_model_text_does_not_change_a_finished_run_to_error(monkeypatch, tmp_path):
    lines = helpers.stream("s1")
    lines.insert(2, json.dumps({"type": "assistant", "session_id": "s1", "message": {
        "id": "text", "model": SONNET, "content": [{"type": "text", "text": "A JSON example:\u2028null\u2028end"}]}},
        ensure_ascii=False))
    record, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "", lines)])
    assert record["status"] == "finished", record.get("error")
    assert record["observed"].get("error") is None, record["observed"]


#: What a JSON string can hold that a split other than at "\n" would cut: U+2028, U+2029 and U+0085, which json.dumps writes as
#: they are, and a lone "\r" and "\r\n", which it escapes.
HOSTILE = "a\u2028b\u2029c\x85d\re\r\nf"


def _assistant(text: str, tool: str = "", mid: str = "m1") -> dict:
    content = [{"type": "text", "text": text}] + ([{"type": "tool_use", "id": f"u-{mid}", "name": tool, "input": {}}] if tool else [])
    return {"type": "assistant", "session_id": "s1", "message": {"id": mid, "model": SONNET, "content": content,
                                                                 "usage": {"input_tokens": 1, "output_tokens": 1}}}


def _ndjson(path: Path, rows: list[dict]) -> Path:
    """``rows`` as a newline-delimited JSON file at its hardest to frame: each row's strings hold ``HOSTILE`` as json.dumps writes
    them with ensure_ascii off, and every second row also holds a raw "\\r" between its tokens and ends "\\r\\n", as a file
    written with Windows line endings does."""
    text = "".join("{\r" + json.dumps(row, ensure_ascii=False)[1:] + "\r\n" if i % 2 else json.dumps(row, ensure_ascii=False) + "\n"
                   for i, row in enumerate(rows))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    data = path.read_bytes()
    assert len(rows) >= 2 and b"{\r" in data and b"\r\n" in data and all(ch.encode() in data for ch in "\u2028\u2029\x85")
    return path


# Each reader of a file ``wecommon.NDJSON`` names, as the names' uses and the writers' call sites reach it (``READERS``): fed the
# file at its hardest to frame, it reads every record whole and in order. Each returns (what it read, what was written).
def _index_rows(tmp_path, capsys, monkeypatch):
    archive = c.Archive(tmp_path / "a")
    assert archive.index == archive.root / c.NDJSON["index"]
    rows = [{"event": "started", "run": "run/t/1", "note": HOSTILE}, {"event": "ended", "run": "run/t/1", "note": HOSTILE}]
    _ndjson(archive.index, rows)
    archive.append({"event": "started", "run": "run/t/2", "note": HOSTILE})            # and a row as its writer writes it
    return archive.rows(), [*rows, {"event": "started", "run": "run/t/2", "note": HOSTILE}]


def _index_checks(tmp_path, capsys, monkeypatch):
    import export_reviewer_cases as ex
    _ndjson(tmp_path / c.NDJSON["index"],
            [{"event": "started", "run": f"check/t/{i}", "checked_run": f"run/t/{i}", "note": HOSTILE} for i in (1, 2, 3)])
    for i in (1, 2, 3):
        (tmp_path / "check" / "t" / str(i)).mkdir(parents=True)
        (tmp_path / "check" / "t" / str(i) / "run.json").write_text(json.dumps({"checks": [{"patch": f"p{i}"}]}), encoding="utf-8")
    return list(ex._checks(tmp_path, "t").items()), [(f"run/t/{i}", [{"patch": f"p{i}"}]) for i in (1, 2, 3)]


def _ledger(tmp_path, capsys, monkeypatch):
    import distill                    # the ledger's reader (loop.one recovers a made run from the index: study review 2, 2)
    archive = c.Archive(tmp_path / "a")
    rows = [{"run": f"run/t/{i}", "status": "finished", "note": HOSTILE} for i in (1, 2)]
    _ndjson(archive.root / c.NDJSON["ledger"], rows)
    wrote = we.r3_ledger(archive, {"run": "run/t/3", "task": HOSTILE, "status": "finished"})     # a row as its writer writes it
    return distill.main_row(archive.root / c.NDJSON["ledger"]), [*rows, wrote]


def _egress(tmp_path, capsys, monkeypatch):
    rows = [{"host": "api.anthropic.com", "port": 443, "allowed": True, "note": HOSTILE},
            {"host": "x.invalid", "port": 80, "allowed": False, "note": HOSTILE},
            {"host": "y.invalid", "port": 80, "allowed": False, "note": HOSTILE}]
    dest, copied = _ndjson(tmp_path / c.NDJSON["egress"], rows), []
    got = we.egress(SimpleNamespace(docker=lambda *a, **kw: (copied.append(a),            # a copy that succeeded, as docker
                                                             SimpleNamespace(returncode=0, stdout=b"", stderr=b""))[1]),
                    "px", dest)                                                     # says it (study review 2: its status is read)
    assert copied == [("cp", f"px:/opt/{c.NDJSON['egress']}", str(dest))], "the log is copied out from where the proxy writes it"
    return got, {"requests": 3, "allowed": ["api.anthropic.com:443"], "refused": ["x.invalid:80", "y.invalid:80"]}


def _host_facts(tmp_path, capsys, monkeypatch):
    limit = {"type": "result", "subtype": "error_during_execution", "is_error": True,
             "result": "You've hit your limit\u2029\r resets 5pm"}       # its reset after a separator: a split there loses it
    rows = [json.loads(line) for line in helpers.stream("s1", extra=(_assistant(HOSTILE, mid="m2"), limit))]
    facts = we.host_facts(_ndjson(tmp_path / c.NDJSON["stream"], rows), SONNET)
    return ({k: facts[k] for k in ("init", "result", "limit", "models", "refusals")},
            {"init": we.host_init(rows[0]), "result": rows[-1], "limit": "resets 5pm", "models": {SONNET}, "refusals": []})


def _host_stream(tmp_path, capsys, monkeypatch):
    """A host session's stream, from its pipe (``host_session``) into its file, and read back by ``_run_task``: a record with a
    raw "\\r" between its tokens and a line ending "\\r\\n" among them."""
    records = [json.loads(line) for line in helpers.stream("s1", extra=(_assistant(HOSTILE, "Grep", "m2"), _assistant(HOSTILE, mid="m3")))]
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    lines[2] = "{\r" + lines[2][1:] + "\r"
    record, _, _, ctx, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "", lines)])
    saved = c.read_stream(ctx.archive.root / record["run"] / c.NDJSON["stream"])
    return ((record["status"], record["claude_version"], record["model_calls"], [json.loads(line) for line in saved if line.strip()]),
            ("finished", "2.1.318", 3, records))


def _host_stderr(tmp_path, capsys, monkeypatch):
    """A host session's stderr, saved beside its stream and read as it is written (``host_session``): a limit whose reset follows
    a line separator and a lone "\\r" keeps its reset, which a split at either would cut off."""
    said = json.dumps({"note": HOSTILE}, ensure_ascii=False) + "\n" + "You've hit your limit\u2028\r resets 5pm\r\n"
    plain = helpers.starter

    def starter(sessions):
        start, starts = plain(sessions)

        def with_stderr(*args, **kw):
            proc = start(*args, **kw)
            proc.stderr = io.BytesIO(said.encode("utf-8"))
            return proc
        return with_stderr, starts

    monkeypatch.setattr(helpers, "starter", starter)
    record, _, _, ctx, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "", None)])
    saved = ctx.archive.root / record["run"] / c.NDJSON["stderr"].format(stem=Path(c.NDJSON["stream"]).stem)
    return ((record["status"], record["limit"], saved.read_bytes()),
            ("limited", {"stream": c.NDJSON["stream"], "resets": "resets 5pm"}, said.encode("utf-8")))


def _progress(tmp_path, capsys, monkeypatch):
    archive = c.Archive(tmp_path / "a")
    _ndjson(archive.root / "run" / "t" / "1" / c.NDJSON["stream"], [_assistant(HOSTILE, "Grep", "m1"), _assistant(HOSTILE, "Read", "m2")])
    got = we.progress(archive, "t")
    return (got["model_calls"], got["last_tool"]), (2, "Read")


def _rounds_command(tmp_path, capsys, monkeypatch):
    _ndjson(tmp_path / "a" / "run" / "t" / "1" / c.NDJSON["stream"], [_assistant(HOSTILE, mid="m1"), _assistant(HOSTILE, mid="m2")])
    assert we.main(["rounds", "--archive", str(tmp_path / "a"), "--run", "run/t/1"]) == 0
    return json.loads(capsys.readouterr().out), []


def _observed(tmp_path, capsys, monkeypatch):
    import observe
    _ndjson(tmp_path / c.NDJSON["stream"], [_assistant(HOSTILE, "ToolSearch", "m1"), _assistant(HOSTILE, "ToolSearch", "m2")])
    return observe.observe_run(tmp_path)["tool_use"]["tool_search"], 2


#: Each reader of a file ``wecommon.NDJSON`` names, by (module, function), with its case above.
READERS = {("wecommon.py", "Archive.rows"): _index_rows, ("export_reviewer_cases.py", "_checks"): _index_checks,
           ("distill.py", "main_row"): _ledger, ("run.py", "egress"): _egress, ("run.py", "host_facts"): _host_facts,
           ("run.py", "_run_task"): _host_stream, ("run.py", "host_session"): _host_stderr, ("run.py", "progress"): _progress,
           ("run.py", "main"): _rounds_command, ("observe.py", "observe_run"): _observed}


@pytest.mark.parametrize("reader", sorted(READERS), ids=lambda r: f"{r[0]}:{r[1]}")
def test_every_reader_of_a_newline_delimited_json_file_reads_each_record_whole_and_in_order(tmp_path, capsys, monkeypatch, reader):
    got, written = READERS[reader](tmp_path, capsys, monkeypatch)
    assert got == written


# --- 1b. the files are named once, from their writers, and read only through the one framing (fix review 1) -------------------
#: The calls that read a newline-delimited JSON file through the one framing.
FRAMED = frozenset({"read_stream", "stream_lines"})
#: The end of each name ``wecommon.NDJSON`` holds, after its last field: a literal that holds one names one of those files.
TAILS = frozenset(name.rsplit("}", 1)[-1] for name in c.NDJSON.values())
#: A function that names one of those files by a literal, with why it does not take the name from ``NDJSON``.
LITERAL_NAMES = {
    ("stage3_domain.py", "domain"): "a domain file g6.metaharness.runner loads by its path, so it imports nothing of the worked "
                                    "examples; it hands the export's two cases.jsonl to CodingReplay, which is not in this checkout",
}
HANDED = "a reader with its own case in READERS"
SESSION = "a Claude Code session's stream, which run_session writes from its pipe; nothing reads it back"
#: Every use of ``wecommon.NDJSON`` in the worked examples, by (module, the function it is in, the kind it names): for each use in
#: order, the call it is passed into (None: no call) and what that does with the file. One passed into ``read_stream`` or
#: ``stream_lines`` reads it through the one framing.
NDJSON_USES = {
    ("wecommon.py", "Archive.index", "index"):
        [(None, "the index's one path: Archive.append writes it and Archive.rows reads it with read_stream, " + HANDED)],
    ("export_reviewer_cases.py", "main", "cases"): [(None, "the cases written below, a json.dumps line a case")] * 2,
    ("observe.py", "observe_run", "stream"): [("read_stream", "read")],
    ("run.py", "r3_ledger", "ledger"): [("open", "written: a json.dumps line a run")],
    ("run.py", "start_net", "egress"): [("docker", "where the egress proxy writes its log, in its own container")],
    ("run.py", "egress", "egress"): [("copy_out", "copied out of the proxy's container to the file egress then reads")],
    ("run.py", "host_session", "stderr"): [("write", "written as it is read: by stream_lines, " + HANDED)],
    ("run.py", "run_scout", "tagged_stream"): [("session", SESSION)] * 3,
    ("run.py", "run_scout", "numbered_stream"): [("checked_again", SESSION)] * 3,
    ("run.py", "repair_loop.ask", "numbered_stream"): [("run_session", SESSION)],
    ("run.py", "reviews_until_clean", "numbered_stream"):
        [("run_session", SESSION), ("session", "a host repair's stream: host_session writes it and host_facts reads it, " + HANDED)],
    ("run.py", "review_once", "numbered_stream"): [("run_session", SESSION)],
    ("run.py", "rule_step", "tagged_stream"):
        [("run_session", SESSION), ("session", "a host rule step's stream: host_session writes it and host_facts reads it, " + HANDED)],
    ("run.py", "reviews_with_bug_test", "numbered_stream"): [("run_session", SESSION)],
    ("run.py", "_run_task", "stream"):
        [("turn", "the host session's stream: host_session writes it and host_facts reads it, " + HANDED),
         ("open", "the container session's stream, written from its pipe"), ("read_stream", "read")],
    ("run.py", "_run_task", "egress"): [("egress", HANDED)],
    ("run.py", "modelcheck", "egress"): [("egress", HANDED)],
    ("run.py", "progress", "stream"): [(None, "the run's stream, read with read_stream when it is a file, " + HANDED)],
    ("run.py", "main", "stream"): [("read_stream", "read")],
}


def _docstrings(tree: ast.AST) -> set[int]:
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body
            and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}


def _bound_to_ndjson(tree: ast.AST) -> set[str]:
    """Every name a module binds to ``wecommon.NDJSON`` (fix review 2, finding 1): the name itself, an import alias
    (``from wecommon import NDJSON as names``), and a plain assignment to a name of the constant or of a name bound to it
    (``names = NDJSON``, ``names = c.NDJSON``). An attribute named NDJSON (``w.NDJSON`` after ``import wecommon as w``) is the
    constant whatever holds it."""
    bound = {"NDJSON"} | {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                          and node.module == "wecommon" for alias in node.names if alias.name == "NDJSON"}
    assigned = [(targets, node.value) for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
                and node.value is not None
                for targets in [node.targets if isinstance(node, ast.Assign) else [node.target]]]
    while True:
        more = {target.id for targets, value in assigned if (isinstance(value, ast.Name) and value.id in bound)
                or (isinstance(value, ast.Attribute) and value.attr == "NDJSON")
                for target in targets if isinstance(target, ast.Name)} - bound
        if not more:
            return bound
        bound |= more


def _names_and_uses(folder: Path) -> tuple[Counter, dict[tuple[str, str, str], list[list[str]]]]:
    """In every module under ``folder``: each literal that holds one of ``TAILS`` (its docstrings and the constant's own
    definition left out), by (module, function); and each use of ``NDJSON``, by (module, function, kind), with the names of the
    calls it is passed into, in order. A use whose kind is not a literal is kind "?"; the constant itself, not indexed, "*"."""
    literals: Counter = Counter()
    uses: dict[tuple[str, str, str], list[list[str]]] = {}
    for path in sorted(folder.rglob("*.py")):
        module = path.relative_to(folder).as_posix()
        tree = ast.parse(path.read_text("utf-8"))
        skip, bound = _docstrings(tree), _bound_to_ndjson(tree)

        def constant(node: ast.AST) -> bool:
            return (isinstance(node, ast.Name) and node.id in bound and isinstance(node.ctx, ast.Load)) or (
                isinstance(node, ast.Attribute) and node.attr == "NDJSON")

        def visit(node: ast.AST, where: str, calls: list[str]) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                where = node.name if where == "<module>" else f"{where}.{node.name}"
            if module == "wecommon.py" and isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                    isinstance(t, ast.Name) and t.id == "NDJSON" for t in (node.targets if isinstance(node, ast.Assign) else [node.target])):
                return
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip \
                    and any(tail in node.value for tail in TAILS):
                literals[(module, where)] += 1
            if isinstance(node, ast.Subscript) and constant(node.value):
                kind = node.slice.value if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) else "?"
                uses.setdefault((module, where, kind), []).append(list(calls))
                return
            if constant(node):
                uses.setdefault((module, where, "*"), []).append(list(calls))
            if isinstance(node, ast.Call):
                func = node.func
                calls = [*calls, func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else "?"]
            for child in ast.iter_child_nodes(node):
                visit(child, where, calls)

        visit(tree, "<module>", [])
    return literals, uses


def _guard(folder: Path) -> None:
    literals, uses = _names_and_uses(folder)
    named = sorted(key for key in literals if key not in LITERAL_NAMES)
    assert not named, f"a newline-delimited JSON file named by a literal, not from wecommon.NDJSON: {named}"
    assert set(LITERAL_NAMES) <= set(literals), f"named but no longer there: {sorted(set(LITERAL_NAMES) - set(literals))}"
    new, gone = sorted(set(uses) - set(NDJSON_USES)), sorted(set(NDJSON_USES) - set(uses))
    assert not new, f"a newline-delimited JSON file's name used where no entry says what is done with the file: {new}"
    assert not gone, f"a newline-delimited JSON file's name listed where it is no longer used: {gone}"
    stray = sorted(key for key in uses if key[2] == "index" and key[:2] != ("wecommon.py", "Archive.index"))
    assert not stray, (f"the index is read only through Archive.rows (its torn-tail rule included), and its name is used only by "
                       f"Archive.index, even where a listed entry says it is read: {stray}")
    for key, found in uses.items():
        listed = NDJSON_USES[key]
        assert len(found) == len(listed), f"a newline-delimited JSON file's name used {len(found)} times in {key}, listed {len(listed)}"
        for calls, (callee, why) in zip(found, listed):
            held = not calls if callee is None else callee in calls
            assert held, f"a newline-delimited JSON file's path in {key} is passed into {calls or 'no call'}, not {callee}: {why}"


def test_every_newline_delimited_json_file_is_named_from_its_writers_constant_and_read_only_through_the_one_framing():
    """Fix review 1, the design replaced: the guard is built from the producers' own list (``wecommon.NDJSON``), not from a scan
    of every way code can split text. Every name of those files in the worked examples comes from the constant (a literal that
    holds one is refused); every use of the constant is listed with the call it is passed into, and one listed as read must be
    passed into ``read_stream`` or ``stream_lines``; each reader those uses reach has a behavioural case (``READERS``). A use is
    found under any name the module binds the constant to (``_bound_to_ndjson``).

    The guarantee rests on the behavioural reader tests; this guard is a lint over them, which makes a new reader of those files
    visible so it gets a case of its own. Its limits, stated: the constant reached through getattr, a name built some other way
    (from parts that hold no name's end, or from data), a reader whose name comes from the constant indirectly, through a function
    or a property that returns a path built from it (a new reader of ``Archive(root).index``: fix review 3, finding 2, rejected by
    ruling), a reader outside this folder (``CodingReplay`` in g6.metaharness, which reads the exported cases), and the egress
    proxy, which writes its log where it is told, are out of its scope."""
    _guard(WE_DIR)
    read = {(m, f) for (m, f, _), listed in NDJSON_USES.items() if any(callee in FRAMED for callee, _ in listed)}
    assert read <= set(READERS), f"a reader with no behavioural case: {sorted(read - set(READERS))}"
    assert all(why.strip() for listed in NDJSON_USES.values() for _, why in listed)


def _shadow(tmp_path: Path) -> Path:
    shadow = tmp_path / "worked_examples"
    shadow.mkdir()
    for source in WE_DIR.glob("*.py"):
        (shadow / source.name).write_bytes(source.read_bytes())
    return shadow


@pytest.mark.parametrize(("name", "source", "said"), [
    ("readers/transcript.py", "def read(folder):\n    return (folder / 'stream.jsonl').read_text('utf-8').splitlines()\n",
     "named by a literal"),
    ("ledger_reader.py", "import wecommon as c\ndef read(root):\n    return open(root / c.NDJSON['ledger']).read().split('\\r')\n",
     "no entry says"),
    ("index_reader.py", "import wecommon as c\ndef read(root, kind):\n    return (root / c.NDJSON[kind]).read_bytes()\n",
     "no entry says")])
def test_a_new_reader_of_one_of_those_files_fails_the_guard_until_it_is_listed(tmp_path, name, source, said):
    shadow = _shadow(tmp_path)
    (shadow / name).parent.mkdir(parents=True, exist_ok=True)
    (shadow / name).write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError, match=said):
        _guard(shadow)


@pytest.mark.parametrize("source", [
    # fix review 2, finding 1: an import alias
    "from wecommon import NDJSON as filenames\ndef read(folder):\n    return (folder / filenames['stream']).read_text('utf-8').splitlines()\n",
    "from wecommon import NDJSON as a\nb = a\ndef read(folder):\n    return (folder / b['stream']).read_bytes()\n",
    "import wecommon as w\ndef read(folder):\n    return (folder / w.NDJSON['stream']).read_bytes()\n",
    "import wecommon\ndef read(folder):\n    names = wecommon.NDJSON\n    return (folder / names['ledger']).read_bytes()\n",
    "from wecommon import NDJSON\nnames: dict = NDJSON\ndef read(folder):\n    return (folder / names['index']).read_bytes()\n"])
def test_fix_review_2_finding_1_a_reader_that_binds_the_constant_to_another_name_fails_the_guard(tmp_path, source):
    shadow = _shadow(tmp_path)
    (shadow / "aliased_reader.py").write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError, match="no entry says"):
        _guard(shadow)


@pytest.mark.parametrize("source", [
    "import wecommon as c\ndef read(root):\n    return c.read_stream(root / c.NDJSON['index'])\n",
    "import wecommon as c\ndef read(root):\n    return [line for line in c.stream_lines((root / c.NDJSON['index']).read_text('utf-8'))]\n"])
def test_review_3_finding_6_the_index_is_read_only_through_archive_rows_and_a_direct_read_fails_the_guard(monkeypatch, tmp_path,
                                                                                                          source):
    """Review 3, finding 6: the export read the index itself, through ``read_stream`` and listed as a read, and failed on a torn
    tail that ``Archive.rows`` skips. The index has one reader, ``Archive.rows`` (its torn-tail rule included), so any other use of
    its name fails the guard, even one listed as a read and passed to ``read_stream``, as the export's was."""
    shadow = _shadow(tmp_path)
    (shadow / "index_reader.py").write_text(source, encoding="utf-8")
    callee = "read_stream" if "read_stream" in source else "stream_lines"
    monkeypatch.setitem(NDJSON_USES, ("index_reader.py", "read", "index"), [(callee, "read")])
    with pytest.raises(AssertionError, match="read only through Archive.rows"):
        _guard(shadow)


@pytest.mark.parametrize(("old", "new", "said"), [
    ('read_stream(folder / NDJSON["stream"])', 'open(folder / NDJSON["stream"], encoding="utf-8").read().splitlines()', "not read_stream"),
    ('lines = read_stream(folder / NDJSON["stream"])', 'lines = read_stream(folder / NDJSON["stream"]) + [NDJSON["stream"]]', "used 2 times")])
def test_a_listed_read_moved_off_the_one_framing_or_made_twice_fails_the_guard(tmp_path, old, new, said):
    shadow = _shadow(tmp_path)
    text = (shadow / "observe.py").read_text("utf-8")
    assert text.count(old) == 1
    (shadow / "observe.py").write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(AssertionError, match=said):
        _guard(shadow)


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
            "PATH": r"D:\home\a@b\bin;D:\tools", "USERPROFILE": r"D:\home\a@b", "SSL_CERT_FILE": r"D:\certs\ca@corp.pem"}
    assert we.host_env(kept) == {**kept, **helpers.FIXED}


def test_every_allowed_variable_programs_read_as_a_url_is_also_read_with_no_scheme():
    assert we.HOST_ENV_URLS <= we.HOST_ENV_NAMES
    assert {n for n in we.HOST_ENV_NAMES if n.endswith("_PROXY") and n != "NO_PROXY"} | {"DOCKER_HOST"} == we.HOST_ENV_URLS


# --- 3b. a URL's user and password are what the URL parser reads (follow-up review 1, findings 1 and 2) -------------------------
def _reads_userinfo(url: str) -> bool:
    parts = urlsplit(url)
    return parts.username is not None or parts.password is not None


@pytest.mark.parametrize(("name", "value", "url"), [
    ("NODE_EXTRA_CA_CERTS", "https://review-user:review password@proxy.invalid/ca.pem", None),  # follow-up review 1, finding 1
    ("HTTPS_PROXY", "http://review-user:review password@proxy.invalid:8080", None),
    ("HTTP_PROXY", "review-user:review password@proxy.invalid:8080", "//review-user:review password@proxy.invalid:8080"),
    ("DOCKER_HOST", "u:p w@host.invalid:2376", "//u:p w@host.invalid:2376"),
    ("PATH", r"D:\tools;https://u:p w@host.invalid/x;D:\bin", "https://u:p w@host.invalid/x"),
    ("NO_PROXY", "localhost, https://u:p w@host.invalid", "https://u:p w@host.invalid")])
def test_follow_up_review_1_finding_1_a_user_or_password_the_url_parser_reads_never_reaches_the_host_session(name, value, url):
    assert _reads_userinfo(url or value)
    assert we.host_env({"TEMP": "t", name: value}) == {"TEMP": "t", **helpers.FIXED}


@pytest.mark.parametrize(("name", "value"), [("HTTPS_PROXY", "http://u@[::1"), ("SSL_CERT_FILE", "https://u:p@[::1/ca.pem")])
def test_a_url_the_parser_cannot_read_is_not_passed(name, value):
    with pytest.raises(ValueError):
        urlsplit(value)
    assert we.host_env({"TEMP": "t", name: value}) == {"TEMP": "t", **helpers.FIXED}


@pytest.mark.parametrize("value", ["npipe:////./pipe/g6@review", "npipe:////./pipe/docker_engine",
                                   "unix:///run/user@1000/docker.sock", "tcp://127.0.0.1:2376"])
def test_follow_up_review_1_finding_2_a_docker_endpoint_with_no_user_or_password_keeps_its_daemon(value):
    assert not _reads_userinfo(value) and not _reads_userinfo("//" + value)
    assert we.host_env({"TEMP": "t", "DOCKER_HOST": value}) == {"TEMP": "t", "DOCKER_HOST": value, **helpers.FIXED}


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


def test_follow_up_review_4_finding_5_a_root_made_a_link_before_the_re_walk_starts_is_refused(monkeypatch, tmp_path):
    ws, other = tmp_path / "ws", tmp_path / "other"
    ws.mkdir()
    other.mkdir()          # both empty: no entry's own check can see the root, only a check of the root itself
    walk, walks = box._tree, []

    def swap_then_walk(root, repo):
        walks.append(root)
        if len(walks) == 2:                       # the walk and its folders' checks are done; the re-walk has read nothing
            shutil.rmtree(ws)
            helpers.link(other, ws)
        return walk(root, repo)

    monkeypatch.setattr(box, "_tree", swap_then_walk)
    sent = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    try:
        got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, lambda *a, **kw: (sent.append(a), helpers.ok(b"{}"))[1])
        assert len(walks) == 2 and (ws.is_junction() or ws.is_symlink())
    finally:
        if ws.is_symlink():
            os.unlink(ws)
        elif ws.is_junction():
            os.rmdir(ws)
    assert got["isError"] is True and sent == [], "a root made a link before the re-walk was mirrored and run"
    assert "a link, a junction or another reparse point" in got["content"][0]["text"]


def _exact(path: Path) -> str:
    """The path as the file system holds it: on Windows, with the prefix that keeps Win32 from trimming a trailing dot or space."""
    return "\\\\?\\" + str(path) if os.name == "nt" else str(path)


def test_follow_up_review_4_finding_6_names_windows_reads_as_one_file_are_refused_before_anything_is_sent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    names = {"name": b"normal", "name.": b"dotted", "name ": b"spaced"}
    try:
        for name, data in names.items():
            with open(_exact(ws / name), "wb") as f:
                f.write(data)
        assert set(os.listdir(ws)) == set(names)
        sent = []
        with pytest.raises(box.Refused, match="Windows would refuse or change"):
            box.mirror(ws, lambda *a, **kw: (sent.append(a), helpers.ok(b"{}"))[1], container="ctr", repo="/w/r", user="tester",
                       timeout=5)
        assert sent == []
    finally:                  # by its exact name: pytest's own clean-up of tmp_path would trim the dot and the space too
        for name in names:
            if os.path.lexists(_exact(ws / name)):
                os.unlink(_exact(ws / name))


@pytest.mark.parametrize("rel", ["name.", "name ", "pkg./a.py", "pkg/a.py ", "con", "nul.txt", "LPT1", "a:b", "a?b", "a\\b", "a\x01"])
def test_a_name_part_the_host_cannot_hold_as_named_is_refused_by_the_one_rule_both_directions_use(rel):
    assert we.HOST_UNSAFE is box.HOST_UNSAFE, "one rule: the export to the host and the pack from it read the same names"
    assert any(box.HOST_UNSAFE.search(part) for part in rel.split("/"))
    with pytest.raises(box.Refused, match="Windows would refuse or change"):
        box._holdable(rel, "/w/r")


@pytest.mark.parametrize("rel", ["a.py", ".hidden", "a.b.c", "pkg/x y.py", "console.py", "com.py", "nul_x", "界.txt"])
def test_a_name_part_the_host_holds_as_named_is_sent(rel):
    box._holdable(rel, "/w/r")


def _call_on(ws: Path) -> tuple[dict, list]:
    sent = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}},
                   lambda *a, **kw: (sent.append(a), helpers.ok(a[-2].encode() + b"\nran\n"))[1])
    return got, sent


def test_fix_review_2_finding_2_a_root_windows_reads_as_another_folder_is_refused_with_no_docker_call(tmp_path):
    ordinary, named = tmp_path / "workspace", tmp_path / "workspace."
    helpers.write(ordinary, {"payload.py": "wrong tree\n"})
    os.mkdir(_exact(named))
    try:
        with open(_exact(named / "payload.py"), "wb") as f:
            f.write(b"named tree\n")
        assert {"workspace", "workspace."} <= set(os.listdir(tmp_path))
        got, sent = _call_on(named)
        assert got["isError"] is True and sent == [], "a root whose name Windows rewrites was packed as another folder"
        assert "Windows would refuse or change" in got["content"][0]["text"]
    finally:
        if os.path.lexists(_exact(named / "payload.py")):
            os.unlink(_exact(named / "payload.py"))
        os.rmdir(_exact(named))


@pytest.mark.parametrize("parts", [("ws.",), ("ws ",), ("con", "ws")])
def test_a_root_with_a_part_the_host_cannot_hold_as_named_is_refused_before_anything_is_read(tmp_path, parts):
    made = [tmp_path.joinpath(*parts[:i + 1]) for i in range(len(parts))]
    for folder in made:
        os.mkdir(_exact(folder))
    try:
        got, sent = _call_on(made[-1])
        assert got["isError"] is True and sent == []
        assert "Windows would refuse or change" in got["content"][0]["text"] and repr(parts[0]) in got["content"][0]["text"]
        with pytest.raises(box.Refused, match="Windows would refuse or change"):
            box.pack(made[-1])
    finally:
        for folder in reversed(made):
            os.rmdir(_exact(folder))


def _export_into(dest: Path) -> list:
    """``run.workspace_from`` into ``dest``, from a fake container whose export holds one file: the docker calls it made."""
    calls = []
    tar = helpers.tar_of([("./payload.py", "file", b"exported bytes")])
    ctx = SimpleNamespace(docker=lambda *a, **kw: (calls.append(a), helpers.ok(tar))[1])
    with pytest.raises(RuntimeError, match="Windows would refuse or change"):
        we.workspace_from(ctx, SimpleNamespace(repo="/w/repo"), "agent", dest)
    return calls


def test_fix_review_3_finding_1_the_export_refuses_the_root_the_pack_refuses_and_writes_nothing(tmp_path):
    ordinary, named = tmp_path / "workspace", tmp_path / "workspace."
    ordinary.mkdir()
    os.mkdir(_exact(named))
    try:
        assert {"workspace", "workspace."} <= set(os.listdir(tmp_path))
        with pytest.raises(box.Refused, match="Windows would refuse or change"):
            box.pack(named)
        assert _export_into(named) == [], "the export ran though its root is one Windows rewrites"
        assert os.listdir(ordinary) == [] and os.listdir(_exact(named)) == [], "the export wrote through a rewritten root"
    finally:
        for folder in (ordinary, named):
            for name in os.listdir(_exact(folder)):
                os.unlink(_exact(folder / name))
        os.rmdir(_exact(named))


@pytest.mark.parametrize("parts", [("ws.",), ("ws ",), ("con", "ws")])
def test_the_export_refuses_a_root_with_a_part_the_host_cannot_hold_as_named_and_writes_nothing(tmp_path, parts):
    made = [tmp_path.joinpath(*parts[:i + 1]) for i in range(len(parts))]
    for folder in made:
        os.mkdir(_exact(folder))
    try:
        assert _export_into(made[-1]) == []
        assert os.listdir(_exact(made[-1])) == [] and sorted(os.listdir(tmp_path)) == sorted({parts[0]})
    finally:
        for folder in reversed(made):
            for name in os.listdir(_exact(folder)):
                if not os.path.isdir(_exact(folder / name)):
                    os.unlink(_exact(folder / name))
            os.rmdir(_exact(folder))


def test_a_root_with_ordinary_parts_a_leading_dot_and_a_space_inside_packs_as_before(tmp_path):
    ws = tmp_path / ".box" / "a b" / "ws"
    helpers.write(ws, {"a.py": "x\n"})
    got, sent = _call_on(ws)
    assert got["isError"] is False and len(sent) == 2
    assert tarfile.open(fileobj=io.BytesIO(box.pack(ws))).getnames() == ["a.py"]


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
