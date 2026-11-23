import json

import pytest

from g6.core import component_process
from g6.harness import change_map, patch_review
from g6.mcp.review_reply import review
from g6.metaharness.jobs import Jobs
from tests._review_changemap2.test_review import API, mapped, repository


@pytest.mark.parametrize("source", [
    b"def broken(:\n", b"x = '\x00'\n", b"#\xff\ndef f():\n    return 1\n",
    ("def f():\n    return 0x" + "f" * 5000 + "\n").encode(),
    ("def f():\n    return " + "+".join(["1"] * 2000) + "\n").encode(),
    "# bad surrogate: \udcff\n",
])
def test_malformed_sources_do_not_escape(tmp_path, source):
    # Direct text API for a lone surrogate, real bytes/disk reader for the rest.
    if isinstance(source, str):
        from tests.harness.test_change_map import change
        before = {"api.py": API}
        after = {"api.py": API.replace("return 1", "return 2"), "bad.py": source}
        record = change_map.build(change(before, after), before, after)
    else:
        before = {"api.py": API, "bad.py": source}
        after = {**before, "api.py": API.replace("return 1", "return 2")}
        record = mapped(tmp_path, before, after)
    json.dumps(record, allow_nan=False)
    assert any(t.get("symbol") == "neighbour" for t in record["targets"]), json.dumps(record)


def test_full_review_outcome_survives_real_recursion_error(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    before = {"api.py": API, "deep.py": "def unrelated():\n    return external" + ".part" * 600 + "()\n"}
    repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    options = dict(task="Change target", requirements=[], search={"templates": False})
    with monkeypatch.context() as scoped:
        scoped.setattr(patch_review, "_change_neighbourhood", lambda *a, **kw: change_map.empty())
        baseline = review(tmp_path, **options)
    got = review(tmp_path, **options)
    assert baseline["ok"] and got["ok"], json.dumps(got)
    for field in ("outcome", "reasons", "requirements", "summary", "next_actions"):
        assert baseline["assessment"][field] == got["assessment"][field]
    assert baseline["gate"] == got["gate"]
    for field in ("ok", "completion_state", "reliability_label", "artifact_digest"):
        assert baseline[field] == got[field]
    assert got["assessment"]["completion"]["neighbourhood"]["skipped"]
    assert got["merge"]["status"] == baseline["merge"]["status"]
    json.dumps(got, allow_nan=False)
    jobs = Jobs()
    try:
        job = jobs.start(lambda: review(tmp_path, **options))["job_id"]
        jobs._jobs[job].result(timeout=30)
        observed = jobs.status(job, detail="full")
        assert observed["status"] == "completed", json.dumps(observed)
        assert observed["result"]["assessment"]["outcome"] == baseline["assessment"]["outcome"]
    finally:
        jobs.close()


def test_operator_stop_survives_advisory(tmp_path):
    delta = repository(tmp_path, {"api.py": API}, {"api.py": API.replace("return 1", "return 2")})
    with component_process.window(60, stop_requested=lambda: True):
        with pytest.raises(component_process.ExecutionInterrupted) as stopped:
            patch_review._change_neighbourhood(delta, (), (), (), deadline=1e20)
    assert stopped.value.stopped
