# SPDX-License-Identifier: Apache-2.0
"""Replay 6's follow-up (PLAN.md, 2026-11-04, night): review 4's open findings, each removed at its root. The probe shares with
what judges it nothing it can change: it runs in a namespace of its own, an observation is JSON by its types alone and is read by
the model's own rule, a probe that overruns is ended with every process it started, each run has a container of its own, and the
probe imports on pytest's own path. Reader 2 is shown no literal that tells it reader 1's expectations, and a saved model that
another compiler compiled is compiled again with no reader asked. Fakes stand in for the container, Codex and Claude; the
compiled files run in real local pytest processes, as review 4's tests run them."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import final_review as fr  # noqa: E402
import replay3 as r3  # noqa: E402
import replay4 as r4  # noqa: E402
import replay5 as r5  # noqa: E402
import replay6 as r6  # noqa: E402
import replay_compare as rc  # noqa: E402
import run as we  # noqa: E402

QUOTE = "The result must be ok."
ROW = {"when": {}, "expect": {"result": "ok"}, "quote": QUOTE}


class Caller:
    """Reader 2's fake: these rows as its reply, and every call kept."""

    def __init__(self, rows=None):
        self.rows = [ROW] if rows is None else rows
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return json.dumps({"rules": self.rows})


def answer(probe, *, rows=None, factors=None, observations=None):
    """Reader 1's answer as ``final_review._step`` reads it, the model where replay 3's reply carries its test."""
    model = {"factors": factors or {"x": [0]}, "observations": observations or ["result"], "probe": probe,
             "rules": [ROW] if rows is None else rows}
    return {"verdict": "defect", "rule": "r", "why": "w", "error": "", "test": json.dumps(model)}


def formal(probe, *, rows=None, caller=None, **model):
    return r6.formal(answer(probe, rows=rows, **model), "Issue", QUOTE, caller or Caller(rows))


