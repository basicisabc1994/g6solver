# SPDX-License-Identifier: Apache-2.0
"""Before a moment session starts, its G6 home is asked, through G6's own reader and G6's own server, whether a
review's reply would come back: the session's copy of the person's decisions must share every source the task needs,
and a review taken in a copy of that home must come back (or, for a task that withholds sources, be withheld on them).
Otherwise the session is refused and nothing starts. No client is started and no model is called: the probe's review
runs in this process, with model calls switched off, and a fake stands where ``CliAgentRunner`` is."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", HERE / "score.py")
run = _load("g6_validation_routing_run", HERE / "run.py")
server = _load("g6_validation_routing_server", HERE / "server.py")
PROVIDER = "anthropic:claude_code"
QUIET = "\n".join(json.dumps(e) for e in ({"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
                                          {"type": "turn.completed"}))


def _grant(source: str, provider: str = PROVIDER) -> None:
    from g6.core import cloud_consent as cloud

    assert cloud._grant(source, provider, cloud.preview(source, provider)[1], "the test's stand-in person") == "granted"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit",
                                                "-qm", "base"]):
        subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)
    return repo


def test_a_reviews_reply_is_withheld_at_its_poll_while_the_review_calls_own_arguments_are_unshared(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pilot's finding, held offline with G6's own server: every named source of a review shared, its job poll
    is still withheld, on one source alone: the review call's own arguments (``input:``), which no grant made before
    the call can name. Granting that exact input lets the reply come back."""
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    repo = _repo(tmp_path)
    reviews = cloud.repository_source(tmp_path / "home" / "reviews")    # where the home keeps a saved review
    for source in ("agent_environment", "host_configuration", "jobs", "completion_history",
                   cloud.repository_source(repo), reviews):
        _grant(source)
    found = server.probe("g6", str(repo), "claude_code")
    review = cloud.input_source(json.dumps({"repo": str(repo), "task": server.PROBE_TASK}, sort_keys=True))
    assert found["reply"] == "withheld" and found["provider"] == PROVIDER
    assert found["review_input"] == review and found["unshared"] == [review]
    assert {"jobs", "host_configuration", cloud.repository_source(repo)} <= set(found["named"])
    _grant(review)
    assert server.probe("g6", str(repo), "claude_code")["reply"] == "returned"


def test_the_probe_names_a_named_source_its_home_does_not_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    repo = _repo(tmp_path)
    for source in ("agent_environment", "host_configuration", "completion_history", cloud.repository_source(repo)):
        _grant(source)
    found = server.probe("g6", str(repo), "claude_code")
    assert found["reply"] == "withheld" and "jobs" in found["unshared"]


# --- the pre-start check in a run --------------------------------------------------------------------------------

KEPT = ("moment.before_done.1", "moment.after_refusal.3")


def _fake(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from g6.agents._runtime import cli

    seen: list[str] = []

    class FakeCli(cli.CliAgentRunner):
        def locate(self) -> str:
            return self.program.command

        def require(self, *, stop: Any = None) -> Any:
            return SimpleNamespace(found=True, detail=f"{self.program.command} 0.0.0")

        def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path,
                stop: Any = None) -> Any:
            seen.append(prompt)
            (scratch / cli.OUTPUT_LOG).write_text(QUIET, encoding="utf-8")
            return cli.CliRun(argv=tuple(argv), exit_code=0, output_tail="", output_bytes=0, elapsed_s=3.0,
                              cleanup_confirmed=True, env_names=())

    monkeypatch.setattr(cli, "CliAgentRunner", FakeCli)
    return seen


@pytest.fixture()
def granted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A non-consent task and a consent-refusal task, every line ``sources`` lists granted by a stand-in person."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(run, "user_home", lambda: tmp_path / "person")
    tasks = run.load_moment()
    path = tmp_path / "two.json"
    path.write_text(json.dumps(dict(tasks, tasks=[t for t in tasks["tasks"] if t["id"] in KEPT])), encoding="utf-8")
    from g6.core import cloud_consent as cloud

    for out in ("before", "again"):
        for row in run.sources(run.load_moment(path), tmp_path / out, "codex", "moment"):
            if not cloud.preview(row["source"], row["provider"])[0]:
                _grant(row["source"], row["provider"])
    by_prompt = {task["prompt"]: task["id"] for task in tasks["tasks"]}
    return SimpleNamespace(path=path, tmp=tmp_path, by_prompt=by_prompt)


