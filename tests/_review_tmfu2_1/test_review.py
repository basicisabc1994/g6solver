"""On-disk review probes. No confinement, provider, network, or repository pytest config."""
import ast
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


ABSENT = "review_absent_external"
GOOD = "def run(value):\n    return value\n"


@pytest.fixture
def tmp_path(monkeypatch):
    # pytest cannot remove/list this machine's .bt directory; keep real disk fixtures here instead.
    folder = Path(__file__).parent / "disk" / uuid.uuid4().hex[:10]
    folder.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(folder.resolve()))
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, str(folder.resolve()))
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    return folder.resolve()


def observe(folder, source, *, cases=None, template="example.v1", files=None, deleted=()):
    folder.mkdir(parents=True)
    sources = {"subject.py": source, **(files or {})}
    for rel, text in sources.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    base = frozenset()
    commit = "HEAD"
    if deleted:
        for rel in deleted:
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Review", "-c", "user.email=review@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "base fixture")):
            subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)
        commit = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        base = frozenset({*sources, *deleted})
        for rel in deleted:
            (folder / rel).unlink()
    tree = vt.Tree(folder, SimpleNamespace(files=tuple(deleted), base_commit=commit),
                   frozenset(sources), base)
    if deleted:
        # Same immutable blob cache as Tree.preload, read from the fixture's actual commit.
        for rel in ("subject.py", *deleted):
            tree._texts[(rel, vt.BASE)] = subprocess.run(
                ["git", "-C", str(folder), "show", commit + ":" + rel],
                check=True, capture_output=True, text=True,
            ).stdout
    definition = vt.REGISTRY[template]
    bindings = {"function": "subject.run"}
    if template == "example.v1":
        bindings["cases"] = cases or [{"args": [1], "returns": 1}]
    else:
        bindings.update(mode="outputs", calls=cases or [{"args": [1]}])
    data = definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data
    data = runner.located(folder, data)
    boot = folder.parent / (folder.name + "-bootstrap.py")
    channel = folder.parent / (folder.name + "-channel.bin")
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_text(json.dumps(runner.request(data)), encoding="utf-8")
    ran = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(boot), str(folder), str(channel), "@channel"],
        capture_output=True, timeout=20,
    )
    assert ran.returncode == 0, ran.stderr.decode("utf-8", errors="replace")
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    read = vs.reading({
        "verdict": {"passed": "passed", "failed": "failed", "error": "not_run"}[found["result"]],
        "said": found["detail"], "g6_observation": found,
        "source_before": "0" * 64, "seconds": 0.1,
    }, "test_g6_example.py::test_example")
    return read, found


@pytest.mark.parametrize("later_wrong", [False, True], ids=["control", "unavailable-hides-mismatch"])
def test_unmarked_confirmed_mismatch_survives_another_missing_import(tmp_path, later_wrong):
    cases = [{"args": [0], "returns": 0}, {"args": [1], "returns": 1}]
    base, _ = observe(tmp_path / "base", GOOD, cases=cases)
    patch_source = (
        "def run(value):\n"
        "    if value:\n"
        f"        try:\n            import {ABSENT}\n"
        "        except ImportError:\n            pass\n"
        f"        return {2 if later_wrong else 1}\n"
        "    return 99\n"
    )
    patch, found = observe(tmp_path / "patch", patch_source, cases=cases)
    assert found["import_evidence"]["outcomes"] == [[], [ABSENT]]
    verdict = vs.classify("preserve", patch, base)
    assert verdict["result"] == vs.COUNTEREXAMPLE, verdict


@pytest.mark.parametrize("regular", [True, False], ids=["control-regular", "namespace"])
def test_deleted_owned_namespace_submodule_is_a_code_failure(tmp_path, regular):
    files = {"owned/kept.py": ""}
    if regular:
        files["owned/__init__.py"] = ""
    source = "def run(value):\n    import owned.deleted\n    return value\n"
    _, found = observe(tmp_path / "patch", source, files=files, deleted=("owned/deleted.py",))
    assert found["result"] == "failed", found


@pytest.mark.parametrize("wrapper", [True, False], ids=["control-repository-frame", "library-callback"])
def test_repository_requested_import_in_worker_is_unavailable(tmp_path, wrapper):
    source = (
        "import concurrent.futures\nimport importlib\n"
        f"def load():\n    return importlib.import_module('{ABSENT}')\n"
        "def run(value):\n"
        "    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:\n"
        + ("        future = pool.submit(load)\n" if wrapper else
           f"        future = pool.submit(importlib.import_module, '{ABSENT}')\n")
        + "        try:\n            future.result()\n"
        "        except ImportError:\n            return 0\n"
        "    return value\n"
    )
    _, found = observe(tmp_path / "patch", source)
    assert found["result"] == "error", found
    assert found["import_evidence"]["outcomes"] == [[ABSENT]], found


