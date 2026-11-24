"""Shared helpers for review 2 of claude/template-methods-fu (tip 970da2d3), from review 1's tmfu1_support: reproductions on a real disk.

No test needs a confined run: G6's fixed bootstrap is started directly in a fresh interpreter (``-I -S -B``, as
``behaviour_runner.run`` starts it), and the compiled pytest checks are loaded as pytest loads them from a copy's top
folder. No model, network or host program is used. The helpers follow the parent's fourth review
(``tests/_review_templatemethods4``), so a reading here is made the way that review's readings were.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from _pytest._code import ExceptionInfo

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt
from g6.workforce import repo_tests

EXAMPLE_NODE = "test_g6_example.py::test_example"
RUNS = "def run(value):\n    return value\n"


def disk_for(nodeid: str) -> Path:
    folder = Path(__file__).parent / "disk" / hashlib.sha256(nodeid.encode()).hexdigest()[:12]
    if folder.exists():
        import shutil
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def applied(folder, template, module, source, bindings):
    """The template's resolved literal for ``bindings`` against ``source`` written to ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (module + ".py")).write_text(source, encoding="utf-8")
    tree = vt.Tree(folder, None, frozenset({module + ".py"}), frozenset())
    definition = vt.REGISTRY[template]
    return definition, definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data


def observed(folder, data, module=runner):
    """G6's own runner's reading: the fixed bootstrap in a fresh interpreter, then ``finish`` in this process."""
    data = module.located(folder, data)
    own = folder.parent / (folder.name + "-g6")   # G6's folder for its bootstrap and channel, beside the tree
    own.mkdir(parents=True, exist_ok=True)
    boot, channel = own / "bootstrap.py", own / "result.bin"
    boot.write_text(module.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(folder), str(channel),
                          json.dumps(module.request(data))], capture_output=True, timeout=60)
    return module.finish(ran.returncode, channel.read_bytes(), data), ran


def read(found):
    """The record ``behaviour_runner.run`` returns for ``found``, as verification search reads it."""
    verdict = {"passed": "passed", "failed": "failed", "error": "not_run"}[found["result"]]
    return vs.reading({"verdict": verdict, "said": found["detail"], "g6_observation": found,
                       "source_before": "0" * 64, "seconds": 0.1}, EXAMPLE_NODE)


def runner_bindings(template, function="subject.run"):
    return ({"function": function, "cases": [{"args": [1], "returns": 1}]} if template == "example.v1" else
            {"function": function, "mode": "outputs", "calls": [{"args": [1]}]})


