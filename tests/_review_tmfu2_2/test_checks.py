"""Negative probes for the requested repair boundaries."""
import ast
import json
import re
import subprocess
import sys

import pytest

from tests._review_tmfu2_2.test_review import ABSENT, GOOD, observe, read, runner, tmp_path, vs, vt


@pytest.mark.parametrize("shape", ["preparation-and-call", "second-call", "computed", "pool", "asyncio"])
def test_missing_import_provenance(tmp_path, shape):
    guard = f"try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n"
    if shape == "preparation-and-call":
        source = guard + "def run(value):\n" + "".join("    " + line + "\n" for line in guard.splitlines())
        source += "    return value\n"
        wanted = [[ABSENT], [ABSENT], [ABSENT]]
    elif shape == "second-call":
        source = ("count = 0\ndef run(value):\n    global count\n    count += 1\n    if count == 2:\n"
                  f"        try:\n            import {ABSENT}\n"
                  "        except ImportError:\n            pass\n    return value\n")
        wanted = [[], [ABSENT], []]
    else:
        expression = {"computed": f"importlib.import_module(''.join(['{ABSENT[:9]}', '{ABSENT[9:]}']))",
                      "pool": f"pool.submit(importlib.import_module, '{ABSENT}').result()",
                      "asyncio": f"loop.run_in_executor(None, importlib.import_module, '{ABSENT}')"}[shape]
        source = "import importlib\nimport concurrent.futures\nimport asyncio\ndef run(value):\n"
        if shape == "pool":
            source += "    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:\n"
            indent = "        "
        else:
            indent = "    "
        if shape == "asyncio":
            source += "    loop = asyncio.new_event_loop()\n"
            expression = "loop.run_until_complete(" + expression + ")"
        source += indent + "try:\n" + indent + "    " + expression + "\n"
        source += indent + "except ImportError:\n" + indent + "    pass\n"
        if shape == "asyncio":
            source += "    loop.close()\n"
        source += "    return value\n"
        wanted = [[ABSENT], [ABSENT], [ABSENT]]
    cases = [{"args": [i], "returns": i} for i in range(3)]
    found, _, _ = observe(tmp_path / "tree", source, cases=cases)
    assert found["result"] == "passed", found
    assert found["import_evidence"]["outcomes"] == wanted, found


@pytest.mark.parametrize("statement", ["import split.deep.deleted", "from split.deep import deleted",
                                       "import importlib; importlib.import_module('split.' + 'deep.deleted')"])
def test_deleted_namespace_across_roots(tmp_path, statement):
    files = {"one/split/deep/kept.py": "", "two/split/deep/other.py": ""}
    found, _, _ = observe(tmp_path / "tree", f"def run(value):\n    {statement}\n    return value\n",
                           files=files, deleted=("two/split/deep/deleted.py",), roots=("one", "two"))
    assert found["result"] == "failed", found


def test_same_name_module_readded(tmp_path):
    found, _, _ = observe(tmp_path / "tree", "def run(value):\n    from owned import renamed\n    return renamed\n",
                           files={"owned.py": "renamed = 1\n"}, deleted=("owned/renamed.py",))
    assert found["result"] == "passed", found


def test_same_absent_root_requested_under_a_submodule_name_is_not_a_finding(tmp_path):
    source = ("def run(value):\n    if value:\n        try:\n            import " + ABSENT +
              "\n        except ImportError:\n            return 0\n    return value\n")
    cases = [{"args": [0]}, {"args": [1]}]
    base, _, _ = observe(tmp_path / "base", source, template="differential.v1", cases=cases)
    patch, _, _ = observe(tmp_path / "patch", source.replace(ABSENT, ABSENT + ".child").replace("return 0", "return 2"),
                           template="differential.v1", cases=cases)
    assert vs.classify("preserve", read(patch), read(base))["result"] == vs.CHECK_ERROR


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_unavailable_fallback_is_never_admitted_as_a_pass(tmp_path, template):
    source = (f"def run(value):\n    try:\n        import {ABSENT}\n"
              f"    except ImportError:\n        import {ABSENT}_fallback\n    return value\n")
    base, _, _ = observe(tmp_path / "base", source, template=template)
    patch, _, _ = observe(tmp_path / "patch", source, template=template)
    assert vs.classify("preserve", read(patch), read(base))["result"] == vs.CHECK_ERROR


