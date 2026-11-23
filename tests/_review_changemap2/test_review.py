import json
import subprocess
import sys
import time

from g6.harness import patch_review
from g6.workforce.repo_tests import Change


API = "def target():\n    return 1\ndef neighbour():\n    return target()\n"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout.decode().strip()


def repository(root, before, after):
    def write(files):
        for rel, source in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source if isinstance(source, bytes) else source.encode("utf-8"))

    write(before)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Review", "-c", "user.email=review@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    commit = git(root, "rev-parse", "HEAD")
    for rel in before.keys() - after.keys():
        (root / rel).unlink()
    write(after)
    files = tuple(sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)))
    return Change(root, "HEAD", commit, "b" * 40, files,
                  {p: before[p] for p in files if p in before},
                  {p: after[p] for p in files if p in after}, files, ())


def mapped(root, before, after, checks=()):
    delta = repository(root, before, after)
    return patch_review._change_neighbourhood(delta, checks, (), (), deadline=time.monotonic() + 60)


def assert_pytest_collects(root, selection):
    filename, _, node = selection.partition("::")
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--import-mode=prepend", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(root.parent), "--confcutdir", str(root),
         str(root / filename) + "::" + node], cwd=root.parent.parent,
        capture_output=True, text=True,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert selection in collected.stdout, collected.stdout


def test_an_unrelated_selection_keeps_inherited_named_calls(tmp_path):
    test_source = """from api import neighbour
class Grand:
    def test_call(self):
        neighbour()
class Base(Grand):
    pass
class TestA(Base):
    def test_other(self):
        pass
class TestB(Base):
    pass
"""
    before = {"api.py": API, "test_api.py": test_source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = mapped(tmp_path, before, after,
                    ("test_api.py::TestA::test_other", "test_api.py::TestB::test_call"))
    assert_pytest_collects(tmp_path, "test_api.py::TestB::test_call")
    neighbour = next(t for t in record["targets"] if t.get("symbol") == "neighbour")
    assert neighbour["checks"] == ["test_api.py::TestB::test_call"], json.dumps(neighbour)


def test_latin1_literals_are_not_silently_replaced(tmp_path):
    before = {"api.py": b"# coding: latin-1\ndef target(x):\n    return False\n"}
    after = {"api.py": b"# coding: latin-1\ndef target(x):\n    return x == 'caf\xe9'\n"}
    namespace = {}
    exec(compile(after["api.py"], "api.py", "exec"), namespace)
    assert namespace["target"]("caf\u00e9") is True
    record = mapped(tmp_path, before, after)
    literals = [literal for t in record["targets"] for literal in t.get("literals", [])]
    assert "'caf\u00e9'" in literals or any("api.py" in s["check"] for s in record["skipped"]), json.dumps(record)


def test_deep_attribute_keeps_unrelated_file_advice(tmp_path):
    source = "def unrelated():\n    return external" + ".part" * 600 + "()\n"
    before = {"api.py": API, "deep.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = mapped(tmp_path, before, after)
    assert any(t.get("symbol") == "neighbour" for t in record["targets"]), json.dumps(record)


def test_nested_pytest_class_selection_keeps_named_call(tmp_path):
    test_source = """from api import neighbour
class TestOuter:
    class TestInner:
        def test_call(self):
            neighbour()
"""
    before = {"api.py": API, "test_api.py": test_source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    selection = "test_api.py::TestOuter::TestInner::test_call"
    record = mapped(tmp_path, before, after, (selection,))
    assert_pytest_collects(tmp_path, selection)
    neighbour = next(t for t in record["targets"] if t.get("symbol") == "neighbour")
    assert neighbour["checks"] == [selection], json.dumps(neighbour)