def stand_in_probe(home: Path, workspace: Path, client: str) -> dict[str, Any]:
    """The probe's answer as the server would give it once a review's reply can come back: withheld on the three
    sources for a task that withholds them, returned otherwise."""
    three = ["completion_history", "host_configuration", "jobs"] if "after_refusal.3" in str(home) else []
    return {"reply": "withheld" if three else "returned", "code": "", "named": three, "unshared": three}


def _rows(out: Path) -> dict[str, dict[str, Any]]:
    return {r["task_id"]: r for r in map(json.loads, (out / "transcripts-codex-moment.jsonl").read_text(
        encoding="utf-8").splitlines())}


def test_a_session_whose_reviews_reply_would_be_withheld_is_refused_before_it_starts(
        granted: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """With all 28 lines granted, the server as it stands still withholds a review's reply (the call's own
    arguments): a task that starts with the granted sources is refused; the consent task, whose reply must be
    withheld, starts."""
    seen = _fake(monkeypatch)
    out = granted.tmp / "before"
    done = {t.task_id: t for t in run.run(granted.path, out, client="codex", cohort="moment")}
    assert (done["moment.before_done.1"].state, done["moment.before_done.1"].code) == ("refused", run.REPLY_WITHHELD)
    assert done["moment.after_refusal.3"].state == "completed", _rows(out)["moment.after_refusal.3"]["session"].get("fault")
    assert [granted.by_prompt[p] for p in seen] == ["moment.after_refusal.3"]
    rows = _rows(out)
    probe = rows["moment.before_done.1"]["session"]["reply_probe"]
    assert probe["reply"] == "withheld" and len(probe["unshared"]) == 1 and probe["unshared"][0].startswith("input:")
    consent = rows["moment.after_refusal.3"]["session"]["reply_probe"]
    assert consent["reply"] == "withheld" and {"host_configuration", "jobs"} <= set(consent["unshared"])
    readme = " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
    assert run.REPLY_WITHHELD in readme and "server.probe" in readme


def test_a_session_whose_copy_lacks_a_source_the_task_needs_is_refused_before_it_starts(
        granted: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """The person's decisions share everything, but the session's own copy (what its servers read) lacks jobs: G6's
    own reader on the session's home refuses the session; the person's decisions alone could not show it."""
    seen = _fake(monkeypatch)
    real = run.trial_home

    def lossy(out_dir: Path, session: str = "", *, withhold: Any = ()) -> Path:
        return real(out_dir, session, withhold=("jobs",) if "before_done" in session else withhold)

    def probe(home: Path, *args: Any) -> dict[str, Any]:
        assert "before_done" not in str(home), "the copy's check comes before the probe"
        three = ["completion_history", "host_configuration", "jobs"]
        return {"reply": "withheld", "code": "", "named": three, "unshared": three}

    monkeypatch.setattr(run, "trial_home", lossy)
    monkeypatch.setattr(run, "reply_probe", probe)
    done = {t.task_id: t for t in run.run(granted.path, granted.tmp / "before", client="codex", cohort="moment")}
    assert (done["moment.before_done.1"].state, done["moment.before_done.1"].code) == ("refused", run.NOT_GRANTED)
    assert "moment.before_done.1" not in [granted.by_prompt[p] for p in seen]


def test_a_session_starts_when_its_reviews_reply_would_come_back(granted: SimpleNamespace,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch)
    monkeypatch.setattr(run, "reply_probe", stand_in_probe)
    done = {t.task_id: t for t in run.run(granted.path, granted.tmp / "before", client="codex", cohort="moment")}
    assert {t.state for t in done.values()} == {"completed"} and len(seen) == 2, [r["session"].get("fault") for r in _rows(granted.tmp / "before").values()]
    rows = _rows(granted.tmp / "before")
    assert rows["moment.before_done.1"]["session"]["reply_probe"]["reply"] == "returned"
    # a consent task whose probe came back would not lack its sources: it is refused as not fresh
    monkeypatch.setattr(run, "reply_probe", lambda *a, **k: {"reply": "returned", "code": "", "named": [],
                                                              "unshared": []})
    again = {t.task_id: t for t in run.run(granted.path, granted.tmp / "again", client="codex", cohort="moment")}
    assert (again["moment.after_refusal.3"].state, again["moment.after_refusal.3"].code) == ("refused", run.NOT_FRESH)