def run_compiled(folder, source, *, config="[pytest]\n", extra_files=None):
    """The compiled file run by real pytest in a process of its own, in ``folder``, as ``run_on`` runs it in a container."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "test_formal.py").write_text(source, encoding="utf-8")
    (folder / "pytest.ini").write_text(config, encoding="utf-8")
    for name, text in (extra_files or {}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    report = folder / "report.xml"
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-m", "pytest", "test_formal.py", "-q", "-p", "no:cacheprovider",
                           "-c", "pytest.ini", "--rootdir", str(folder), f"--junitxml={report}"],
                          cwd=folder, env=env, capture_output=True, timeout=120)
    xml = report.read_text(encoding="utf-8") if report.is_file() else ""
    return we.ProbeRun(done.returncode, we.parse_junit(xml) if xml else {}, {}, done.stdout.decode("utf-8", "replace"))


def _load(src: str) -> dict:
    space: dict = {"__name__": "test_formal"}
    exec(compile(src, "test_formal.py", "exec"), space)       # G6's compiled file with this file's fake probes, never a model's
    return space


def _outcomes(ran: we.ProbeRun) -> dict[str, str]:
    """Each case's outcome by the case's label in its id."""
    return {case.split("-", 2)[2].rstrip("]"): outcome for case, outcome in ran.results.items()}


# --- 1. one rule for what an observation is ------------------------------------------------------------------------------------
def test_an_observation_that_is_no_unicode_text_is_unobservable_and_flags_nothing(tmp_path):
    """Review 4 finding 1: the probe's process and the file read a lone surrogate as JSON, which the model's reader refuses, so
    its cell failed on both trees and flagged the patch."""
    got = formal("def observe(case):\n    return {'result': chr(0xD800)}\n")
    assert got["status"] == "stands"
    parent, patch = run_compiled(tmp_path / "parent", got["test"]), run_compiled(tmp_path / "patch", got["test"])
    assert list(parent.results.values()) == list(patch.results.values()) == ["skipped"], (parent.tail, patch.tail)
    assert r3.verdict(got, parent, patch, same_case=True)["said"] == "none"


@pytest.mark.parametrize("probe", [
    "bool = lambda value: True\ndef observe(case):\n    return {'result': (1, 2)}\n",
    "class ResultTuple(tuple):\n    def __eq__(self, other):\n        return True\n"
    "def observe(case):\n    return {'result': ResultTuple([1, 2])}\n",
], ids=["shadow_bool", "tuple_equality"])
def test_a_probe_cannot_pass_off_a_value_json_would_change(tmp_path, probe):
    """Review 4 finding 7: the check ran in the probe's own namespace and trusted the value's own equality, so a tuple was read
    as a list."""
    got = formal(probe, rows=[{"when": {}, "expect": {"result": [9, 9]}, "quote": QUOTE}])
    parent, patch = run_compiled(tmp_path / "parent", got["test"]), run_compiled(tmp_path / "patch", got["test"])
    assert list(parent.results.values()) == list(patch.results.values()) == ["skipped"], (parent.tail, patch.tail)
    assert r3.verdict(got, parent, patch, same_case=True)["said"] == "none"


@pytest.mark.parametrize("given", ["{'result': S('ok')}", "{'result': I(1)}", "{'result': F(0.5)}", "{'result': L(['ok'])}",
                                   "{'result': D(a='ok')}", "D(result='ok')", "{'result': [S('ok')]}"])
def test_a_subclass_of_a_json_type_is_no_json_value_whatever_it_compares_equal_to(tmp_path, given):
    probe = ("class S(str): pass\nclass I(int): pass\nclass F(float): pass\nclass L(list): pass\nclass D(dict): pass\n"
             f"def observe(case):\n    return {given}\n")
    ran = run_compiled(tmp_path, formal(probe)["test"])
    assert list(ran.results.values()) == ["skipped"], ran.tail


def test_a_key_that_is_no_unicode_text_makes_its_own_case_unobservable_and_no_other(tmp_path):
    rows = [{"when": {}, "expect": {"result": "ok"}, "quote": QUOTE}]
    probe = "def observe(case):\n    return {'result': {chr(0xD800): 1} if case['x'] == 0 else 'ok'}\n"
    got = formal(probe, rows=rows, factors={"x": [0, 1]})
    assert _outcomes(run_compiled(tmp_path, got["test"])) == {"x=0": "skipped", "x=1e0": "passed"}


def test_a_json_observation_still_compares_by_the_tables_one_rule(tmp_path):
    rows = [{"when": {"x": 0}, "expect": {"result": {"a": [1, 2.5, True, None, "é"]}}, "quote": QUOTE},
            {"when": {"x": 1}, "expect": {"result": 2}, "quote": QUOTE},
            {"when": {"x": 2}, "expect": {"result": "ok"}, "quote": QUOTE}]
    probe = "def observe(case):\n    return {'result': [{'a': [1, 2.5, True, None, 'é']}, 2.0, 'bad'][case['x']]}\n"
    got = formal(probe, rows=rows, factors={"x": [0, 1, 2]})
    assert _outcomes(run_compiled(tmp_path, got["test"])) == {"x=0": "passed", "x=1e0": "passed", "x=2e0": "failed"}


def test_the_probe_runs_in_a_namespace_of_its_own_that_is_the_main_module(tmp_path):
    """What running the probe in ``__main__``'s namespace gave is kept: its name is ``__main__``, and what looks a class's module
    up by name finds the probe's names there. Nothing of G6's program is in it."""
    probe = ("import sys, typing\n"
             "class Point:\n    x: 'Point'\n"
             "def observe(case):\n"
             "    hints = typing.get_type_hints(Point)\n"
             "    return {'result': [__name__ == '__main__', sys.modules['__main__'].__dict__ is globals(),"
             " hints['x'] is Point, '_g6_child' in globals()]}\n")
    got = formal(probe, rows=[{"when": {}, "expect": {"result": [True, True, True, False]}, "quote": QUOTE}])
    ran = run_compiled(tmp_path, got["test"])
    assert list(ran.results.values()) == ["passed"], ran.tail


@pytest.mark.parametrize("said", ['{"seen": [{"seen": {"result": "\\ud800"}}]}',
                                  '{"seen": [{"seen": {"result": "ok", "result": "bad"}}]}'])
def test_the_file_reads_the_probes_answer_by_the_models_own_rule(said):
    """The probe's answer is read by ``_loads``, the rule the model's data is read by, so what the model's reader refuses is
    never an observation: G6's own text in the file, once."""
    src = formal("def observe(case):\n    return {'result': str(case)}\n")["test"]
    for own in (r6.canon, r6._loads, r6._number, r6._object, r6._no_constant, r6._Refused):
        assert src.count(inspect.getsource(own)) == 1
    space = _load(src)
    space["_G6_CHILD"] = f"import sys\nsys.stdin.buffer.read()\nsys.stdout.write({said!r})\n"   # a child that says this
    for cell in space["_G6_CELLS"]:
        with pytest.raises(pytest.skip.Exception, match="no answer"):
            space["test_formal"](cell)


