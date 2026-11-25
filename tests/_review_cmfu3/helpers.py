"""Shared fixtures for review 3 of claude/change-map-fu (794f766b).

Everything runs real pytest through the shipped collection bootstrap on a real
disk. ``API.neighbour`` raises when it is called, so "1 passed" from the real
run proves the selected check never called it. No confinement is claimed: this
machine has none, which is why the reader is driven directly.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from g6.harness import patch_review
from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map import target

API = ("def changed():\n    return 1\ndef neighbour():\n"
       "    raise AssertionError('unexercised neighbour')\n    return changed()\n")
HONEST_API = API.replace("    raise AssertionError('unexercised neighbour')\n", "")
HONEST_TEST = "from api import neighbour\ndef test_call():\n    assert neighbour() == 2\n"
BODY = "from api import neighbour\ndef test_call():\n    neighbour()\n"
NODE = "test_api.py::test_call"
UNAVAILABLE = {"status": "uncertain", "reason": "collection unavailable"}
REVIEW_TREE = Path(__file__).resolve().parents[2]

# Code a repository can run inside the collection child once anything of its
# own executes there. It hands pytest a second function object whose code is
# the source body's own code object (so the unmodified producer reports the
# content identity G6 expects) but whose globals bind ``neighbour`` to a
# stand-in. The collection report is never touched.
TWIN = '''
def _twin():
    import types
    import pytest
    original = pytest.Function.from_parent.__func__
    def from_parent(cls, parent, **kw):
        if kw.get("name", "").split("[")[0] == "test_call":
            real = vars(parent.obj)["test_call"]
            kw["callobj"] = types.FunctionType(
                real.__code__, {**real.__globals__, "neighbour": lambda: None}, real.__name__)
        return original(cls, parent, **kw)
    pytest.Function.from_parent = classmethod(from_parent)
'''


def oracle(root, selection="test_api.py", collect_only=True, python=None, prefix=""):
    """The shipped bootstrap and reader. ``python``: the repository's interpreter (G6_REPO_PYTHON's place)."""
    own = root.parent / (root.name + "-reports")
    own.mkdir(exist_ok=True)
    counted = own / "counted.json"
    variables = own / "env.json"
    variables.write_text("{}", encoding="utf-8")
    args = [selection, "-q", "-p", "no:cacheprovider", "--confcutdir", str(root),
            "--rootdir", str(root), "--basetemp", str(own / "oracle-temp"),
            "--junitxml=" + str(own / "report.xml"), "--log-file=" + str(own / "pytest.log")]
    if collect_only:
        args.append("--collect-only")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("G6_ALLOW_UNCONFINED", None)
    boot = getattr(repo_tests, "_COLLECTION_BOOT", None)   # absent at the branch's base: its ordinary bootstrap
    command = [python or sys.executable, "-I", "-B", "-X", "utf8", "-c", prefix + (boot or repo_tests._BOOT),
               json.dumps([str(root)]), "0", "", "", str(counted), str(variables),
               str(own / "loaded.json"), str(repo_tests.LOADED_LIMIT), *([str(root)] if boot else []), *args]
    ran = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=60)
    answer = (repo_tests._read_collection(own / "counted.json.collection.json", ran.returncode, root)
              if boot else None)
    return ran, answer


def change(root, files, api=API, ignored=None):
    """A committed repository whose patch edits ``changed``; ``ignored`` files are written after the commit."""
    before = {"api.py": api, **files}
    delta = repository(root, before, {**before, "api.py": api.replace("return 1", "return 2")})
    for rel, data in (ignored or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            stale_cache(root, data["stale_cache_of"], data["source"], path)
        else:
            path.write_text(data, encoding="utf-8", newline="")
    return delta


def stale_cache(root, rel, source, target):
    """A bytecode cache whose recorded size and time are ``rel``'s on disk, holding ``source``'s code instead:
    what Python loads in place of the file while the cache's header still matches it."""
    import importlib._bootstrap_external as external

    path = root / rel
    held = path.stat()
    code = compile(source, str(path), "exec")
    target.write_bytes(bytes(external._code_to_timestamp_pyc(code, int(held.st_mtime), held.st_size)))


def cache_name(rel):
    path = Path(rel)
    return (path.parent / "__pycache__" / f"{path.stem}.{sys.implementation.cache_tag}.pyc").as_posix()


