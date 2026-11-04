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
import re
import shutil
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
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


#: What the inventory lists (``line_splits``): every use named in ``NAMES``, everything reached through what an import binds to
#: ``re``, and a for over an open file or a standard stream.
STREAMS = {"stdin", "stdout", "stderr"}
#: The one framing of newline-delimited JSON (``wecommon.stream_lines``, and ``read_stream`` for a file): Claude Code's stream
#: and a host session's stderr, ``runs.jsonl``, the ledger and the egress log are all split by it, at "\n" alone.
FRAMING = ("wecommon.py", "stream_lines", "text.split('\\n')")
PIPE = "a pipe of bytes: readline and iteration split at b'\\n' alone, as stream_lines splits text"
ASCII_JSON = ("the last line a script of the worked examples printed with json.dumps and its default ensure_ascii, so no line "
              "separator can be inside it")
#: Reasons the inventory gives more than once (follow-up review 4).
RE_M = "found with re.M, whose ^ and $ hold at '\\n' alone, where the framing splits"
PARTS = "a path, cut into its parts at '/'"
TEST_ID = "a pytest id, cut at its '::'"
TEST_NAME = "a test's name, cut before its parameters"
OWNER = "a project's 'owner/name', cut once at '/'"
WORDS = "the words of a text, found"
PATCH_NAME = "a patch's --- or +++ line, its name before the tab git writes after a name that holds a space"
SAVED_NOTE = "Claude Code's note that a tool's output was saved, searched in a tool result or in a line the framing gave"
ARM = "an arm's name, matched whole"
OWN_LINE = "the output of G6's own script in the container: one 'name=number' line, " + RE_M
CRITIC = "Codex's own report: one field, by its heading, " + RE_M
LABELLED = "a failed case's 'label:id', cut once at ':' for its id"
#: Everything else the inventory lists in the worked examples, by (module, the function it is in, its source), with why it reads
#: no newline-delimited JSON, or reads it as the framing does. A person names each; the scan judges none.
NOT_A_STREAM = {
    ("box_mcp.py", "serve", "for ... in stdin"): "the box's JSON-RPC requests on standard input: " + PIPE,
    ("box_mcp.py", "main", "__doc__.splitlines()"): "the module's own docstring",
    ("export_reviewer_cases.py", "context", "text.splitlines()"): "a source file of the bug's repository, shown by its lines",
    ("final_review.py", "reproduction", "mask_paths(scrub(now.texts.get(case, ''))).splitlines()"):
        "a failing test's report, as pytest wrote it",
    ("headroom.py", "main", "__doc__.splitlines()"): "the module's own docstring",
    ("layers.py", "_tokens", "io.StringIO(body).readline"): "one line of Python source, given to Python's tokenizer",
    ("layers.py", "change", "text.split('\\n')"): "a patch, read as git apply reads it",
    ("library.py", "chunks", "re.split('\\\\n\\\\s*\\\\n', path.read_text('utf-8', errors='replace'))"):
        "Markdown, split into paragraphs",
    ("probe_replay.py", "summary", "output.strip().splitlines()"): "pytest's own output",
    ("product_review.py", "touched", "patch.split('\\n')"): "a patch, read as git apply reads it",
    ("round2_g6.py", "patch", "old.splitlines()"): "an edit's anchor, Python source, named in an error",
    ("run.py", "crashes", "output.splitlines()"): "pytest's own output",
    ("run.py", "critic_report", "said[said.rfind('[codex]'):].split('\\n', 1)"): "Codex's own report, its first line dropped",
    ("run.py", "test_changes", "name_status.splitlines()"): "git's --name-status output",
    ("run.py", "key_check", "out(done).splitlines()"): ASCII_JSON,
    ("run.py", "hook_selftest", "out(ctx.docker.sh(agent, script, workdir=task.repo)).splitlines()"):
        "the hook self-test's echo of numbers",
    ("run.py", "run_session", "proc.stdout.readline"): "a container session's stream: " + PIPE,
    ("run.py", "host_session.lines", "proc.stdout.readline"): "a host session's stream: " + PIPE,
    ("run.py", "_run_task", "out(ctx.docker.sh(agent, TREE_STATE, workdir=task.repo)).splitlines()"):
        "TREE_STATE's printf of git's hashes and counts",
    ("run.py", "_run_task", "out(ctx.docker.sh(agent, ARM_STATE)).splitlines()"): ASCII_JSON,
    ("run.py", "_run_task", "proc.stdout.readline"): "a container run's stream: " + PIPE,
    ("run.py", "g6replay", "out(done).splitlines()"): ASCII_JSON,
    ("run.py", "modelcheck", "out(done).splitlines()"): ASCII_JSON,
    ("run.py", "main", "__doc__.splitlines()"): "the module's own docstring",
    ("selection.py", "main", "__doc__.splitlines()"): "the module's own docstring",
    # Seen once a line break is what Python reads as one (follow-up review 2, finding 2); none reads a record stream.
    ("box_mcp.py", "run_command", "said.partition(start + '\\n')"):
        "a command's combined output, cut once at the start line the box's shell printed with this call's own nonce",
    ("evolve.py", "main", "os.environ.get('G6_LIBRARY', '').split(os.pathsep)"): "a list of folders, as PATH holds one",
    ("export_reviewer_cases.py", "_image",
     "subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}', 'g6we-task'], capture_output=True, text=True, "
     "timeout=60).stdout.split()"): "docker's image names, which hold no whitespace",
    ("final_review.py", "_step", "proof.split()"): "G6's own proof text, 'review' and a hex id, cut at its one space",
    ("final_review.py", "review", "proof.split()"): "G6's own proof text, 'review' and a hex id, cut at its one space",
    ("run.py", "run_critic", "proof.split()"): "G6's own proof text, 'review' and a hex id, cut at its one space",
    ("run.py", "archived_resolver.resolve", "path.split(marker, 1)"): "a path in the container, cut once at /.claude/projects/",
    ("run.py", "read_probes", "said.partition(JUNIT_MARK)"):
        "the probe script's output, cut once at G6's own mark, before pytest's JUnit XML",
    # Listed once the inventory lists every use by its name and of what re binds (follow-up review 4); none reads a record stream.
    ("box_mcp.py", "<module>", 're.compile(\'[<>:"|?*\\\\\\\\\\\\x00-\\\\x1f]|[. ]$|^(?:con|prn|aux|nul|com\\\\d|lpt\\\\d)(?:\\\\..*)?$\', re.I)'):
        "HOST_UNSAFE, searched in one part of a name",
    ("box_mcp.py", "_holdable", "rel.split('/')"): PARTS,
    ("compare.py", "end_of", "case.split('::')[0].split('.')"): "a test file's name, cut at its dot",
    ("compare.py", "end_of", "case.split('::')"): TEST_ID,
    ("distill.py", "named", "re.search(f'\\\\b{word}\\\\b', text, re.I)"): "a project's name, searched for in a distilled text",
    ("distill.py", "named", "re.findall('#?\\\\b\\\\d{4,5}\\\\b', text)"): "issue numbers, found in a distilled text",
    ("distill.py", "named", "re.findall('(?<![A-Za-z])[A-Za-z]:\\\\\\\\|/(?:home|Users)/', text)"):
        "the start of a local path, found in a distilled text",
    ("distill.py", "named", "test.rpartition('::')[2].partition('[')"): TEST_NAME,
    ("distill.py", "named", "test.rpartition('::')"): TEST_ID,
    ("distill.py", "distill", "re.findall('^diff --git a/(\\\\S+)', '\\n'.join((f.output for f in found)), re.M)"):
        "the file names in diffs' header lines: " + RE_M,
    ("evolve.py", "near_miss", "path.stem.split('_', 3)"):
        "a saved check's file name, G6's own 'repair_check_<n>_<label>', cut at '_'",
    ("evolve.py", "similar", "re.findall('\\\\w+', text.lower())"): WORDS,
    ("evolve.py", "projects", "t.project.split('/', 1)"): OWNER,
    ("export_reviewer_cases.py", "<module>", "re.compile('^@@ -(\\\\d+)(?:,(\\\\d+))? \\\\+\\\\d+(?:,\\\\d+)? @@', re.M)"):
        "a diff's hunk header: " + RE_M,
    ("export_reviewer_cases.py", "is_source", "path.rsplit('/', 1)"): "a path, its last part",
    ("export_reviewer_cases.py", "changed", "re.split('^diff --git ', diff, flags=re.M)"):
        "a diff, cut into its files at each header line: " + RE_M,
    ("export_reviewer_cases.py", "changed", "re.search('^\\\\+\\\\+\\\\+ b/(.+)$', section, re.M)"):
        "a diff section's new name, from its +++ line: " + RE_M,
    ("layers.py", "<module>", "re.compile('(^|/)(tests?|testing)/|(^|/)test_[^/]*\\\\.py$|_test\\\\.py$|(^|/)conftest\\\\.py$')"):
        "a path, searched for a test folder or a test's file name",
    ("layers.py", "change", "line[4:].split('\\t', 1)"): PATCH_NAME,
    ("library.py", "words", 're.findall("[a-z0-9\']+", text.lower())'): WORDS,
    ("observe.py", "<module>", "re.compile('Output has been saved to (/\\\\S+?\\\\.txt)')"): SAVED_NOTE,
    ("observe.py", "<module>", "re.compile('template_instances\\\\.\\\\d+.*bindings')"): "a tool error's text, searched",
    ("observe.py", "_norm", "re.sub('\\\\s+', ' ', re.sub('(?m)^[ \\\\t]*>[ \\\\t]?', '', text))"):
        "a report's text, its whitespace collapsed for comparison",
    ("observe.py", "_norm", "re.sub('(?m)^[ \\\\t]*>[ \\\\t]?', '', text)"):
        "a report's text, a Markdown quote's markers taken off: " + RE_M,
    ("observe.py", "observe_run", "path.split('/.claude/projects/', 1)"):
        "a path in the container, cut once at /.claude/projects/",
    ("probe_replay.py", "<module>", "re.compile('(\\\\d+) (passed|failed|error|errors|skipped|deselected)\\\\b')"):
        "pytest's summary counts, searched in one line",
    ("product_review.py", "_applied", "name.split('/', 1)"):
        "a header's path, its first part taken off as git apply -p1 takes it",
    ("product_review.py", "touched", "line[4:].split('\\t', 1)"): PATCH_NAME,
    ("product_review.py", "touched", "line.split(' ', 2)"): "a patch's rename or copy line: its verb, its side and its name",
    ("run.py", "<module>", "re.compile('^(?:FAILED|ERROR) \\\\S*?([^/\\\\s]+\\\\.py(?:::\\\\S+)?)', re.M)"):
        "pytest's FAILED and ERROR summary lines: " + RE_M,
    ("run.py", "Task.name", "self.full_name.split('/', 1)"): "a bug's full name, cut once at '/'",
    ("run.py", "trained", "re.fullmatch('K\\\\d+', arm)"): ARM,
    ("run.py", "trained", "re.fullmatch('[LM]\\\\d+', arm)"): ARM,
    ("run.py", "probe_failures", "re.search('^exit=(\\\\d+)$', said, re.M)"): OWN_LINE,
    ("run.py", "crashes", "re.search(' - (?:assert|AssertionError|Failed)', line)"):
        "one summary line of pytest's output, searched",
    ("run.py", "random_cases", "re.search('^random=(\\\\d+)$', said, re.M)"): OWN_LINE,
    ("run.py", "gate_numbers", "re.search('^exit=(\\\\d+)$', said, re.M)"): OWN_LINE,
    ("run.py", "gate_numbers", "re.search('^cross=(\\\\d+)$', said, re.M)"): OWN_LINE,
    ("run.py", "read_probes", "re.search('^exit=(\\\\d+)$', head, re.M)"): OWN_LINE,
    ("run.py", "critic_report", "re.search(f'^READ:\\\\s*{re.escape(proof)}\\\\s*$', body, re.M)"): CRITIC,
    ("run.py", "critic_report", 're.escape(proof)'): "G6's own 'review <hex id>' text, escaped to be searched for",
    ("run.py", "critic_report", "re.search('^VERDICT:\\\\s*(\\\\w+)', body, re.M)"): CRITIC,
    ("run.py", "critic_report", "re.search('^RULE:\\\\s*(.*?)(?=^VERDICT:)', body, re.M | re.S)"): CRITIC,
    ("run.py", "critic_report", "re.search('^WHY:\\\\s*(.*?)(?=^```|\\\\Z)', body, re.M | re.S)"): CRITIC,
    ("run.py", "critic_report", "re.search('^```python\\\\n(.*?)^```', body, re.M | re.S)"): CRITIC,
    ("run.py", "gets_thread", "re.fullmatch('T|R|K(?!0$)\\\\d+|V\\\\d+|X\\\\d+|[LM]\\\\d+', arm)"): ARM,
    ("run.py", "prompt", "re.fullmatch('[KV]\\\\d+', arm)"): ARM,
    ("run.py", "prompt", "re.fullmatch('[GH]\\\\d+', arm)"): ARM,
    ("run.py", "prompt", "re.fullmatch('[LM]\\\\d+', arm)"): ARM,
    ("run.py", "prompt", "task.project.split('/', 1)"): OWNER,
    ("run.py", "r3_loop_key", "re.fullmatch('[KMV]\\\\d+', arm)"): ARM,
    ("run.py", "r3_loop_key", "re.fullmatch('[KV]\\\\d+', arm)"): ARM,
    ("run.py", "r3_loop_key", "re.fullmatch('[GHLM]\\\\d+', arm)"): ARM,
    ("run.py", "test_changes", "line.partition('\\t')"): "a line of git's --name-status output, cut once at its tab",
    ("run.py", "<module>", "re.compile('Output has been saved to (/\\\\S+?\\\\.txt)')"): SAVED_NOTE,
    ("run.py", "<module>", "re.compile('TOKEN|KEY|SECRET|PASS|AUTH|CREDENTIAL', re.I)"):
        "an environment variable's name, searched",
    ("run.py", "<module>", 're.compile("You(?:\'|\u2019)ve hit your (?:[\\\\w-]+ ){0,3}limit\\\\b\\\\W{0,4}(resets\\\\b[^\\\\n|]*)?", re.I)'):
        "Claude Code's words that a usage limit is reached, searched in a session's text",
    ("run.py", "host_value_holds_userinfo", "re.finditer('://', value)"): "an environment value, each '://' in it found",
    ("run.py", "host_facts", "re.compile(re.escape(model) + '(?:-\\\\d{8})?')"):
        "the model asked for, matched whole against each model the transcript names",
    ("run.py", "host_facts", 're.escape(model)'): "the model's name, escaped to be matched",
    ("run.py", "run_task", "re.fullmatch('[A-ERST]|[GHKLMVX]\\\\d+', str(ctx.arm))"): ARM,
    ("run.py", "host_refusal", "re.fullmatch('K\\\\d+', ctx.arm)"): ARM,
    ("run.py", "probe_gate.cases", "k.rsplit('::', 1)[-1].split('[')"): TEST_NAME,
    ("run.py", "probe_gate.cases", "k.rsplit('::', 1)"): TEST_ID,
    ("run.py", "probe_gate", "re.match('assert|AssertionError|Failed', got.texts.get(k, ''))"):
        "a failing test's report, matched at its start",
    ("run.py", "run_scout", "task.project.split('/', 1)"): OWNER,
    ("run.py", "reviews_until_clean", "case.split(':', 1)"): LABELLED,
    ("run.py", "reviews_with_bug_test", "case.split(':', 1)"): LABELLED,
    ("run.py", "_run_task", "re.fullmatch('[LM]\\\\d+', ctx.arm)"): ARM,
    ("run.py", "_run_task", "re.fullmatch('K\\\\d+', ctx.arm)"): ARM,
    ("run.py", "_oracle_run", "out(ran).rsplit('exit=', 1)"): "the pytest script's output, cut once at its last 'exit='",
    ("run.py", "check", "p.stem.split('-')"): "a saved round's file name, G6's own 'round-<n>', cut at '-'",
    ("selection.py", "is_test", "path.replace('\\\\', '/').split('/')"): PARTS,
    ("wecommon.py", "Redactor", "re.compile('sk-ant-[A-Za-z0-9_\\\\-]{8,}')"):
        "an API key's form, searched and replaced in text bound for a file or a terminal",
}