def test_the_compiled_file_evaluates_no_annotation_an_older_python_could_not_and_has_no_future_import():
    """The file runs on the bug's own Python, and other text may come before it, as review 4's import test puts a control."""
    tree = ast.parse(formal("def observe(case):\n    return {'result': str(case)}\n")["test"])
    assert not any(isinstance(node, ast.ImportFrom) and node.module == "__future__" for node in ast.walk(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for given in [*(arg.annotation for arg in node.args.args), node.returns]:
                assert given is None or isinstance(given, ast.Constant) or (
                    isinstance(given, ast.Name) and given.id in {"str", "object", "Decimal"}), (node.name, ast.dump(given))


# --- 6. the probe imports on pytest's own path ---------------------------------------------------------------------------------
def test_the_probe_imports_the_project_on_the_path_pytest_imports_it_on(tmp_path):
    """Review 4's lead: a project importable only through pytest's ``pythonpath`` setting could not be imported in the probe's
    process, so its cells were skipped."""
    got = formal("from review_project import result\ndef observe(case):\n    return {'result': result()}\n")
    assert got["status"] == "stands"
    control = "from review_project import result\nassert result() == 'ok'\n" + got["test"]
    ran = run_compiled(tmp_path / "project", control, config="[pytest]\npythonpath = src\n",
                       extra_files={"src/review_project.py": "def result():\n    return 'ok'\n"})
    assert ran.exit == 0 and list(ran.results.values()) == ["passed"], ran.tail


def test_the_probe_keeps_the_project_folder_as_its_working_folder(tmp_path):
    probe = "from pathlib import Path\ndef observe(case):\n    return {'result': Path('data.txt').read_text()}\n"
    ran = run_compiled(tmp_path, formal(probe)["test"], extra_files={"data.txt": "ok"})
    assert list(ran.results.values()) == ["passed"], ran.tail


# --- 2. reader 2 gets no copy of reader 1's expectations -----------------------------------------------------------------------
SECRET = {"when": {"x": 0}, "expect": {"result": "R1_SECRET", "flag": True, "n": -37}, "quote": QUOTE}
HALF = {"when": {"x": 1}, "expect": {"n": 2.5}, "quote": QUOTE}
TELLS = {"factors": {"x": [0, 1]}, "observations": ["result", "flag", "n"]}
OBSERVE = "def observe(case):\n    return {'result': str(case), 'flag': False, 'n': 0}\n"


def _shown(user: str) -> str:
    return user.split("<probe>\n", 1)[1].rsplit("\n</probe>", 1)[0]


def test_reader_2_is_given_no_copy_of_reader_1s_table_in_the_probes_data():
    """Review 4 finding 2: data in the probe, neither a comment nor a docstring, reached reader 2 whole."""
    row = {"when": {}, "expect": {"result": "R1_PRIVATE_EXPECTATION_84"}, "quote": QUOTE}
    probe = f"READER_ONE_RULES = {json.dumps([row])}\ndef observe(case):\n    return {{'result': 'ok'}}\n"
    caller = Caller([row])
    got = formal(probe, rows=[row], caller=caller)
    [(_, user)] = caller.calls
    assert json.dumps([row]) not in user and "R1_PRIVATE_EXPECTATION_84" not in user
    assert _shown(user).startswith("READER_ONE_RULES = MASKED.literal\n") and got["masked"] == 1
    assert repr(probe) in got["test"]                 # the file runs the probe as reader 1 wrote it


@pytest.mark.parametrize(("line", "shown", "masked"), [
    ("A = 'R1_SECRET'", "A = MASKED.literal", 1),                                       # a value a row expects
    ("A = 'R1_' 'SECRET'", "A = MASKED.literal", 1),                                    # one literal, as the parser reads it
    ("A = -37", "A = MASKED.literal", 1),                                               # a negative number is a literal too
    ("A = -37.0", "A = MASKED.literal", 1),                                             # one value by canon
    ("A = 2.5", "A = MASKED.literal", 1),
    ("A = ['R1_SECRET', 'other']", "A = [MASKED.literal, 'other']", 1),
    ("A = ('R1_SECRET', 'other')", "A = (MASKED.literal, 'other')", 1),                # no JSON value; its parts are literals
    ("A = {'R1_SECRET': 1}", "A = {MASKED.literal: 1}", 1),
    (f"A = {SECRET!r}", "A = MASKED.literal", 1),                                       # a row, so its exempt True is told by none
    (f"A = [{SECRET!r}, {HALF!r}]", "A = MASKED.literal", 1),                           # the rules list
    (f"A = {{'table': [{SECRET!r}], 'other': 'R1_SECRET'}}", "A = MASKED.literal", 1),  # a display that holds a row
    ("A = f'{0}R1_SECRET'", "A = MASKED.literal", 1),                                   # an f-string is masked whole
    ("A = 'R1_SECRET'; B = -37", "A = MASKED.literal; B = MASKED.literal", 2),
    ("A = [True, None, 0, 1, -1, 1.0, '', [], {}]", "A = [True, None, 0, 1, -1, 1.0, '', [], {}]", 0),   # each tells nothing
    ("A = 'other'; B = 37", "A = 'other'; B = 37", 0),
])
def test_each_literal_that_tells_one_of_reader_1s_expectations_is_masked_and_counted(line, shown, masked):
    caller = Caller([SECRET, HALF])
    got = formal(f"{line}\n{OBSERVE}", rows=[SECRET, HALF], caller=caller, **TELLS)
    [(_, user)] = caller.calls
    text = _shown(user)
    assert text == f"{shown}\n{OBSERVE}" and got["masked"] == masked
    ast.parse(text)                                   # it still parses
    assert "R1_SECRET" not in text and "-37" not in text and "2.5" not in text


def test_a_probe_with_no_literal_of_reader_1s_is_sent_as_its_bare_text_and_nothing_is_masked():
    probe = '"""R1_DOC"""\n# R1_NOTE\nA = "other"\ndef observe(case):\n    return {"result": str(case)}  # R1_TRAILING\n'
    caller = Caller()
    got = formal(probe, caller=caller)
    [(_, user)] = caller.calls
    assert _shown(user) == r6._bare(probe) and got["masked"] == 0
    assert user == r6.reader_2_text(f"Issue\n\n{QUOTE}", r6.parse_model(answer(probe)["test"])[0])


def test_a_probe_the_parser_cannot_read_is_invalid_and_reader_2_is_not_asked():
    """A probe the parser cannot read has no literal it can mask, and cannot run, so nothing of it is shown to reader 2."""
    caller = Caller()
    got = formal("# R1_COMMENT\nA = 'ok'\ndef observe(case) return {}\n", caller=caller)
    assert got["status"] == "invalid" and "parser" in got["status_reason"] and caller.calls == [] and got["test"] == ""


def test_the_summary_reports_each_bugs_masks(tmp_path):
    def keep(task, found):
        path = r3.bug_record(tmp_path, task, r6.FORMAL)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task": task, "instruction": r6.FORMAL.digest, "found": found}), "utf-8")

    keep("a-1", formal(f"A = 'R1_SECRET'; B = -37\n{OBSERVE}", rows=[SECRET, HALF], **TELLS))
    keep("b-2", formal(OBSERVE, rows=[SECRET, HALF], **TELLS))
    keep("c-3", {"verdict": "none", "status": "no_model"})
    got = r6.summary(tmp_path, ["a-1", "b-2", "c-3"])
    assert [bug["masked"] for bug in got["bugs"]] == [2, 0, None] and got["masked"] == 2


# --- 3. each run gets a container of its own -----------------------------------------------------------------------------------
class _Docker:
    """A docker that records each call and each shell command it is given, and starts nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(("docker", args))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def sh(self, ctr, script, *args, **kwargs):
        self.calls.append(("sh", (ctr, script)))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def _run_on(tmp_path, ins):
    docker = _Docker()
    patch = tmp_path / "copy.patch"
    patch.write_text("patch\n", "utf-8")
    ctx = SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3)
    r3.run_on(ctx, we.task_by_id("black-5634"), "def test_a():\n    pass\n", patch, ins)
    calls = docker.calls
    life = [(args[0], args[args.index("--name") + 1] if args[0] == "run" else args[2]) for kind, args in calls
            if kind == "docker" and args[:2] in (("run", "-d"), ("rm", "-f"))]
    writes = [args[4] for kind, args in calls if kind == "docker" and args[:2] == ("exec", "-i")]
    copied = [args[2].split(":")[0] for kind, args in calls if kind == "docker" and args[0] == "cp"]
    applied = [ctr for kind, (ctr, *rest) in calls if kind == "sh" and "git apply" in rest[0]] if calls else []
    runs = [ctr for kind, (ctr, *rest) in calls if kind == "sh" and we.JUNIT_OUT in rest[0]] if calls else []
    return life, writes, copied, applied, runs


def test_replay_6_runs_the_parent_and_the_patch_each_in_a_container_of_its_own(tmp_path):
    """Review 4 finding 3: the probe's process works in the project folder, and the parent and the patch ran in one container,
    so a file the probe wrote there changed the patch's run."""
    life, writes, copied, applied, runs = _run_on(tmp_path, r6.FORMAL)
    (_, first), (_, gone), (_, second), (_, ended) = life
    assert [kind for kind, _ in life] == ["run", "rm", "run", "rm"]          # one removed before the next starts
    assert first == gone and second == ended and first != second
    assert writes == [first, second] and copied == applied == [second] and runs == [first, second]


def test_replays_3_4_and_5_run_the_parent_and_the_patch_in_one_container_as_before(tmp_path):
    for n, ins in enumerate((r3.rule(), r4.SYMPTOM, r5.RELATION)):
        (tmp_path / str(n)).mkdir()
        life, writes, copied, applied, runs = _run_on(tmp_path / str(n), ins)
        [(_, name), _] = life
        assert life == [("run", name), ("rm", name)] and writes == copied == applied == [name] and runs == [name, name]


def test_only_replay_6_turns_own_containers_on_and_the_others_instructions_are_as_they_were_at_db29929():
    assert r6.FORMAL.own_containers and not any(ins.own_containers for ins in (r3.rule(), r4.SYMPTOM, r5.RELATION))
    at_db29929 = {"838d8da877502eee": (r3.rule(), "final_review_rule-838d8da877502eee", "/opt/we/rule", "test_rule.py", False),
                  "780e81dac0e14cb1": (r4.SYMPTOM, "final_review_symptom-780e81dac0e14cb1", "/opt/we/symptom",
                                       "test_symptom.py", False),
                  "fd779fd79374dfe4": (r5.RELATION, "final_review_relation-fd779fd79374dfe4", "/opt/we/relation",
                                       "test_relation.py", True)}
    for digest, (ins, folder, at, file, same_case) in at_db29929.items():
        assert (ins.digest, ins.folder, ins.at, ins.file, ins.same_case) == (digest, folder, at, file, same_case)
    assert (r3.INSTRUCTION, r3.FOLDER, r4.INSTRUCTION, r4.FOLDER, r5.INSTRUCTION, r5.FOLDER) == (
        "838d8da877502eee", "final_review_rule-838d8da877502eee", "780e81dac0e14cb1", "final_review_symptom-780e81dac0e14cb1",
        "fd779fd79374dfe4", "final_review_relation-fd779fd79374dfe4")
    assert (r6.INSTRUCTION, r6.FOLDER) == ("b8df0b33deca6844", "final_review_formal-b8df0b33deca6844")


# --- 4. a probe that overruns ends with everything it started ------------------------------------------------------------------
def _descendant_probe(pid_file: Path) -> str:
    descendant = "import time; time.sleep(4)"
    return ("import json, subprocess, sys, time\nfrom pathlib import Path\n"
            f"child = subprocess.Popen([sys.executable, '-c', {descendant!r}], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            f"Path({str(pid_file)!r}).write_text(json.dumps(child.pid), encoding='ascii')\n"
            "time.sleep(10)\ndef observe(case):\n    return {'result': 'ok'}\n")


@pytest.mark.skipif(sys.platform != "win32", reason="uses Windows process handles to see what is still running")
def test_a_probe_that_overruns_is_ended_with_the_process_it_started_on_windows(tmp_path):
    """Review 4 finding 6: the probe's process was killed on its timeout, and what it had started kept running."""
    import ctypes
    from ctypes import wintypes

    pid_file = tmp_path / "descendant.json"
    source = formal(_descendant_probe(pid_file))["test"].replace("_G6_SECONDS = 120", "_G6_SECONDS = 1")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    started, handle, alive = time.monotonic(), None, False
    try:
        ran = run_compiled(tmp_path / "project", source)
        if pid_file.is_file():
            handle = kernel.OpenProcess(0x100000, False, json.loads(pid_file.read_text("ascii")))   # SYNCHRONIZE only
            alive = bool(handle) and kernel.WaitForSingleObject(handle, 0) == 258                   # WAIT_TIMEOUT
    finally:
        if handle:                                    # never killed here: it ends by itself within 4 seconds
            assert kernel.WaitForSingleObject(handle, 10000) == 0
            kernel.CloseHandle(handle)
        else:
            time.sleep(max(0, 6 - (time.monotonic() - started)))
    assert pid_file.is_file() and not alive, "the probe timed out and the process it started was still running"
    assert list(ran.results.values()) == ["skipped"], ran.tail


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups; Windows has its own test above")
def test_a_probe_that_overruns_is_ended_with_its_process_group_on_posix(tmp_path):
    pid_file = tmp_path / "descendant.json"
    source = formal(_descendant_probe(pid_file))["test"].replace("_G6_SECONDS = 120", "_G6_SECONDS = 1")
    ran = run_compiled(tmp_path / "project", source)
    pid = json.loads(pid_file.read_text("ascii"))

    def running() -> bool:
        try:
            os.kill(pid, 0)                           # signal 0 asks only whether it is there
        except ProcessLookupError:
            return False
        stat = Path(f"/proc/{pid}/stat")              # a zombie no one has reaped yet has ended
        return not (stat.is_file() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z")

    deadline = time.monotonic() + 2
    while running() and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = running()
    while running():                                  # never killed here: it ends by itself within 4 seconds
        time.sleep(0.1)
    assert not alive, "the probe timed out and the process it started was still running"
    assert list(ran.results.values()) == ["skipped"], ran.tail


# --- 5. a saved compiled test is named by its compiler -------------------------------------------------------------------------
def _record(root: Path, task: str, found: dict) -> Path:
    saved = r3.bug_record(root, task, r6.FORMAL)
    saved.parent.mkdir(parents=True, exist_ok=True)
    fr.write_whole(saved, json.dumps({"task": task, "instruction": r6.FORMAL.digest, "found": found}))
    return saved


def _patch(root: Path) -> list[dict[str, str]]:
    (root / "loop").mkdir(exist_ok=True)
    (root / "run").mkdir()
    (root / "run" / "final.patch").write_text("a saved patch\n", encoding="utf-8")
    return [{"task": "black-5634", "run": "run"}]


def _no_reader(*args, **kwargs):
    raise AssertionError("a reader was asked")


def test_a_model_compiled_before_the_repair_is_compiled_again_on_restart(tmp_path):
    """Review 4 finding 4: a record was named by the instruction alone, so on a restart a compiled test made by an earlier
    compiler was reused however the compiler had changed since."""
    source = subprocess.run(["git", "show", "ee6c61f:validation/worked_examples/replay6.py"], cwd=ROOT,
                            capture_output=True, check=True, timeout=30).stdout
    old_path = tmp_path / "old_replay6.py"
    old_path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("_followup_previous_replay6", old_path)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    model = json.dumps({"factors": {"x": [0]}, "observations": ["result"],
                        "probe": "def observe(case):\n    return {'result': b'bad'}\n", "rules": [ROW]})
    try:
        spec.loader.exec_module(old)
        found = old.formal({"verdict": "defect", "test": model, "error": ""}, "Issue", QUOTE, Caller())
    finally:
        sys.modules.pop(spec.name, None)
    assert old.FORMAL.digest == r6.FORMAL.digest and found["status"] == "stands"
    _record(tmp_path, "black-5634", found)
    caller, ran = Caller(), []

    def run(ctx, task, test, patch):
        ran.append(test)
        return run_compiled(tmp_path / "parent", test), run_compiled(tmp_path / "patch", test)

    [record] = r6.review_bug(SimpleNamespace(), tmp_path, we.task_by_id("black-5634"), _patch(tmp_path), "Issue", QUOTE,
                             step=_no_reader, caller=caller, run=run)
    assert record["said"] != "reproduced" and caller.calls == []
    assert ran == [r6.formal({"verdict": "defect", "test": model, "error": ""}, "Issue", QUOTE, Caller())["test"]]
    again = fr.read(r3.bug_record(tmp_path, "black-5634", r6.FORMAL))["found"]
    assert again["compiler"] == r6.COMPILER and again["test"] == ran[0] and again["model"] == model


def test_a_record_names_its_compiler_and_keeps_reader_2s_reply():
    caller = Caller()
    got = formal("def observe(case):\n    return {'result': str(case)}\n", caller=caller)
    assert got["compiler"] == r6.COMPILER and got["reader_2_reply"] == json.dumps({"rules": [ROW]})
    assert len(r6.COMPILER) == 16 and int(r6.COMPILER, 16) >= 0


def test_a_record_its_own_compiler_made_is_reused_as_it_was(tmp_path):
    found = {**formal("def observe(case):\n    return {'result': str(case)}\n"), "test": "THE SAVED FILE"}
    saved = _record(tmp_path, "black-5634", found)
    before = saved.read_bytes()
    ran = []
    r6.review_bug(SimpleNamespace(), tmp_path, we.task_by_id("black-5634"), _patch(tmp_path), "Issue", QUOTE,
                  step=_no_reader, caller=SimpleNamespace(complete=_no_reader),
                  run=lambda ctx, task, test, patch: ran.append(test) or (None, None))
    assert ran == ["THE SAVED FILE"] and saved.read_bytes() == before


@pytest.mark.parametrize("kept", ["reply", "table"])
def test_a_standing_record_another_compiler_made_is_made_again_exactly_from_its_answers(tmp_path, kept):
    """From reader 2's kept reply, every number as it was written: two values no float tells apart stay two. A record older than
    the reply's keeping has reader 2's kept table, whose numbers are floats: two such values can only become one, which leaves
    the bug ambiguous, never flagged."""
    rows = [{"when": {"x": 0}, "expect": {"result": "A"}, "quote": QUOTE}, {"when": {"x": 1}, "expect": {"result": 3}, "quote": QUOTE}]
    text = answer("def observe(case):\n    return {'result': 3}\n", rows=rows, factors={"x": [0, 1]})
    text["test"] = text["test"].replace('"A"', "0.1000000000000000055511151231257827")
    reply = json.dumps({"rules": rows}).replace('"A"', "0.1000000000000000055511151231257827")
    fresh = r6.formal(text, "Issue", QUOTE, SimpleNamespace(complete=lambda system, user: reply))
    assert fresh["status"] == "stands" and fresh["comparison"]["cells_unholdable"] == 1
    old = {**fresh, "compiler": "an-older-one", "test": "AN OLDER FILE", "masked": 7}    # what reader 2 was shown then
    if kept == "table":
        del old["reader_2_reply"]
    saved = _record(tmp_path, "black-5634", old)
    assert r6.again(tmp_path, "black-5634", "Issue", QUOTE)
    got = fr.read(saved)["found"]
    if kept == "reply":
        assert got == json.loads(json.dumps({**fresh, "masked": 7}))
    else:
        assert got["status"] == "ambiguous" and got["test"] == "" and got["compiler"] == r6.COMPILER and got["masked"] == 7
    assert not r6.again(tmp_path, "black-5634", "Issue", QUOTE)          # made once


def test_a_record_that_does_not_stand_has_nothing_compiled_and_is_reused_as_it_was(tmp_path):
    found = {**formal("def observe(case):\n    return {'result': 1}\n", caller=Caller([])), "compiler": "an-older-one"}
    assert found["status"] == "no_agreed_case"
    saved = _record(tmp_path, "black-5634", found)
    before = saved.read_bytes()
    assert not r6.again(tmp_path, "black-5634", "Issue", QUOTE) and saved.read_bytes() == before


# --- a patch's verdict speaks only for the test its bug's record holds now ------------------------------------------------------
# The class of review 4's finding 4, at its root: a saved artifact is named by less than what made it. A bug's record made
# again (by another compiler, or asked again after one cut short) holds another test; the verdicts the old test gave stay on
# disk under each patch's digest, and the scheduler and the counts reused them. A verdict counts only while its bug's record
# holds the test it ran, and never while that record waits to be made again (replay 6's ``stale``).
FAILS = we.ProbeRun(1, {"test_formal::test_formal[c000-result-x=0]": "failed"}, {}, "")


def _verdict(root: Path, row: dict, ins, test: str) -> str:
    sha = hashlib.sha256((root / str(row["run"]) / "final.patch").read_bytes()).hexdigest()
    gate = {"same_case": True} if ins.same_case else {}
    fr.keep(root, row, {"said": "none", "verdict": "none", "test": test, **gate}, ins.folder, sha=sha)
    return sha


def _bug(root: Path, ins, found: dict) -> None:
    saved = r3.bug_record(root, "black-5634", ins)
    saved.parent.mkdir(parents=True, exist_ok=True)
    fr.write_whole(saved, json.dumps({"task": "black-5634", "instruction": ins.digest, "found": found}))


def _finished_run(root: Path) -> dict:
    [row] = _patch(root)
    row = {**row, "arm": "L9", "repeat": 0}
    (root / "loop" / "L9-black-5634-0.json").write_text(json.dumps({**row, "status": "finished"}), "utf-8")
    return row


@pytest.mark.parametrize("ins", [r3.rule(), r4.SYMPTOM, r5.RELATION, r6.FORMAL], ids=lambda ins: ins.folder.split("-")[0])
def test_a_verdict_counts_only_while_its_bugs_record_holds_the_test_it_ran(tmp_path, monkeypatch, ins):
    row = _finished_run(tmp_path)
    monkeypatch.setattr(rc.compare, "saved", lambda root: {("L9", "black-5634", 0): {"amended": True, "maintainers": True}})
    name = {folder: name for name, folder in rc.REVIEWERS}[ins.folder]
    own = {"status": "stands", "compiler": r6.COMPILER} if ins is r6.FORMAL else {}
    sha = _verdict(tmp_path, row, ins, "THE TEST IT RAN")
    for test, waits in (("THE TEST IT RAN", False), ("ANOTHER TEST", True)):
        _bug(tmp_path, ins, {"verdict": "defect", "rule": "r", "why": "w", "error": "", "test": test, **own})
        assert (r3.waiting(tmp_path, ["black-5634"], ins) != []) == waits, (ins.folder, test)
        assert (rc.patches(tmp_path, ["black-5634"])[sha][name] is None) == waits, (ins.folder, test)   # the counts agree


def test_a_verdict_with_no_record_beside_it_speaks_for_the_test_it_carries(tmp_path):
    row = _finished_run(tmp_path)
    _verdict(tmp_path, row, r3.rule(), "THE TEST IT RAN")
    assert r3.waiting(tmp_path, ["black-5634"], r3.rule()) == []


def test_replay_6_leaves_waiting_a_patch_whose_standing_record_another_compiler_made(tmp_path):
    row = _finished_run(tmp_path)
    found = {**formal("def observe(case):\n    return {'result': str(case)}\n"), "test": "AN OLDER FILE"}
    assert found["status"] == "stands"
    _verdict(tmp_path, row, r6.FORMAL, "AN OLDER FILE")
    for compiler, waits in ((r6.COMPILER, False), ("an-older-one", True)):
        _record(tmp_path, "black-5634", {**found, "compiler": compiler})
        assert (r3.waiting(tmp_path, ["black-5634"], r6.FORMAL) != []) == waits, compiler
    assert all(r3.rule().stale is None for _ in [0]) and r4.SYMPTOM.stale is None and r5.RELATION.stale is None


def test_replay_6s_run_makes_such_a_record_again_and_reviews_its_patch_with_no_reader_asked(tmp_path, monkeypatch):
    """Through ``main``, as a restart meets it: the scheduler is asked first, so a patch it left with a reused verdict was never
    reviewed and its bug's record never made again."""
    root = tmp_path / "round3"
    root.mkdir()
    row = _finished_run(root)
    found = {**formal("def observe(case):\n    return {'result': str(case)}\n"), "test": "AN OLDER FILE",
             "compiler": "an-older-one"}
    _record(root, "black-5634", found)
    _verdict(root, row, r6.FORMAL, "AN OLDER FILE")
    ran = []

    def run_on(ctx, task, test, patch, ins=None):
        ran.append(test)
        return FAILS, FAILS

    monkeypatch.setattr(r6.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(rc.compare, "saved", lambda root: {})
    monkeypatch.setattr(we, "Docker", lambda: object())
    monkeypatch.setattr(we, "g6_info", lambda commit: {"commit": commit})
    monkeypatch.setattr(we, "read_issue", lambda task: ("Issue", QUOTE))
    monkeypatch.setattr(we, "read_thread", lambda task: "")
    monkeypatch.setattr(r6, "reader_2", lambda: SimpleNamespace(complete=_no_reader))
    monkeypatch.setattr(fr, "_step", _no_reader)
    monkeypatch.setattr(r3, "run_on", run_on)
    r6.main(["--tasks", "black-5634"])
    again = fr.read(r3.bug_record(root, "black-5634", r6.FORMAL))["found"]
    assert again["compiler"] == r6.COMPILER and ran == [again["test"]] and again["test"] != "AN OLDER FILE"

# --- follow-up review 1 ---------------------------------------------------------------------------------------------------------
def _user(probe, rows):
    caller = Caller(rows)
    got = formal(probe, rows=rows, caller=caller, **TELLS)
    [(_, user)] = caller.calls
    return got, _shown(user)


def test_follow_up_review_1_finding_1_a_mask_in_a_case_pattern_leaves_python_that_parses():
    """A literal that tells stands as ``MASKED.literal``: an expression wherever a literal is, and a value pattern in a ``case``,
    where ``...`` is no pattern."""
    probe = ("def observe(case):\n    match case['x']:\n        case 'R1_SECRET':\n            return {'result': 'a'}\n"
             "        case {'k': 'R1_SECRET'}:\n            return {'result': 'b'}\n        case _:\n"
             "            return {'result': None}\n")
    got, text = _user(probe, [SECRET, HALF])
    ast.parse(text)
    assert "case MASKED.literal:" in text and "case {'k': MASKED.literal}:" in text and got["masked"] == 2
    assert "R1_SECRET" not in text


def test_a_probe_that_masking_would_leave_unreadable_is_withheld_from_reader_2():
    """Where a mask leaves text Python's parser cannot read (a sign before a mask in a pattern, which allows a sign only before
    a number), reader 2 is shown that the probe is withheld, never the text, and never the literal."""
    probe = "def observe(case):\n    match case['x']:\n        case -2.5:\n            return {'result': 'a'}\n" \
            "        case _:\n            return {'result': None}\n"
    got, text = _user(probe, [SECRET, HALF])
    assert text == r6.WITHHELD and "2.5" not in text and got["masked"] == 1


@pytest.mark.parametrize("line", [f"A = {json.dumps(json.dumps([SECRET, HALF]))}",       # the rules, as JSON text
                                  f"A = {json.dumps(json.dumps(SECRET))}",                # a row, as JSON text
                                  "A = '{\"k\": [\"R1_SECRET\"]}'",                      # an expected value inside JSON text
                                  "A = '-37'"])                                           # an expected number as JSON text
def test_follow_up_review_1_finding_2_a_literal_whose_text_as_json_tells_is_masked(line):
    got, text = _user(f"{line}\n{OBSERVE}", [SECRET, HALF])
    assert text == f"A = MASKED.literal\n{OBSERVE}" and got["masked"] == 1


@pytest.mark.parametrize("line", ["A = 'true'", "A = '[]'", "A = '{\"k\": \"other\"}'", "A = 'not json R1'"])
def test_a_literal_whose_text_as_json_tells_nothing_is_shown(line):
    got, text = _user(f"{line}\n{OBSERVE}", [SECRET, HALF])
    assert text == f"{line}\n{OBSERVE}" and got["masked"] == 0


class _RemovalFails:
    """A docker whose ``rm`` fails, every other call succeeding, and the containers it made and was asked to remove kept."""

    def __init__(self):
        self.live, self.removed = set(), []

    def __call__(self, *args, **kwargs):
        if args[:2] == ("run", "-d"):
            self.live.add(args[args.index("--name") + 1])
        if args[:2] == ("rm", "-f"):
            self.removed.append(args[2])
            return subprocess.CompletedProcess(args, 1, b"", b"removal failed")
        return subprocess.CompletedProcess(args, 0, b"", b"")


@pytest.mark.parametrize("ins", [r6.FORMAL, r3.rule()], ids=["own containers", "one container"])
def test_follow_up_review_1_finding_3_a_container_that_was_not_removed_leaves_no_counted_verdict(tmp_path, monkeypatch, ins):
    docker = _RemovalFails()
    ctx = SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3)
    ran = we.ProbeRun(0, {"case": "passed"}, {}, "")
    monkeypatch.setattr(fr, "parent_run", lambda *a: ran)
    monkeypatch.setattr(fr, "patch_run", lambda *a: ran)
    monkeypatch.setattr(r3, "_patched", lambda *a: ran)
    row = _finished_run(tmp_path)
    [record] = r3.review_bug(ctx, tmp_path, we.task_by_id(row["task"]), [row], "Issue", QUOTE,
                             ask=lambda *a, **kw: ({"verdict": "defect", "test": "test", "error": ""}, None, None), ins=ins)
    assert docker.removed and not fr.counts(record) and "rm" in record["error"]


def test_a_run_that_failed_keeps_its_own_error_when_its_container_is_not_removed_either(tmp_path, monkeypatch):
    docker = _RemovalFails()
    ctx = SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3)

    def broke(*args):
        raise RuntimeError("the parent's run broke")

    monkeypatch.setattr(fr, "parent_run", broke)
    row = _finished_run(tmp_path)
    [record] = r3.review_bug(ctx, tmp_path, we.task_by_id(row["task"]), [row], "Issue", QUOTE,
                             ask=lambda *a, **kw: ({"verdict": "defect", "test": "test", "error": ""}, None, None),
                             ins=r6.FORMAL)
    assert docker.removed and not fr.counts(record) and "the parent's run broke" in record["error"]


def test_every_docker_call_a_review_run_makes_is_read_for_its_status():
    checked = r3.Checked(lambda *args, **kwargs: subprocess.CompletedProcess(args, 125, b"", b"no such image"))
    with pytest.raises(RuntimeError, match="125"):
        checked("cp", "a", "b")


@pytest.mark.skipif(os.name != "nt", reason="a Windows job object; POSIX ends the probe's process group")
def test_follow_up_review_1_finding_4_a_timeout_ends_a_process_whose_parent_had_already_exited(tmp_path):
    import ctypes
    import textwrap
    from ctypes import wintypes
    pid_file = tmp_path / "grandchild.json"
    intermediary = textwrap.dedent(f"""
        import json, subprocess, sys
        from pathlib import Path
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Path({str(pid_file)!r}).write_text(json.dumps(child.pid), encoding='ascii')
    """)
    probe = textwrap.dedent(f"""
        import subprocess, sys, time
        def observe(case):
            subprocess.run([sys.executable, '-c', {intermediary!r}], check=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while True:
                time.sleep(0.05)
    """)
    space = {"__name__": "follow_up_compiled"}
    exec(r6.compile_test(probe, {"x": [0]}, {(r6.canon({"x": 0}), "result"): "ok"}), space)
    space["_G6_SECONDS"] = 1.5
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    started, handle, alive = time.monotonic(), None, False
    try:
        answer_, why = space["_g6_ask"]()
        assert answer_ is None and "ran past" in why
        handle = kernel.OpenProcess(0x100000, False, json.loads(pid_file.read_text(encoding="ascii")))
        alive = bool(handle) and kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        if handle:
            assert kernel.WaitForSingleObject(handle, 10000) == 0, "the descendant must end before this test does"
            kernel.CloseHandle(handle)
        else:
            time.sleep(max(0, 9 - (time.monotonic() - started)))
    assert not alive, "the probe overran, and a process it started outlived it"