def _tail(ran):
    return (ran.stdout + ran.stderr).strip().splitlines()[-1:]


def scenario(spec):
    """One repository and one question, as plain data.

    The tests run it on 794f766b; ``before`` runs the same data on an earlier commit's ``change_map``,
    ``repo_tests`` and ``patch_review``. ``mode``:
    collected  real collection through the shipped bootstrap, the reader, then the map (default);
    static     collection unavailable (this machine's own case), one explicit selection;
    many       collection unavailable, several explicit selections;
    proposed   a proposed check, collected in ``repository_copy`` as ``repo_tests.collect`` does.
    """
    root = Path(spec["root"])
    delta = change(root, spec["files"], api=spec.get("api", API), ignored=spec.get("ignored"))
    selection = spec.get("selection", "test_api.py")
    mode = spec.get("mode", "collected")
    evidence = getattr(repo_tests, "_collection_evidence", None)   # absent before 794f766b
    deadline = time.monotonic() + 60
    if mode == "static":
        ran, _ = oracle(root, selection, collect_only=False)
        if spec.get("collector", True):
            record = patch_review._change_neighbourhood(delta, (selection,), (), (), deadline=deadline,
                                                        collector=lambda *a, **k: UNAVAILABLE)
        else:   # the branch's base: static patterns, no collector
            record = patch_review._change_neighbourhood(delta, (selection,), (), (), deadline=deadline)
        return {"exit": ran.returncode, "tail": _tail(ran), "sibling": target(record, "neighbour")}
    if mode == "many":
        started = time.monotonic()
        record = patch_review._change_neighbourhood(delta, tuple(spec["checks"]), (), (), deadline=deadline,
                                                    collector=lambda *a, **k: UNAVAILABLE)
        return {"seconds": round(time.monotonic() - started, 2), "targets": len(record["targets"]),
                "siblings": [t for t in record["targets"] if t["kind"] == "sibling"],
                "limits": [s for s in record["skipped"] if "limit" in s["reason"] or "budget" in s["reason"]]}
    if mode == "proposed":
        seen = {}

        def collector(repo, checks, *, roots=(), timeout_s=10, sources=None):
            # repo_tests.collect's own steps, without the confinement this machine cannot provide.
            with repo_tests.repository_copy(Path(repo), deadline=time.monotonic() + 30, before=sources) as copy:
                seen["authority"] = evidence(copy)[1] if evidence else None
                ran, answer = oracle(copy, checks[0])
                seen["exit"] = ran.returncode
                return answer

        record = patch_review._change_neighbourhood(delta, (), tuple(spec["proposed"]), (), deadline=deadline,
                                                    collector=collector)
        return {**seen, "sibling": target(record, "neighbour")}
    authority = evidence(root)[1] if evidence else None
    python, prefix = spec.get("python"), spec.get("prefix", "")
    saved = sys.modules.get("_pytest.assertion.rewrite")
    try:
        if spec.get("g6_without_pytest"):   # G6's own environment, which does not depend on pytest
            sys.modules["_pytest.assertion.rewrite"] = None
        run, answer = oracle(root, selection, python=python, prefix=prefix)
        record = patch_review._change_neighbourhood(delta, (selection,), (), (), deadline=deadline,
                                                    collector=lambda *a, **k: answer)
    finally:
        if saved is not None:
            sys.modules["_pytest.assertion.rewrite"] = saved
        else:
            sys.modules.pop("_pytest.assertion.rewrite", None)
    ran, _ = oracle(root, selection, collect_only=False, python=python, prefix=prefix)
    return {"authority": authority, "collect_exit": run.returncode, "answer": answer,
            "sibling": target(record, "neighbour"), "exit": ran.returncode, "tail": _tail(ran)}


def before(commit, spec, root):
    """The same scenario on ``commit``'s three modules, in a child process (``before.py``)."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REVIEW_TREE / "src"), str(REVIEW_TREE)]),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("G6_ALLOW_UNCONFINED", None)
    ran = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("before.py")), commit,
                          json.dumps({**spec, "root": str(root)})],
                         env=env, capture_output=True, text=True, timeout=180, cwd=str(REVIEW_TREE))
    assert ran.returncode == 0, ran.stdout[-2000:] + ran.stderr[-4000:]
    return json.loads(ran.stdout.strip().splitlines()[-1])


def show(value):
    return json.dumps(value, sort_keys=True)
