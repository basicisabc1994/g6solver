import subprocess
import time

import pytest

from g6.harness import change_map, patch_review
from g6.workforce.repo_tests import Change


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout.decode().strip()


def repository(tmp_path, before, after):
    for rel, text in before.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Review", "-c", "user.email=review@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    commit = git(tmp_path, "rev-parse", "HEAD")
    for rel, text in after.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    files = tuple(sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)))
    return Change(tmp_path, "HEAD", commit, "b" * 40, files,
                  {p: before[p] for p in files if p in before},
                  {p: after[p] for p in files if p in after}, files, ())


def mapped(tmp_path, before, after, checks=()):
    delta = repository(tmp_path, before, after)
    return patch_review._change_neighbourhood(
        delta, checks, (), (), deadline=time.monotonic() + 60
    )


def test_analysis_obeys_its_two_second_budget(tmp_path):
    before = {"api.py": "def accepts(x):\n    return False\n"}
    source = "def accepts(x):\n    return x in (" + ",".join(map(str, range(18000))) + ")\n"
    assert len(source) < change_map.MAX_FILE_CHARS
    delta = repository(tmp_path, before, {"api.py": source})
    started = time.monotonic()
    patch_review._change_neighbourhood(delta, (), (), (), deadline=started + 60)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"two-second advisory analysis took {elapsed:.2f}s"


def test_conditional_definition_change_is_not_silently_overwritten(tmp_path):
    before = {"api.py": """import sys
if sys.platform == 'win32':
    def target():
        return 1
else:
    def target():
        return 2
def caller():
    return target()
"""}
    after = {"api.py": before["api.py"].replace("return 1", "return 3")}
    record = mapped(tmp_path, before, after)
    assert any(t.get("symbol") == "caller" for t in record["targets"]) or any(
        "target" in s["check"] and "ambig" in s["reason"] for s in record["skipped"]
    ), record


def test_loop_binding_does_not_invent_a_named_call(tmp_path):
    before = {"api.py": """def target():
    return lambda: 7
def neighbour():
    return target()
""", "test_api.py": """from api import neighbour
def test_other():
    for neighbour in [lambda: 7]:
        neighbour()
"""}
    after = {**before, "api.py": before["api.py"].replace("lambda: 7", "lambda: 8")}
    record = mapped(tmp_path, before, after, ("test_api.py::test_other",))
    neighbour = next(t for t in record["targets"] if t.get("symbol") == "neighbour")
    assert neighbour["exercised"] is False, neighbour


def test_nested_function_caller_is_not_silently_lost(tmp_path):
    before = {"api.py": """def outer():
    def target():
        return 1
    def caller():
        return target()
    return caller
"""}
    after = {"api.py": before["api.py"].replace("return 1", "return 2")}
    record = mapped(tmp_path, before, after)
    assert any(t.get("symbol") == "outer.caller" for t in record["targets"]) or any(
        "outer.caller" in s["check"] for s in record["skipped"]
    ), record


def test_class_check_selection_includes_its_named_test_methods(tmp_path):
    before = {"api.py": """def target():
    return 1
def neighbour():
    return target()
""", "test_api.py": """from api import neighbour
class TestAPI:
    def test_call(self):
        neighbour()
"""}
    after = {**before, "api.py": before["api.py"].replace("return 1", "return 2")}
    record = mapped(tmp_path, before, after, ("test_api.py::TestAPI",))
    neighbour = next(t for t in record["targets"] if t.get("symbol") == "neighbour")
    assert neighbour["exercised"] is True, neighbour


@pytest.mark.parametrize("statement", ["return 0", "raise ValueError('stop')"])
def test_added_unconditional_control_exit_is_a_decision(tmp_path, statement):
    before = {"api.py": "def target(x):\n    work(x)\n"}
    after = {"api.py": "def target(x):\n    " + statement + "\n    work(x)\n"}
    record = mapped(tmp_path, before, after)
    assert any(t.get("kind") == "decision_point" and t["line"] == 2 for t in record["targets"]), record


def test_infinite_source_literal_does_not_fail_the_review_job(tmp_path, monkeypatch):
    from g6.metaharness.jobs import Jobs
    from g6.mcp.review_reply import review

    repository(tmp_path, {"api.py": "def accepts(x):\n    return False\n"},
               {"api.py": "def accepts(x):\n    return x < 1e309\n"})
    options = dict(task="Change the acceptance bound", requirements=[], search={"templates": False})
    real = patch_review._change_neighbourhood
    jobs = Jobs()
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: change_map.empty())
            baseline_id = jobs.start(lambda: review(tmp_path, **options))["job_id"]
            jobs._jobs[baseline_id].result(timeout=30)
            baseline = jobs.status(baseline_id, detail="full")
        assert baseline["status"] == "completed", baseline
        assert baseline["result"]["ok"] is True, baseline
        assert patch_review._change_neighbourhood is real
        job_id = jobs.start(lambda: review(tmp_path, **options))["job_id"]
        jobs._jobs[job_id].result(timeout=30)
        got = jobs.status(job_id, detail="full")
        assert got["status"] == "completed", got
        assert got["result"]["assessment"]["outcome"] == baseline["result"]["assessment"]["outcome"]
    finally:
        jobs.close()