def parent_runner(commit="86204ecf"):
    """``behaviour_runner`` as the change's base commit holds it, loaded beside the tree's own (read with git show)."""
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    text = subprocess.run(["git", "-C", str(root), "show", commit + ":src/g6/harness/behaviour_runner.py"],
                          capture_output=True, timeout=60, check=True).stdout
    path = Path(__file__).parent / "disk" / ("behaviour_runner_" + commit + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    spec = importlib.util.spec_from_file_location("tmfu1_parent_behaviour_runner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def runner_reading(folder, template, stated, tree_source, extra=None, module=runner):
    """``template`` compiled against the ``stated`` module, observed by G6's runner on ``tree_source``; ``extra``
    maps further relative paths of the tree to their text."""
    _, data = applied(folder, template, "subject", stated, runner_bindings(template))
    (folder / "subject.py").write_text(tree_source, encoding="utf-8")
    for rel, text in (extra or {}).items():
        path = folder.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    found, ran = observed(folder, data, module)
    return read(found), found, ran


def pytest_run(node, message=None, check_errors=None):
    """One recorded pytest run in which ``node`` passed, or failed its call with ``message``; with the producer's
    ``check_errors`` field, as every run of this tip records it."""
    failed = message is not None
    return {"verdict": "failed" if failed else "passed", "said": "", "passed": 0 if failed else 1,
            "failed": 1 if failed else 0, "not_passed": 0, "total": 1, "deselected": 0,
            "test_outcomes": {node: {"setup": "passed", "call": "failed" if failed else "passed",
                                     "teardown": "passed"}},
            "collected_ids": [node], "omitted_ids": [], "changed": [], "output": "", "probe_observations": None,
            "seconds": 0.25, "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": [node] if failed else [],
            "setup_errors": {}, "degraded": False, "receipt": "", "rootdir": "",
            "check_errors": dict(check_errors or {}),
            **({"failure_messages": {node: message}} if failed else {})}


def crash_message(exc):
    """What G6's pytest plugin records for a failed call: the crash's message (``reprcrash.message``), its tail."""
    return ExceptionInfo.from_exception(exc).exconly(tryshort=True)[-2000:]


def produced(folder, node, exc):
    """The run G6's own pytest plugin records when ``node``'s call ends in ``exc``: its real exception hook decides
    ``check_errors`` (by the class the generated check registered), and its crash message is the failure's text."""
    source = repo_tests._BOOT.split("class _Counted:", 1)[1].split("class _Capture:", 1)[0]
    namespace = {"json": json, "sys": sys, "counted": str(folder / "counted.json")}
    exec("class _Counted:" + source, namespace)
    counted = namespace["_Counted"]()
    counted.pytest_exception_interact(None, SimpleNamespace(excinfo=SimpleNamespace(value=exc)),
                                     SimpleNamespace(nodeid=node))
    return pytest_run(node, crash_message(exc), counted.check_errors)


def real_pytest(folder, template, module, stated, tree_source, bindings, data=None, extra=None):
    """A real pytest process on the compiled check, in a copy that holds ``tree_source``, with G6's own ``_Counted``
    plugin (the class text of ``repo_tests._BOOT``) writing its record: the run as ``verification_search.reading``
    takes it, the plugin's raw record, and the process. Nothing above the copy is read (no ini file, no conftest)."""
    definition = vt.REGISTRY[template]
    if data is None:
        definition, data = applied(folder, template, module, stated, bindings)
    (folder / (module + ".py")).write_text(tree_source, encoding="utf-8")
    for rel, text in (extra or {}).items():
        path = folder.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    name = "test_g6_" + template.split(".")[0] + ".py"
    (folder / name).write_text(vt.compile_text(definition, data), encoding="utf-8")
    own = folder.parent / (folder.name + "-g6")
    own.mkdir(parents=True, exist_ok=True)
    (own / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    counted = own / "counted.json"
    source = repo_tests._BOOT.split("class _Counted:", 1)[1].split("class _Capture:", 1)[0]
    (own / "boot.py").write_text(
        "import json, os, sys\n"
        "sys._g6_check_setup_types = []\n"   # as repo_tests' boot does before pytest
        "import pytest\n"
        "counted, folder, name, ini = sys.argv[1:5]\n"
        "class _Counted:" + source + "\n"
        "raise SystemExit(pytest.main([name, '-q', '-p', 'no:cacheprovider', '--rootdir', folder, '-c', ini,\n"
        "                              '--confcutdir', folder], plugins=[_Counted()]))\n", encoding="utf-8")
    ran = subprocess.run([sys.executable, "-B", str(own / "boot.py"), str(counted), str(folder), name,
                          str(own / "pytest.ini")], cwd=folder, capture_output=True, timeout=120)
    record = json.loads(counted.read_text(encoding="utf-8")) if counted.is_file() else {}
    outcomes = record.get("test_outcomes", {})
    failed = record.get("failed_ids", [])
    passed = [node for node, phases in outcomes.items() if phases.get("call") == "passed"]
    run = {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
           "not_passed": 0, "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes,
           "collected_ids": record.get("collected_ids", []), "omitted_ids": [], "changed": [], "output": "",
           "probe_observations": None, "seconds": 0.25, "python": sys.executable, "interpreter_source": "g6",
           "command": "pytest", "isolation": {"backend": "synthetic-unit-fixture", "why": ""},
           "failed_ids": failed, "setup_errors": record.get("setup_errors", {}), "degraded": False, "receipt": "",
           "rootdir": "", "check_errors": record.get("check_errors", {}),
           **({"failure_messages": record.get("messages", {})} if failed else {})}
    return run, record, ran


def legacy(folder, monkeypatch, template, module, stated, tree_source, bindings, data=None, extra=None):
    """The compiled pytest check, resolved against ``stated`` and loaded from a copy that holds ``tree_source``."""
    definition = vt.REGISTRY[template]
    if data is None:
        definition, data = applied(folder, template, module, stated, bindings)
    (folder / (module + ".py")).write_text(tree_source, encoding="utf-8")
    for rel, text in (extra or {}).items():
        path = folder.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys, "_g6_check_setup_types", [], raising=False)   # as repo_tests' boot does before pytest
    monkeypatch.chdir(folder)
    monkeypatch.syspath_prepend(str(folder))
    for name in list(sys.modules):
        if name == module or name.startswith(module + "."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    namespace = {"__name__": "test_g6_" + template.split(".")[0]}
    exec(vt.compile_text(definition, data), namespace)
    return namespace, data


def free_form_pytest(folder, rel, check_source, tree=None):
    """Review 2: a real pytest process on a caller's own (free-form) test file ``rel`` in a copy that holds ``tree``
    (relative path to text), with G6's own ``_Counted`` plugin writing its record, as ``real_pytest`` runs a compiled
    check: the run as ``verification_search.reading`` takes it, the plugin's raw record, and the process."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in {**(tree or {}), rel: check_source}.items():
        path = folder.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    own = folder.parent / (folder.name + "-g6")
    own.mkdir(parents=True, exist_ok=True)
    (own / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    counted = own / "counted.json"
    source = repo_tests._BOOT.split("class _Counted:", 1)[1].split("class _Capture:", 1)[0]
    (own / "boot.py").write_text(
        "import json, os, sys\n"
        "sys._g6_check_setup_types = []\n"
        "import pytest\n"
        "counted, folder, name, ini = sys.argv[1:5]\n"
        "class _Counted:" + source + "\n"
        "raise SystemExit(pytest.main([name, '-q', '-p', 'no:cacheprovider', '--rootdir', folder, '-c', ini,\n"
        "                              '--confcutdir', folder], plugins=[_Counted()]))\n", encoding="utf-8")
    ran = subprocess.run([sys.executable, "-B", str(own / "boot.py"), str(counted), str(folder), rel,
                          str(own / "pytest.ini")], cwd=folder, capture_output=True, timeout=120)
    record = json.loads(counted.read_text(encoding="utf-8")) if counted.is_file() else {}
    outcomes = record.get("test_outcomes", {})
    failed = record.get("failed_ids", [])
    passed = [node for node, phases in outcomes.items() if phases.get("call") == "passed"]
    run = {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
           "not_passed": 0, "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes,
           "collected_ids": record.get("collected_ids", []), "omitted_ids": [], "changed": [], "output": "",
           "probe_observations": None, "seconds": 0.25, "python": sys.executable, "interpreter_source": "g6",
           "command": "pytest", "isolation": {"backend": "synthetic-unit-fixture", "why": ""},
           "failed_ids": failed, "setup_errors": record.get("setup_errors", {}), "degraded": False, "receipt": "",
           "rootdir": "", "check_errors": record.get("check_errors", {}),
           **({"failure_messages": record.get("messages", {})} if failed else {})}
    return run, record, ran
