import json
import subprocess
import sys
import time

import pytest

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
    write(after)
    files = tuple(sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)))
    return Change(root, "HEAD", commit, "b" * 40, files,
                  {p: before[p] for p in files if p in before},
                  {p: after[p] for p in files if p in after}, files, ())


def mapped(root, before, after, checks=()):
    delta = repository(root, before, after)
    return patch_review._change_neighbourhood(delta, checks, (), (), deadline=time.monotonic() + 60)


def neighbour(record):
    return next(t for t in record["targets"] if t.get("symbol") == "neighbour")


def collect(root):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "--confcutdir", str(root), "--rootdir", str(root),
         "--basetemp", str(root / "pytest-temp"), "test_api.py"],
        cwd=root, capture_output=True, text=True)


def test_module_collision_does_not_silently_erase_changed_file(tmp_path):
    before = {"api.py": API, "src/api.py": "def target():\n    return 99\n"}
    record = mapped(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    assert any(t.get("symbol") == "neighbour" for t in record["targets"]) or any(
        "api.py" in s["check"] and "ambig" in s["reason"] for s in record["skipped"]
    ), json.dumps(record)


@pytest.mark.parametrize("source, exit_code, expected", [
    ("class Base:\n    def test_call(self):\n        neighbour()\n"
     "class TestBad(object, Base):\n    pass\n", 2, False),
    ("class object:\n    def test_call(self):\n        neighbour()\n"
     "class TestGood(object):\n    pass\n", 0, True),
], ids=["builtin-object-order", "local-object"])
def test_mro_preserves_object_semantics(tmp_path, source, exit_code, expected):
    source = "from api import neighbour\n" + source
    before = {"api.py": API, "test_api.py": source}
    record = mapped(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")},
                    ("test_api.py",))
    collected = collect(tmp_path)
    assert collected.returncode == exit_code, collected.stdout + collected.stderr
    if expected:
        assert "test_api.py::TestGood::test_call" in collected.stdout
    else:
        assert "TypeError" in collected.stdout and "MRO" in collected.stdout
    assert neighbour(record)["exercised"] is expected, json.dumps(record)


@pytest.mark.parametrize("parent", [
    "class Hidden:\n",
    "class TestOuter:\n    __test__ = False\n",
    "class TestOuter:\n    def __init__(self):\n        pass\n",
], ids=["non-test-parent", "disabled-parent", "constructor-parent"])
def test_nested_class_requires_collectible_parents(tmp_path, parent):
    source = ("from api import neighbour\ndef test_control():\n    pass\n" + parent
              + "    class TestNested:\n        def test_call(self):\n            neighbour()\n")
    before = {"api.py": API, "test_api.py": source}
    record = mapped(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")},
                    ("test_api.py",))
    collected = collect(tmp_path)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "test_api.py::test_control" in collected.stdout
    assert "::test_call" not in collected.stdout
    assert neighbour(record)["exercised"] is False, json.dumps(record)


@pytest.mark.parametrize("prefix", [
    b"# coding: latin-1 # caf\xe9\n",
    b"#!/caf\xe9/python\n# coding: latin-1\n",
    b"# caf\xe9\n# coding: latin-1\n",
], ids=["cookie-line", "shebang", "comment-before-cookie"])
@pytest.mark.parametrize("side", ["base", "patch"])
def test_python_valid_nonascii_cookie_headers_are_read(tmp_path, prefix, side):
    old_body = b"def target(x):\n    return x != 'caf\xe9'\n"
    new_body = b"def target(x):\n    return x == 'caf\xe9'\n"
    clean = b"# coding: latin-1\n"
    old = (prefix if side == "base" else clean) + old_body
    new = (prefix if side == "patch" else clean) + new_body
    for raw, expected in ((old, False), (new, True)):
        namespace = {}
        exec(compile(raw, "api.py", "exec"), namespace)
        assert namespace["target"]("caf\u00e9") is expected
    record = mapped(tmp_path, {"api.py": old}, {"api.py": new})
    assert any("'caf\u00e9'" in t.get("literals", []) for t in record["targets"]), json.dumps(record)