#: What the inventory lists by its name alone, called or not, with any arguments (follow-up review 4: a scan that judged whether a
#: call can cut a line missed a form in reviews 2, 3 and 4, a back reference, a flag, ** arguments and a star import, so it
#: judges nothing now; a person names each entry).
NAMES = frozenset({"split", "rsplit", "partition", "rpartition", "splitlines", "readline", "readlines"})


def _module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _bound_to_re(tree: ast.AST) -> dict[str, object]:
    """Each name a module's imports bind to ``re``, to a module inside it, or to anything either holds, with what Python binds to
    it: ``import re`` (``re`` is taken as bound in any case), ``import re as rx``, ``import re._parser as p``,
    ``from re import split as cut``, and ``from re import *``, read as Python reads a star import (the module's ``__all__``, else
    its names with no leading underscore). A relative import is the module's own and binds nothing of ``re``; a name that
    cannot be imported is bound to None, which is read as anything."""
    bound: dict[str, object] = {"re": re}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re" or alias.name.startswith("re."):
                    bound[alias.asname or "re"] = _module(alias.name) if alias.asname else re
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module \
                and (node.module == "re" or node.module.startswith("re.")):
            module = _module(node.module)
            for alias in node.names:
                if alias.name == "*" and module is not None:
                    names = getattr(module, "__all__", None) or [n for n in vars(module) if not n.startswith("_")]
                    bound.update({n: getattr(module, n, None) for n in names})
                elif alias.name != "*":
                    held = getattr(module, alias.name, None)
                    bound[alias.asname or alias.name] = held if held is not None else _module(f"{node.module}.{alias.name}")
    return bound


