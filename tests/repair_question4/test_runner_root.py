"""The observation runner owns startup, framing and comparison; fixtures run no services."""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest


def runner():
    from g6.harness import behaviour_runner
    return behaviour_runner


@pytest.mark.parametrize("raw", [b"", b"G6R1\n", b"junk", b"G6R1\n2\n{}junk",
                                     b"G6R1\n2\n{}G6R1\n2\n{}", b"G6R1\n1\n{",
                                     b"G6R1\n-1\n{}", b"G6R1\n999999999999999999999\n{}"])
def test_every_incomplete_or_extra_frame_is_unknown(raw):
    assert runner().read_frame(raw) is None


def test_frame_round_trip_and_unknown_schema():
    one = {"version": "observation.v1", "outcomes": [{"returned": "5", "arguments": ["2", "3"]}],
           "errors": {}}
    assert runner().read_frame(runner().frame(one)) == one
    assert runner().read_frame(runner().frame({"result": "passed"})) is None


def test_actual_frames_duplicates_trailing_bytes_and_crashes_are_unknown():
    mod = runner()
    value = {'version': 'observation.v1', 'outcomes': [{'returned': '5', 'arguments': []}], 'errors': {}}
    good = mod.frame(value)
    data = {'cases': [{'expect': 'returns', 'value': 5, 'args': []}]}
    for raw in (b'', good + good, good + b'\n', b'prefix' + good, good[:-1],
                mod.frame({**value, 'unexpected': True}),
                mod.frame({**value, 'outcomes': [{'returned': 'NaN', 'arguments': []}]})):
        assert mod.read_frame(raw) is None
        assert mod.finish(0, raw, data)['result'] == 'error'
    for exit_code in (None, -1, 1, 73):
        assert mod.finish(exit_code, good, data)['result'] == 'error'


def invoke(tmp_path, code, *, startup="", expected=5):
    mod = runner()
    root = tmp_path / "copy"
    root.mkdir()
    (root / "calc.py").write_text(code, encoding="utf-8")
    (root / "conftest.py").write_text(startup, encoding="utf-8")
    (root / "sitecustomize.py").write_text("raise RuntimeError('site ran')\n", encoding="utf-8")
    data = {"function": ["module", "calc", "add", "calc.py"],
            "cases": [{"args": [2, 3], "kwargs": {}, "expect": "returns", "value": expected}]}
    channel = tmp_path / "channel"
    channel.write_bytes(b"")
    boot = tmp_path / "bootstrap.py"
    boot.write_text(mod.BOOTSTRAP, encoding="utf-8")
    completed = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(root), str(channel),
                                json.dumps(mod.request(data))], capture_output=True, timeout=10)
    return mod.finish(completed.returncode, channel.read_bytes(), data)


def test_one_invocation_cannot_read_the_selected_outcome(tmp_path):
    statuses = []
    code = ("import json,sys\ndef add(a,b):\n"
            "    request=json.loads(sys.argv[-1])\n"
            "    return request.get('cases',[{}])[0].get('value',-1)\n")
    for index, value in enumerate((5, -1)):
        where = tmp_path / str(index)
        where.mkdir()
        statuses.append(invoke(where, code, expected=value)["result"])
    assert statuses != ["passed", "passed"], "the same call accepted both outcomes by reading the chosen expectation"


@pytest.mark.parametrize("startup", ["import calc\ncalc.add=lambda a,b:5\n",
    "def pytest_pyfunc_call(pyfuncitem):\n    return True\n",
    "import builtins\nbuiltins.fake=5\n"])
def test_no_repository_startup_can_replace_the_call(tmp_path, startup):
    got = invoke(tmp_path, "def add(a,b):\n    return a-b\n", startup=startup)
    assert got["result"] == "failed"


@pytest.mark.parametrize("code", ["import os\ndef add(a,b):\n    os._exit(73)\n",
                                  "import os\ndef add(a,b):\n    os._exit(0)\n"])
def test_withdrawn_completion_witness_supports_neither_outcome(tmp_path, code):
    assert invoke(tmp_path, code)["result"] == "error"


def test_running_repository_output_is_the_observed_behaviour(tmp_path):
    assert invoke(tmp_path, "def add(a,b):\n    return 5\n")["result"] == "passed"


def test_a_forged_in_process_frame_is_read_as_the_observed_outcome(tmp_path):
    code = '''import json,os,struct,sys
def add(a,b):
    payload=json.dumps({'version':'observation.v1','outcomes':[
        {'returned':'5','arguments':['2','3']}],'errors':{}},sort_keys=True).encode()
    with open(sys.argv[-2],'ab',buffering=0) as channel:
        channel.write(b'G6R1\\n'+struct.pack('>I',len(payload))+payload)
    os._exit(0)
'''
    observed = invoke(tmp_path, code)
    assert observed["result"] == "passed"
    assert observed["observed"] == {
        "version": "observation.v1",
        "outcomes": [{"returned": "5", "arguments": ["2", "3"]}],
        "errors": {},
    }


def test_reply_names_the_limit_when_the_in_process_frame_is_forged(tmp_path):
    from g6.harness import patch_report, question_store
    from g6.mcp import behaviour

    code = '''import json,os,struct,sys
def add(a,b):
    payload=json.dumps({'version':'observation.v1','outcomes':[
        {'returned':'5','arguments':['2','3']}],'errors':{}},sort_keys=True).encode()
    with open(sys.argv[-2],'ab',buffering=0) as channel:
        channel.write(b'G6R1\\n'+struct.pack('>I',len(payload))+payload)
    os._exit(0)
'''
    observed = invoke(tmp_path, code)
    assert observed['result'] == 'passed'  # Limit ruled on the owner's behalf, retained as a measured witness.
    reply = behaviour._reply(behaviour.PENDING, 'fixture', state=question_store.State())
    report = patch_report._confirmed({'confirmed_expectations': {'expectations': [
        {'question': {}, 'answer': {}, 'check': observed}]}})
    assert runner().LIMIT in reply['execution_limit']
    assert runner().LIMIT in '\n'.join(line for item in report for line in item)


def test_host_comparison_retains_type_and_exception_subtyping():
    mod = runner()
    for value in (True, 5.0, "5", -1):
        data = {"cases": [{"expect": "returns", "value": 5}]}
        observed = {"version": "observation.v1", "errors": {},
                    "outcomes": [{"returned": json.dumps(value, sort_keys=True), "arguments": []}]}
        assert mod.finish(0, mod.frame(observed), data)["result"] == "failed"


def test_inventory_runner_dispatch_and_total_frame_readers():
    from g6.harness import confirmed_checks, verification_search
    source = Path(verification_search.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_check")
    assert "behaviour_runner" in ast.unparse(run)
    confirm = Path(confirmed_checks.__file__).read_text(encoding="utf-8")
    assert "vs._run_check" in confirm and "repo_tests._run(" not in confirm
    assert runner().RUN_TEMPLATES == frozenset({"example.v1", "differential.v1"})
    frame_readers = [n.name for n in ast.parse(Path(runner().__file__).read_text(encoding="utf-8")).body
                     if isinstance(n, ast.FunctionDef) and "frame" in n.name]
    assert frame_readers == ["frame", "read_frame"]