def test_three_cases_with_moving_marks_leave_only_the_shared_occurrence(tmp_path):
    cases = [{"args": [i]} for i in range(3)]
    source = ("import random\nmarked = random.randrange(3)\ndef run(value):\n    if value == marked:\n"
              f"        try:\n            import {ABSENT}\n"
              "        except ImportError:\n            pass\n    return 0\n")

    def select(label, wanted):
        for index in range(64):
            found, _, _ = observe(tmp_path / f"{label}{index}", source, template="differential.v1", cases=cases)
            if found["import_evidence"]["outcomes"][wanted]:
                return read(found)
        pytest.fail("could not select the requested random marked case")

    first, again = select("first", 0), select("again", 1)
    patch, _, _ = observe(tmp_path / "patch", GOOD.replace("return value", "return 1"),
                           template="differential.v1", cases=cases)
    verdict = vs.classify("preserve", read(patch), first)
    stable = vs._stable(verdict, first, again)
    assert stable["result"] == vs.BEHAVIOUR_CHANGED, stable
    assert [json.loads(one["input"])["args"] for one in stable["differences"]] == [[2]], stable


@pytest.mark.parametrize("template", ["bootstrap", "relation.v1", "access_rule.v1", "differential.v1"])
def test_nested_displays_cycles_and_exception_groups(template):
    source = runner.BOOTSTRAP if template == "bootstrap" else vt.REGISTRY[template].text.replace(vt.SLOT, "{}")
    shown = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "_shown")
    namespace = {"json": json, "re": re}
    exec(compile(ast.Module(body=[shown], type_ignores=[]), "<display>", "exec"), namespace)
    display = namespace["_shown"]
    cycle = ["segment at 0x10", b"bytes at 0x20"]
    cycle.append(cycle)
    text = display({"items": (cycle, object())})
    assert "segment at 0x10" in text and "bytes at 0x20" in text and "<object object>" in text, text
    value = "deep at 0x10"
    for _ in range(40):
        value = [value]
    assert "deep at 0x10" in display(value)
    assert "ExceptionGroup" in display(ExceptionGroup("group", [ValueError("message")]))


@pytest.mark.parametrize("raw", [b"", b"{", b'{"version":"observation.v2","outcomes":[],"errors":{}}'])
def test_malformed_observation_supplies_no_support(raw):
    assert runner.finish(0, raw, {"calls": [{"args": [1]}]})["result"] == "error"


def test_compacted_helper_at_the_source_boundary(tmp_path):
    found, data, tree = observe(tmp_path / "tree", GOOD)
    definition = vt.REGISTRY["example.v1"]
    data["own_modules"] = ["subject", *[f"module_number_{i:04d}" for i in range(372)]]
    data["cases"] = [{"args": [""], "kwargs": {}, "expect": "returns", "value": 0} for _ in range(vt.MAX_INPUTS)]
    empty = definition.text.replace(vt.SLOT, ascii(data))
    data["cases"][0]["args"][0] = "x" * (vs.MAX_SOURCE_BYTES - len(empty.encode("utf-8")))
    at_limit = vt.compile_text(definition, data)
    assert len(at_limit.encode("utf-8")) == vs.MAX_SOURCE_BYTES
    data["cases"][0]["args"][0] += "x"
    above = vt.compile_text(definition, data)
    assert isinstance(above, runner.ExecutableSource)
    assert len(above.encode("utf-8")) <= vs.MAX_SOURCE_BYTES
    assert runner.bindings(above)[1] == data
    path = tmp_path / "compiled.py"
    path.write_text(above + "\nassert BINDINGS['own_modules'][-1] == 'module_number_0371'\n", encoding="utf-8")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(path)], capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