def line_splits(source: str) -> list[tuple[str, str]]:
    """Every site in a module's source the inventory lists, as (the function it is in, its source), by nothing but a name and
    what an import binds:
    - every use of an attribute named in ``NAMES``, called or not, and every call of a bare name in ``NAMES``;
    - every call of anything an import binds to ``re`` (``_bound_to_re``), and every other use of it that can be called (a
      function, a class, a module), so a name it is given, to be called through elsewhere, is listed where it is given;
    - a for over an open file or a standard stream (``STREAMS``).
    A call is listed by its whole source, its arguments included, so a call whose arguments change is a new one. Its limits,
    stated: a name reached through getattr on anything but ``re``, a module imported at run time (importlib, ``__import__``),
    code run by exec or eval, and a compiled pattern's methods not in ``NAMES`` (its compile is listed where it is made)."""
    found: list[tuple[str, str]] = []
    tree = ast.parse(source)
    bound = _bound_to_re(tree)
    calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
    attribute_of = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    listed: set[int] = set()

    def callable_or_unknown(held: object) -> bool:
        return held is None or callable(held) or isinstance(held, ModuleType)

    def lists(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr in NAMES:
            return True
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in NAMES and id(node) in calls:
                return True
            if node.id in bound:
                held = bound[node.id]
                if isinstance(held, ModuleType) and id(node) in attribute_of:
                    return False                  # read where its attribute is (below)
                return id(node) in calls or callable_or_unknown(held)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and isinstance(bound.get(node.value.id), ModuleType):
            return id(node) in calls or callable_or_unknown(getattr(bound[node.value.id], node.attr, None))
        return False

    def visit(node: ast.AST, where: str, opened: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            where, opened = (node.name if where == "<module>" else f"{where}.{node.name}"), set()
        if isinstance(node, (ast.With, ast.AsyncWith)):
            opened |= {item.optional_vars.id for item in node.items if isinstance(item.optional_vars, ast.Name)
                       and isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Name)
                       and item.context_expr.func.id == "open"}
        if lists(node):
            site = calls.get(id(node), node)
            if id(site) not in listed:
                listed.add(id(site))
                found.append((where, ast.unparse(site)))
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            it = node.iter
            last = it.attr if isinstance(it, ast.Attribute) else it.id if isinstance(it, ast.Name) else None
            if (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "open") or last in STREAMS \
                    or (isinstance(it, ast.Name) and it.id in opened):
                found.append((where, "for ... in " + ast.unparse(it)))
        for child in ast.iter_child_nodes(node):
            visit(child, where, opened)

    visit(tree, "<module>", set())
    return found

def test_the_scan_sees_each_kind_of_line_split():
    source = ("def f(t, p, s):\n    t.splitlines()\n    p.readlines()\n    t.split('\\n')\n    t.rsplit(b'\\r\\n')\n"
              "    import re\n    re.split(r'\\n+', t)\n    [x for x in open(p)]\n    for x in s.stdin:\n        pass\n"
              "    with open(p) as h:\n        for x in h:\n            pass\n    t.split(',')\n")
    assert [src for _, src in line_splits(source)] == [
        "t.splitlines()", "p.readlines()", "t.split('\\n')", "t.rsplit(b'\\r\\n')", "re.split('\\\\n+', t)", "for ... in open(p)",
        "for ... in s.stdin", "for ... in h", "t.split(',')"]


# Pins changed on purpose (follow-up review 4): the cases below once pinned a split the scan judged unable to cut a line
# ("t.split(',')", a lookahead, a module's own split) as not found. The scan judges nothing now, so every one is listed.
@pytest.mark.parametrize("call", [
    "t.split('\\r')", "t.split('\\x0b')", "t.split('\\u2028')", "t.split('\\x85')", "t.partition('\\n')", "t.rpartition(b'\\r')",
    "t.split()", "t.rsplit(None, 1)", "t.split(sep=None)", "t.split(sep)", "re.split('\\\\s+', t)", "re.split('\\\\n\\\\s*\\\\n', t)",
    "re.split(PATTERN, t)", "t.split(',')", "t.split('\\\\n')", "t.split(b'\\x0b')", "re.split(',', t)", "t.partition('=')"])
def test_follow_up_review_2_finding_2_a_split_at_any_line_break_python_reads_is_seen(call):
    """A split at a line break Python reads is listed, and so is every other split: the inventory lists a call by its name and
    judges no separator (follow-up review 4)."""
    assert [src for _, src in line_splits(f"def f(t, sep):\n    {call}\n")] == [ast.unparse(ast.parse(call))]


@pytest.mark.parametrize("source", [
    "def f(t):\n    import re\n    re.split('\\\\r(?=\\\\{)', t)\n",           # follow-up review 3, finding 1
    "def f(t):\n    import re\n    re.compile('\\\\r(?=\\\\{)').split(t)\n",
    "def f(t):\n    import re\n    re.split('[^x]', t)\n",
    "def f(t):\n    import re\n    re.split('(?=\\\\r),', t)\n",
    "def f(t):\n    import re\n    re.split('[,;]', t)\n",
    "from re import split\ndef f(t):\n    split('\\\\r', t)\n",                  # follow-up review 3, finding 3
    "from re import split as cut\ndef f(t):\n    cut('\\\\r', t)\n",
    "import re as rx\ndef f(t):\n    rx.split('\\\\r', t)\n",
    "def split(a, b):\n    return a\ndef f(t):\n    split('\\\\r', t)\n"])          # a function of the module's own, by its name
def test_follow_up_review_3_a_regex_split_is_read_by_re_s_own_parser_and_by_any_name_bound_to_it(source):
    """A split reached through ``re`` under any name an import binds is listed, whatever its pattern, and so is any call named
    ``split`` (follow-up review 4: the scan no longer reads the pattern)."""
    assert line_splits(source)


#: Each form a scan that judged a call let pass (follow-up review 4, findings 1 to 4), and others of the same kinds, as (the
#: imports, the line, what is listed when it is not the line itself). The inventory lists by name and by what an import binds.
LISTED = [
    ("import re", r"re.split(r'(?=(\r))\1', text)", None),                   # finding 1: a back reference to a captured break
    ("import re", r"re.split(r'(?a)\S', text)", None),                       # finding 2: a category under an inline flag
    ("import re", r"re.split(**{'pattern': r'\r', 'string': text})", None),  # finding 3: the pattern in ** keyword arguments
    ("from re import *", r"split(r'\r', text)", None),                       # finding 4: a star import
    ("from re import *", r"findall(r'[^\r]+', text)", None),
    ("from re import compile as c", "c('x').finditer(text)", "c('x')"),
    ("import re as rx", r"rx.sub(r'\r', '', text)", None),
    ("import re._parser as p", "p.parse('x')", None),
    ("from re import _parser", "_parser.parse('x')", None),
    ("import re", "re.search('x', text)", None),
    ("import re", "cut = re.split", "re.split"),                             # a reference, to be called through another name
    ("from re import split as cut", "f = cut", "cut"),
    ("import re", "getattr(re, 'split')(',', text)", "re"),                  # re itself, reached through getattr
    ("", "text.split(',')", None),                                           # with any arguments
    ("", "text.partition('=')", None),
    ("", "split(text)", None),                                               # a bare name, whatever binds it
]


@pytest.mark.parametrize(("imports", "line", "listed"), LISTED)
def test_follow_up_review_4_findings_1_to_4_every_use_by_name_and_of_what_re_binds_is_listed(imports, line, listed):
    assert [src for _, src in line_splits(f"{imports}\ndef f(text):\n    {line}\n")] == [ast.unparse(ast.parse(listed or line))]


@pytest.mark.parametrize("line", ["text.strip()", "re.M", "x = 're.split'", "[a for a in text]"])
def test_what_is_not_named_and_not_reached_through_re_is_not_listed(line):
    assert line_splits(f"import re\ndef f(text):\n    {line}\n") == []


def test_follow_up_review_3_finding_2_a_reader_in_a_subfolder_fails_the_guard(tmp_path, monkeypatch):
    shadow = tmp_path / "worked_examples"
    shadow.mkdir()
    for source in WE_DIR.glob("*.py"):
        (shadow / source.name).write_bytes(source.read_bytes())
    (shadow / "readers").mkdir()
    (shadow / "readers" / "transcript.py").write_text("def read(text):\n    return text.splitlines()\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WE_DIR", shadow)
    with pytest.raises(AssertionError, match="line split"):
        test_every_line_split_in_the_worked_examples_is_the_one_framing_or_named_with_why_it_reads_no_stream()


def test_follow_up_review_2_finding_2_a_new_split_at_a_lone_carriage_return_fails_the_guard(tmp_path, monkeypatch):
    shadow = tmp_path / "worked_examples"
    shadow.mkdir()
    for source in WE_DIR.glob("*.py"):
        (shadow / source.name).write_bytes(source.read_bytes())
    (shadow / "new_reader.py").write_text("def read_records(text):\n    return text.split('\\r')\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WE_DIR", shadow)
    with pytest.raises(AssertionError, match="line split"):
        test_every_line_split_in_the_worked_examples_is_the_one_framing_or_named_with_why_it_reads_no_stream()


@pytest.mark.parametrize(("edit", "said"), [
    ('    for part in rel.split("\\r"):\n', "not the one framing"),                         # a named call, its argument changed
    ('    rel.split("/")\n    for part in rel.split("/"):\n', "more than once")])             # a named call, made twice
def test_follow_up_review_4_a_named_call_changed_or_made_again_fails_the_guard(tmp_path, monkeypatch, edit, said):
    shadow = tmp_path / "worked_examples"
    shadow.mkdir()
    for source in WE_DIR.glob("*.py"):
        (shadow / source.name).write_bytes(source.read_bytes())
    named = '    for part in rel.split("/"):\n'
    text = (shadow / "box_mcp.py").read_text("utf-8")
    assert text.count(named) == 1
    (shadow / "box_mcp.py").write_text(text.replace(named, edit), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WE_DIR", shadow)
    with pytest.raises(AssertionError, match=f"line split .*{said}"):
        test_every_line_split_in_the_worked_examples_is_the_one_framing_or_named_with_why_it_reads_no_stream()


def test_every_line_split_in_the_worked_examples_is_the_one_framing_or_named_with_why_it_reads_no_stream():
    found = Counter((path.relative_to(WE_DIR).as_posix(), where, src) for path in sorted(WE_DIR.rglob("*.py"))
                    for where, src in line_splits(path.read_text("utf-8")))
    assert found[FRAMING] == 1, "the one framing, wecommon.stream_lines, splits at '\\n' alone"
    other = set(found) - {FRAMING}
    assert not other - set(NOT_A_STREAM), f"a line split that is not the one framing: {sorted(other - set(NOT_A_STREAM))}"
    assert not set(NOT_A_STREAM) - other, f"named but no longer there: {sorted(set(NOT_A_STREAM) - other)}"
    again = {key: n for key, n in found.items() if n != 1}
    assert not again, f"a line split listed more than once in its function, so a new one would pass as the old: {again}"
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
