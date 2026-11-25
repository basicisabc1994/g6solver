"""Review 3 reproductions, using real isolated interpreters and only local disk."""
import json
import io
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt

ABSENT = "review3_absent_external"
GOOD = "def run(value):\n    return value\n"


@pytest.fixture
def tmp_path(monkeypatch):
    folder = (Path(__file__).parent / "disk" / uuid.uuid4().hex).resolve()
    folder.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    for name in ("TEMP", "TMP", "TMPDIR", "G6_HOME"):
        monkeypatch.setenv(name, str(folder))
    return folder


def observe(folder, source, *, cases=None, files=None, template="example.v1", module="subject"):
    sources = {module + ".py": source, **(files or {})}
    for rel, text in sources.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources), frozenset(sources))
    tree._texts.update({(name, vt.BASE): text for name, text in sources.items()})
    cases = cases or [{"args": [1], "returns": 1}]
    binding = {"function": module + ".run"}
    binding.update({"cases": cases} if template == "example.v1" else {
        "mode": "outputs", "calls": [{"args": c["args"]} for c in cases]})
    definition = vt.REGISTRY[template]
    data = runner.located(folder, definition.applies(
        tree, definition.bindings.model_validate(binding), vt.PATCH).data)
    boot, channel = folder.parent / (folder.name + "-boot.py"), folder.parent / (folder.name + "-frame")
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_text(json.dumps(runner.request(data)), encoding="utf-8")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(folder), str(channel), "@channel"],
                         capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", errors="replace")
    return runner.finish(ran.returncode, channel.read_bytes(), data), data, tree


def wire(found):
    return {"g6_observation": found, "verdict": {"passed": "passed", "failed": "failed", "error": "not_run"}[
        found["result"]], "said": found["detail"], "source_before": "0" * 64, "seconds": 0.1}


def read(found):
    return vs.reading(wire(found), "test_g6_example.py::test_example")


@pytest.mark.parametrize("ambient", [False, True], ids=["control-module-state", "environment-state"])
def test_fresh_load_must_not_reuse_fallback_state(tmp_path, ambient):
    cases = [{"args": [0], "returns": 0}, {"args": [1], "returns": 1}]
    setup = "import os\n" if ambient else "available = True\n"
    change = "os.environ['G6_REVIEW3_FALLBACK'] = '1'" if ambient else "available = False"
    available = "'G6_REVIEW3_FALLBACK' not in os.environ" if ambient else "available"
    source = (setup + "def run(value):\n    global available\n    if value == 0:\n"
              f"        try:\n            import {ABSENT}\n"
              f"        except ImportError:\n            {change}\n        return 0\n"
              f"    return value if {available} else 99\n")
    base, _, _ = observe(tmp_path / "base", GOOD, cases=cases)
    patch, _, _ = observe(tmp_path / "patch", source, cases=cases)
    satisfied, _, _ = observe(tmp_path / "satisfied", source, cases=cases, files={ABSENT + ".py": ""})
    assert satisfied["result"] == "passed", satisfied
    verdict = vs.classify("preserve", read(patch), read(base))
    assert verdict["result"] == vs.CHECK_ERROR, (verdict, patch.get("case_reruns"))


@pytest.mark.parametrize("route", ["direct", "library"], ids=["control-direct", "platform-library"])
def test_caught_platform_library_failure_keeps_provenance(tmp_path, monkeypatch, route):
    # pty is present on Windows but its required tty/termios dependency is not.
    assert sys.platform == "win32"
    requested = "termios" if route == "direct" else "pty"
    source = (f"def run(value):\n    try:\n        import {requested}\n"
              "    except ImportError:\n        return value\n    return 99\n")
    found, data, tree = observe(tmp_path / "tree", source)
    assert found["result"] == "passed", found
    satisfied, _, _ = observe(tmp_path / "satisfied", source, files={requested + ".py": ""})
    assert satisfied["result"] == "failed", satisfied
    base, _, _ = observe(tmp_path / "base", GOOD)
    activity, completion = search_result(monkeypatch, base, found, data, tree)
    assert activity["result"] == vs.NOT_REFUTED, activity
    assert activity.get("fallback_observation"), (activity, completion)


@pytest.mark.parametrize("module", ["subject", "importlib"], ids=["control-ordinary", "stdlib-namesake"])
@pytest.mark.parametrize("translated", [False, True], ids=["returned-fallback", "translated-error"])
def test_repository_module_name_does_not_remove_import_evidence(tmp_path, module, translated):
    fallback = "raise RuntimeError('install the dependency')" if translated else "return value"
    cases = [{"args": [1], "raises": "RuntimeError"}] if translated else None
    source = (f"def run(value):\n    try:\n        import {ABSENT}\n"
              f"    except ImportError:\n        {fallback}\n    return 99\n")
    found, _, _ = observe(tmp_path / "tree", source, module=module, cases=cases)
    satisfied, _, _ = observe(tmp_path / "satisfied", source, module=module, cases=cases,
                              files={ABSENT + ".py": ""})
    assert satisfied["result"] == "failed", satisfied
    assert found["result"] == "passed", found
    assert found.get("fallback_observation"), found