@pytest.mark.parametrize("alias", [False, True], ids=["control-direct", "aliased-dynamic-import"])
def test_large_tree_keeps_deleted_dynamic_import_ownership(tmp_path, alias):
    # These are real files. Their names put the inventory above the 8192-byte pruning boundary.
    files = {f"module_number_{index:04d}.py": "" for index in range(600)}
    source = (
        "loader = __import__\n"
        "def run(value):\n"
        + ("    loader(''.join(['he', 'lpers']))\n" if alias else "    __import__('helpers')\n")
        + "    return value\n"
    )
    _, found = observe(tmp_path / "patch", source, files=files, deleted=("helpers.py",))
    assert found["result"] == "failed", found


@pytest.mark.parametrize("tainted_repeat", [False, True], ids=["control", "unavailable-repeat"])
def test_stability_does_not_keep_a_difference_the_repeat_could_not_compare(tmp_path, tainted_repeat):
    source = (
        "import random\nflip = random.getrandbits(1)\n"
        "def run(value):\n"
        "    if value:\n"
        f"        if flip:\n            try:\n                import {ABSENT}\n"
        "            except ImportError:\n                pass\n"
        "        return 0\n"
        "    return flip\n"
    )
    calls = [{"args": [0]}, {"args": [1]}]
    def choose(label, bit):
        for attempt in range(64):
            read, found = observe(tmp_path / f"{label}{attempt}", source,
                                  template="differential.v1", cases=calls)
            provisional = found.get("import_evidence", {}).get("reading", found)
            if provisional["observations"][0]["output"]["returned"] == bit:
                return read
        pytest.fail("could not select the requested random baseline outcome")
    first = choose("first", 0)
    again = choose("again", int(tainted_repeat))
    patch, _ = observe(tmp_path / "patch", GOOD, template="differential.v1", cases=calls)
    verdict = vs.classify("preserve", patch, first)
    assert verdict["result"] == vs.BEHAVIOUR_CHANGED and len(verdict["differences"]) == 1
    stable = vs._stable(verdict, first, again)
    assert stable["result"] == (vs.CHECK_ERROR if tainted_repeat else vs.BEHAVIOUR_CHANGED), stable


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_matching_missing_imports_retain_the_fallback_qualification(tmp_path, template):
    source = f"try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n" + GOOD
    base, _ = observe(tmp_path / "base", source, template=template)
    patch, _ = observe(tmp_path / "patch", source, template=template)
    verdict = vs.classify("preserve", patch, base)
    assert verdict["result"] == vs.NOT_REFUTED, verdict
    assert verdict.get("fallback_observation", {}).get("missing_imports") == [ABSENT], verdict


@pytest.mark.parametrize("count", [0, 371, 372], ids=["control", "below-boundary", "above-boundary"])
def test_maximal_bindings_compile_beside_unrelated_modules(tmp_path, count):
    paths = {"subject.py": GOOD, **{f"module_number_{i:04d}.py": "" for i in range(count)}}
    for rel, text in paths.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    tree = vt.Tree(tmp_path, SimpleNamespace(files=()), frozenset(paths), frozenset())
    bindings = {"function": "subject.run", "cases": [{"args": [""], "returns": 0}
                                                    for _ in range(vt.MAX_INPUTS)]}
    bindings["cases"][0]["args"][0] = "x" * (vt.MAX_BINDINGS_CHARS - len(json.dumps(bindings)))
    assert len(json.dumps(bindings)) == vt.MAX_BINDINGS_CHARS
    definition = vt.REGISTRY["example.v1"]
    data = definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data
    instance = vt.TemplateInstance(id="one", template="example.v1", requirement_id="R1",
                                   intent="preserve", bindings=bindings)
    size = len(vt.compile_text(definition, data).encode("utf-8"))
    assert size <= vs.MAX_SOURCE_BYTES, (size, vs.MAX_SOURCE_BYTES, len(json.dumps(data["own_modules"])))
    vt.compiled_check(tmp_path, instance, data, [])


@pytest.mark.parametrize("template", ["bootstrap", "relation.v1"])
@pytest.mark.parametrize("container", [False, True], ids=["control-text", "container-text"])
def test_display_retains_addresses_in_container_text(tmp_path, template, container):
    fixed = runner.BOOTSTRAP if template == "bootstrap" else vt.REGISTRY[template].text.replace(vt.SLOT, "{}")
    shown = next(node for node in ast.parse(fixed).body if isinstance(node, ast.FunctionDef) and node.name == "_shown")
    path = tmp_path / "display.py"
    path.write_text("import json, re, sys\n" + ast.unparse(shown)
                    + "\nprint(_shown(json.loads(sys.argv[1])))\n", encoding="utf-8")
    results = []
    for address in ("10", "20"):
        value = "segment at 0x" + address
        ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(path),
                              json.dumps([value] if container else value)],
                             capture_output=True, text=True, timeout=20, check=True)
        results.append(ran.stdout.strip())
    assert results[0] != results[1], results
