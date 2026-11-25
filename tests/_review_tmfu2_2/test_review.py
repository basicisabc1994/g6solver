"""Review 2: real isolated-interpreter observations, no host or confinement calls."""
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt

ABSENT = "review2_absent_external"
GOOD = "def run(value):\n    return value\n"


@pytest.fixture
def tmp_path(monkeypatch):
    folder = Path(__file__).parent / "disk" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    folder = folder.resolve()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    for name in ("TEMP", "TMP", "TMPDIR", "G6_HOME"):
        monkeypatch.setenv(name, str(folder))
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    return folder


def observe(folder, source, *, template="example.v1", cases=None, files=None, deleted=(), roots=()):
    sources = {"subject.py": source, **(files or {})}
    for rel, text in sources.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    tree = vt.Tree(folder, SimpleNamespace(files=tuple(deleted)), frozenset(sources),
                   frozenset({*sources, *deleted}), roots=tuple(roots))
    tree._texts.update({(name, vt.BASE): text for name, text in sources.items()})
    tree._texts.update({(name, vt.BASE): "" for name in deleted})
    definition = vt.REGISTRY[template]
    cases = cases or [{"args": [1], "returns": 1}]
    binding = {"function": "subject.run"}
    binding.update({"cases": cases} if template == "example.v1" else
                   {"mode": "outputs", "calls": [{"args": c["args"]} for c in cases]})
    data = definition.applies(tree, definition.bindings.model_validate(binding), vt.PATCH).data
    data = runner.located(folder, data)
    boot = folder.parent / (folder.name + "-boot.py")
    channel = folder.parent / (folder.name + "-frame.bin")
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_text(json.dumps(runner.request(data)), encoding="utf-8")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(folder), str(channel), "@channel"],
                         capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", errors="replace")
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    return found, data, tree


def wire(found):
    return {"g6_observation": found, "verdict": {"passed": "passed", "failed": "failed", "error": "not_run"}[
        found["result"]], "said": found["detail"], "source_before": "0" * 64, "seconds": 0.1}


def read(found):
    return vs.reading(wire(found), "test_g6_example.py::test_example")


@pytest.mark.parametrize("caught", [True, False], ids=["control-caught", "uncaught"])
def test_independent_confirmed_defect_survives_an_unavailable_call(tmp_path, caught):
    cases = [{"args": [0], "returns": 0}, {"args": [1], "returns": 1}]
    base, _, _ = observe(tmp_path / "base", GOOD, cases=cases)
    unavailable = (f"        try:\n            import {ABSENT}\n"
                   "        except ImportError:\n            return 1\n" if caught else
                   f"        import {ABSENT}\n")
    patch, _, _ = observe(tmp_path / "patch", "def run(value):\n    if value:\n" + unavailable +
                          "    return 99\n", cases=cases)
    verdict = vs.classify("preserve", read(patch), read(base))
    assert verdict["result"] == vs.COUNTEREXAMPLE, verdict


@pytest.mark.parametrize("cached", [False, True], ids=["control-independent", "cached-fallback"])
def test_previous_call_fallback_is_not_an_independent_defect(tmp_path, cached):
    cases = [{"args": [0], "returns": 0}, {"args": [1], "returns": 1}]
    base, _, _ = observe(tmp_path / "base", GOOD, cases=cases)
    source = ("available = True\ndef run(value):\n    global available\n    if value == 0:\n"
              f"        try:\n            import {ABSENT}\n"
              "        except ImportError:\n            available = False\n        return 0\n"
              + ("    return value if available else 99\n" if cached else "    return value\n"))
    patch, _, _ = observe(tmp_path / "patch", source, cases=cases)
    satisfied, _, _ = observe(tmp_path / "satisfied", source, cases=cases, files={ABSENT + ".py": ""})
    assert satisfied["result"] == "passed", satisfied
    verdict = vs.classify("preserve", read(patch), read(base))
    assert verdict["result"] == (vs.CHECK_ERROR if cached else vs.NOT_REFUTED), verdict