@pytest.mark.parametrize("alternate_case", [False, True], ids=["control-same-case", "case-alias"])
def test_fresh_load_evicts_owned_files_with_case_aliases(tmp_path, alternate_case):
    cases = [{"args": [0], "returns": 0}, {"args": [1], "returns": 1}]
    source = ("import os, sys\nfolder = os.path.join(os.path.dirname(__file__), 'assets')\n"
              + ("folder = os.path.normcase(folder)\n" if alternate_case else "")
              + "sys.path.insert(0, folder)\nimport state\ndef run(value):\n    if value == 0:\n"
              f"        try:\n            import {ABSENT}\n"
              "        except ImportError:\n            state.available = False\n        return 0\n"
              "    return value if state.available else 99\n")
    base, _, _ = observe(tmp_path / "base", GOOD, cases=cases)
    patch, _, _ = observe(tmp_path / "patch", source, cases=cases,
                           files={"assets/state.py": "available = True\n"})
    satisfied, _, _ = observe(tmp_path / "satisfied", source, cases=cases,
                              files={"assets/state.py": "available = True\n", ABSENT + ".py": ""})
    assert satisfied["result"] == "passed", satisfied
    verdict = vs.classify("preserve", read(patch), read(base))
    assert verdict["result"] == vs.CHECK_ERROR, (verdict, patch.get("case_reruns"))


def search_result(monkeypatch, base, patch, data, tree):
    # Only substitute the executor: comparisons and report assembly consume real disk observations.
    instance = vt.TemplateInstance(id="one", template="example.v1", requirement_id="R1", intent="preserve",
        bindings={"function": "subject.run", "cases": [{"args": [1], "returns": 1}]})
    check, placed, origin = vt.compiled_check(tree.root, instance, data, [])
    monkeypatch.setattr(vs, "_run_check", lambda *a, at_base, **k: wire(base if at_base else patch))
    done = vs.search(SimpleNamespace(base_commit="a" * 40), [check], {"one": placed},
                     deadline=vs._clock() + 60, artifact="b" * 64, origins={"one": origin})
    return done.activities[0], vs.completion([], [], [], [], [], done, artifact="b" * 64, base_commit="a" * 40)


@pytest.mark.parametrize("route", ["stored-callback", "generator", "context-exit", "finalizer"])
def test_repository_callback_boundaries_keep_marks(tmp_path, route):
    guard = f"    try:\n        import {ABSENT}\n    except ImportError:\n        pass\n"
    sources = {
        "stored-callback": ("import contextlib\ndef callback():\n" + guard +
            "def run(value):\n    with contextlib.ExitStack() as stack:\n"
            "        stack.callback(callback)\n    return value\n"),
        "generator": ("import collections\ndef generate(value):\n" + guard +
            "    yield value\ndef run(value):\n    return collections.deque(generate(value))[0]\n"),
        "context-exit": ("import contextlib\n@contextlib.contextmanager\ndef managed():\n"
            "    yield\n" + guard + "def run(value):\n    with managed():\n        pass\n    return value\n"),
        "finalizer": ("import weakref\nclass Item:\n    pass\ndef callback():\n" + guard +
            "def run(value):\n    item = Item()\n    weakref.finalize(item, callback)\n    return value\n"),
    }
    found, _, _ = observe(tmp_path / route, sources[route])
    assert found["result"] == "passed", found
    assert found["import_evidence"]["outcomes"] == [[ABSENT]], found


@pytest.mark.parametrize("archive", [False, True], ids=["control-directory", "zip"])
def test_own_regular_package_missing_child_is_a_failure(tmp_path, archive):
    source = ("import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor.zip'))\n"
              "def run(value):\n    import held.deleted\n    return value\n")
    entries = {"held/__init__.py": "", "held/kept.py": ""}

    def files(complete):
        members = {**entries, **({"held/deleted.py": ""} if complete else {})}
        if not archive:
            return members
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            for name, text in members.items():
                zipped.writestr(name, text)
        return {"vendor.zip": stream.getvalue()}

    base, _, _ = observe(tmp_path / "base", source, files=files(True))
    patch, _, _ = observe(tmp_path / "patch", source, files=files(False))
    assert base["result"] == "passed", base
    assert patch["result"] == "failed", patch