@pytest.mark.parametrize("wrapped", [True, False], ids=["control-repository-callback", "library-finalizer"])
def test_library_finalizer_after_return_keeps_import_evidence(tmp_path, wrapped):
    source = ("import weakref, importlib\nclass Item:\n    pass\n"
              f"def load():\n    return importlib.import_module('{ABSENT}')\n"
              "def run(value):\n    item = Item()\n"
              + ("    weakref.finalize(item, load)\n" if wrapped else
                 f"    weakref.finalize(item, importlib.import_module, '{ABSENT}')\n")
              + "    return value\n")
    found, _, _ = observe(tmp_path / "tree", source)
    assert found.get("import_evidence", {}).get("outcomes") == [[ABSENT]], found
    assert found.get("fallback_observation", {}).get("missing_imports") == [ABSENT], found


@pytest.mark.parametrize("fallback", [False, True], ids=["control", "fallback"])
def test_final_differential_finding_retains_fallback_qualification(tmp_path, monkeypatch, fallback):
    guard = f"try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n" if fallback else ""
    base, _, _ = observe(tmp_path / "base", guard + GOOD, template="differential.v1")
    patch, data, tree = observe(tmp_path / "patch", guard + GOOD.replace("return value", "return value + 1"),
                                template="differential.v1")
    instance = vt.TemplateInstance(id="one", template="differential.v1", requirement_id="R1", intent="preserve",
                                   bindings={"function": "subject.run", "mode": "outputs", "calls": [{"args": [1]}]})
    check, placed, origin = vt.compiled_check(tree.root, instance, data, [])
    monkeypatch.setattr(vs, "_run_check", lambda *a, at_base, **k: wire(base if at_base else patch))
    done = vs.search(SimpleNamespace(base_commit="a" * 40), [check], {"one": placed},
                     deadline=vs._clock() + 60, artifact="b" * 64, origins={"one": origin})
    [activity] = done.activities
    assert activity["result"] == vs.COUNTEREXAMPLE, activity
    if fallback:
        assert activity.get("fallback_observation", {}).get("missing_imports") == [ABSENT], activity
        assert "fallback was taken" in activity["detail"], activity


@pytest.mark.parametrize("duplicate", [False, True], ids=["control-distinct", "duplicate-input"])
@pytest.mark.parametrize("moving_output", [False, True], ids=["same-values", "other-call-changes"])
def test_repeat_comparability_belongs_to_the_call_occurrence(tmp_path, duplicate, moving_output):
    cases = [{"args": [0]}, {"args": [0 if duplicate else 1]}]
    source = ("import random\nmarked = random.getrandbits(1)\ncount = 0\n"
              "def run(value):\n    global count\n    count += 1\n    if count == 2 and marked:\n"
              f"        try:\n            import {ABSENT}\n"
              "        except ImportError:\n            pass\n"
              + ("    return int(count == 1 and marked)\n" if moving_output else "    return 0\n"))

    def select(label, wanted):
        for index in range(64):
            found, _, _ = observe(tmp_path / f"{label}{index}", source,
                                   template="differential.v1", cases=cases)
            marked = bool(found.get("import_evidence"))
            if marked == wanted:
                return read(found)
        pytest.fail("could not select the requested random missing-import evidence")

    first, again = select("first", False), select("again", True)
    patch, _, _ = observe(tmp_path / "patch", "count = 0\ndef run(value):\n    global count\n"
                           "    count += 1\n    return int(count == 2)\n",
                           template="differential.v1", cases=cases)
    verdict = vs.classify("preserve", read(patch), first)
    assert verdict["result"] == vs.BEHAVIOUR_CHANGED, verdict
    stable = vs._stable(verdict, first, again)
    assert stable["result"] == vs.CHECK_ERROR, stable
