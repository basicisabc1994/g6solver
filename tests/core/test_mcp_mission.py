# SPDX-License-Identifier: Apache-2.0
"""The coding mission over MCP (``g6.mcp.mission``): thin tools over ``g6.workforce.api``, where the person's steps go
through the client's own dialog and nowhere else, and the check step is ``review_patch`` on the agent's working tree
with the frozen tests as its stated checks.

The first group runs anywhere and runs no repository code: ``truth.run_suite`` answers for the suite a mission freezes
from, and each test fakes what would run a test. The last group is the whole mission against real checks, and needs
bubblewrap (``bwrap_backend``): it skips elsewhere, and fails where ``G6_EXPECT_BACKEND=bubblewrap``. No model is called.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.invariants import check_envelope

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from g6.harness import patch_review  # noqa: E402
from g6.mcp import build_server, mission  # noqa: E402
from g6.reliability_pipeline.premortem import PREMORTEM_FAILURE_MODES  # noqa: E402
from g6.workforce import api, bench, coding, objective, scheduler, service, truth  # noqa: E402
from g6.workforce.runtime import Runtime  # noqa: E402

BUGGY = "def double(x):\n    return x + x + 1\n"
FIXED = "def double(x):\n    return x + x\n"
TEST = ("from calc import double\n\n\ndef test_double():\n    assert double(2) == 4\n\n\n"
        "def test_double_is_even():\n    assert double(3) % 2 == 0\n")
NODES = ("tests/test_calc.py::test_double", "tests/test_calc.py::test_double_is_even")
OBJECTIVE = "Fix double so that its tests pass."
#: What the person must never be pointed to, and an agent must never be told, for the steps a dialog takes.
TERMINAL = "g6.workforce.decide confirm"


def _commit(repo: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


def data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


async def finished(client, job: dict, detail: str = "compact") -> dict:
    """The result of ``job``, polled as an agent polls it; a refusal before any job is returned as it is."""
    if "job_id" not in job:
        return job
    for _ in range(3000):
        got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"], "detail": detail}))
        if got.get("status") == "completed":
            return got["result"]
        if got.get("status") == "failed":
            return got
        await asyncio.sleep(0.02)
    raise AssertionError("the job did not finish")


async def named(message, response_type, params, context):
    return response_type(value="Ada")


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


async def nameless(message, response_type, params, context):
    return response_type(value="   ")


class Rig:
    """A person's repository, a fresh G6 home, and a server; nothing here runs the repository's code."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
        monkeypatch.delenv(bench.ENV_DB_DIR, raising=False)
        monkeypatch.setenv("G6_MCP_SENSITIVE", "workforce.mission")
        self.repo = _commit(tmp_path / "project", {"calc.py": BUGGY, "tests/test_calc.py": TEST})
        self.server = build_server()
        self.asked: list[str] = []
        self.reviewed: list[dict] = []
        self.submitted: list[dict] = []
        monkeypatch.setattr(truth, "run_suite", lambda *a, **k: {
            "ran": True, "started": True, "why": "", "collect_errors": [], "collect_skipped": [], "seconds": 0.0,
            "outcomes": {NODES[0]: "failed", NODES[1]: "passed"}})
        monkeypatch.setattr(objective, "preflight", lambda *a, **k: {})   # it probes the bench, which runs a test
        # what a confirmation starts runs tests; before a confirmation the real one only reads the status
        monkeypatch.setattr(coding, "advance", lambda wf, mid: service.status(wf, mid))

    @property
    def wf(self):
        return api.bind()

    def client(self, handler=named):
        async def seen(message, response_type, params, context):
            self.asked.append(message)
            return await handler(message, response_type, params, context)

        return Client(self.server, elicitation_handler=seen) if handler else Client(self.server)

    def arguments(self) -> dict:
        return {"repo": str(self.repo), "objective": OBJECTIVE, "module": "calc.py", "function": "double"}

    def create(self, handler=named) -> dict:
        async def flow():
            async with self.client(handler) as client:
                return await finished(client, data(await client.call_tool("g6_mission_create", self.arguments())))

        return asyncio.run(flow())

    def confirm(self, mission_id: str, handler=named) -> dict:
        async def flow():
            async with self.client(handler) as client:
                return await finished(client, data(await client.call_tool("g6_mission_confirm",
                                                                          {"mission_id": mission_id})))

        return asyncio.run(flow())

    def submit(self, mission_id: str, repo: Path | None = None) -> dict:
        async def flow():
            async with Client(self.server) as client:
                return await finished(client, data(await client.call_tool("g6_mission_submit", {
                    "mission_id": mission_id, "repo": str(repo or self.repo)})))

        return asyncio.run(flow())

    def call(self, tool: str, **arguments) -> dict:
        async def flow():
            async with Client(self.server) as client:
                return data(await client.call_tool(tool, arguments))

        return asyncio.run(flow())

    def waiting_for_a_patch(self) -> None:
        """A confirmed mission at its patch step: the real review and the real bench are stood in for."""
        self.monkeypatch.setattr(Runtime, "awaiting", lambda runtime, mid: [SimpleNamespace(key="patch")])
        self.monkeypatch.setattr(coding, "_reproduction", lambda wf, mid: {"tests": {NODES[0]: False, NODES[1]: True}})

        def review(repo, **kwargs):
            self.reviewed.append({"repo": repo, **kwargs})
            return {"ok": True, "assessment": {"outcome": self.outcome, "report": "# the report", "reasons": [],
                                               "next_actions": [], "unknowns": []},
                    "merge": {"status": "requires_separate_approval"}, "completion_state": "qualified-draft",
                    "reliability_label": "qualified-draft", "warning_card": {"code": patch_review.CODE, "message": "m"},
                    "evidence": [], "request_id": "r", "run_id": "u", "artifact_digest": "d"}

        def submit(wf, mission_id, files):
            self.submitted.append(files)
            return {"mission_id": mission_id, "status": "active", "completion_state": "", "awaiting_chat": [],
                    "awaiting_human": []}

        self.outcome = patch_review.SUPPORTED
        self.monkeypatch.setattr(patch_review, "review_patch", review)
        self.monkeypatch.setattr(api, "submit_patch", submit)
        self.monkeypatch.setattr(api, "status", lambda wf, m, detail="summary": {
            "mission_id": m, "status": "active", "completion_state": ""})


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def _says(value) -> str:
    return json.dumps(value, default=str)


# --- the tools ---------------------------------------------------------------------------------------------------


def test_the_mission_tools_are_on_the_surface_and_the_catalogue_names_them(rig):
    import g6.workforce

    tools = set(asyncio.run(rig.server.get_tools()))
    assert set(g6.workforce.__g6_mcp_tools__) == {"g6_mission_create", "g6_mission_clarify", "g6_mission_confirm",
                                                  "g6_mission_submit", "g6_mission_status", "g6_mission_certificate"}
    assert set(g6.workforce.__g6_mcp_tools__) | {"g6_premortem"} <= tools


@pytest.mark.parametrize("tool, arguments", [
    ("g6_mission_create", {"repo": "/nowhere", "objective": "x"}),
    ("g6_mission_clarify", {"mission_id": "m", "replay_semantics": "x"}),
    ("g6_mission_confirm", {"mission_id": "m"}),
    ("g6_mission_submit", {"mission_id": "m", "repo": "/nowhere"})])
def test_a_tool_that_runs_code_stays_behind_the_hosts_permission(rig, monkeypatch, tool, arguments):
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")   # allowing a review does not allow a mission
    got = rig.call(tool, **arguments)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert got["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert "workforce.mission" in got["error"] and "G6_MCP_SENSITIVE" in got["error"]


def test_the_component_says_which_op_the_person_allows():
    from g6.mcp import describe_reply

    assert describe_reply("workforce", allow_sensitive=frozenset({"workforce.mission"}))[
        "sensitive_ops_allowed_here"] == ["mission"]
    assert describe_reply("workforce", allow_sensitive=frozenset())["sensitive_ops_allowed_here"] == []


def test_creating_a_mission_asks_the_person_to_allow_this_revision_then_freezes_the_standard(rig):
    made = rig.create()
    assert check_envelope(made) == [] and made["completion_state"] == "qualified-draft"
    assert made["status"] == "awaiting_plan" and made["run_id"] == made["mission_id"]
    assert len(rig.asked) == 1 and "Allow G6 to run this repository's code" in rig.asked[0]
    assert "not authenticated" in rig.asked[0]
    # the person reads which folder, and that the review runs in it, not only in the copy the library's terms name
    assert str(rig.repo) in rig.asked[0] and mission.IN_PLACE in rig.asked[0]
    [waiting] = made["awaiting_human"]
    assert waiting["kind"] == "acceptance" and any(NODES[0] in line for line in waiting["detail"]["what_counts_as_done"])
    assert TERMINAL not in _says(made) and "g6_mission_confirm" in waiting["question"]
    # the same revision is not asked again; a new one is, by digest
    rig.create()
    assert len(rig.asked) == 1
    _commit(rig.repo, {"notes.py": "x = 1\n"})
    rig.create()
    assert len(rig.asked) == 2


def test_the_permission_to_run_the_code_is_recorded_under_the_name_given_and_is_not_authenticated(rig):
    wf = rig.wf
    rig.create()
    [allowed] = wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED)
    assert allowed["detail"]["decided_by"] == "approver:Ada"
    assert allowed["detail"]["principal"]["authenticated"] is False


@pytest.mark.parametrize("handler", [declined, cancelled, nameless, None], ids=["decline", "cancel", "no name", "no dialog"])
def test_no_answer_from_the_person_allows_nothing_and_creates_nothing(rig, handler):
    wf = rig.wf
    got = rig.create(handler)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert "mission_id" not in got and got["consent_status"] in ("declined", "cancelled", "invalid", "unavailable")
    assert wf.missions.list(wf.venture_id) == []
    assert wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED) == []
    assert "g6.workforce.decide" not in _says(got)


def test_a_repository_that_is_not_clean_is_refused_before_anything_is_asked(rig):
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8")
    got = rig.create()
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert got["warning_card"]["code"] == "G6_E_GIT_REPO_DIRTY" and rig.asked == []


def test_a_folder_that_is_no_repository_is_refused_with_its_own_code(rig, tmp_path):
    rig.repo = tmp_path
    got = rig.create()
    assert got["warning_card"]["code"] == "G6_E_GIT_REPO_INVALID" and check_envelope(got) == []


def test_no_boundary_for_the_checks_says_so_with_its_own_code(rig, monkeypatch):
    """Where nothing can confine a run the mission's route is refused: the reply names the isolation, not "a refusal"."""
    def no_route(*a, **k):
        raise scheduler.NoRoute((("reproduce:run_tests", "[G6_E_ISOLATION_UNAVAILABLE] no boundary here"),))

    monkeypatch.setattr(objective, "preflight", no_route)
    got = rig.create()
    assert got["warning_card"]["code"] == "G6_E_ISOLATION_UNAVAILABLE" and check_envelope(got) == []


def test_what_the_person_allowed_is_bound_to_the_lines_they_read(rig, monkeypatch):
    """The dialog ends before the job starts: a repository that changed in between is refused, never allowed unread."""
    real = coding.execution_terms
    calls = []

    def changed(wf, repo):
        calls.append(repo)
        found = real(wf, repo)
        return found if len(calls) == 1 else {**found, "lines": [*found["lines"], "another digest"]}

    monkeypatch.setattr(coding, "execution_terms", changed)
    wf = rig.wf
    got = rig.create()
    assert got["completion_state"] == "blocked-escalated"
    assert wf.missions.list(wf.venture_id) == []


# --- the most important rule: only the person confirms, only in the client's dialog ----------------------------------


def test_the_confirm_tool_takes_no_answer_of_any_kind(rig):
    tool = asyncio.run(rig.server.get_tools())["g6_mission_confirm"]
    assert set(tool.parameters["properties"]) == {"mission_id"}
    mid = rig.create()["mission_id"]

    async def flow(extra):
        async with Client(rig.server) as client:
            await client.call_tool("g6_mission_confirm", {"mission_id": mid, **extra})

    for extra in ({"answer": "Ada"}, {"approved": True}, {"confirmed_by": "Ada"}, {"name": "Ada"}):
        with pytest.raises(ToolError):
            asyncio.run(flow(extra))
    assert rig.wf.missions.confirmed(mid) is False


def test_the_person_confirms_in_the_clients_dialog_and_the_name_is_recorded_as_given(rig):
    mid = rig.create()["mission_id"]
    rig.asked.clear()
    done = rig.confirm(mid)
    wf = rig.wf
    assert check_envelope(done) == [] and done["mission_id"] == mid
    assert wf.missions.confirmed(mid) is True
    [question] = rig.asked
    assert OBJECTIVE in question and NODES[0] in question and "calc.py / double" in question
    assert wf.missions.acceptance(mid).sha256 in question and "not authenticated" in question
    [confirmed] = [i for i in wf.runtime.decisions.interventions(mid) if i.kind == "clarification"]
    found = confirmed.metadata["principal"]
    assert found["identity"] == "approver:Ada" and found["authenticated"] is False


@pytest.mark.parametrize("handler, status", [(declined, "declined"), (cancelled, "cancelled"), (None, "unavailable"),
                                             (nameless, "invalid")],
                         ids=["decline", "cancel", "no dialog", "no name"])
def test_no_answer_from_the_person_leaves_the_mission_unconfirmed_and_it_cannot_move_on(rig, handler, status):
    mid = rig.create()["mission_id"]
    rig.waiting_for_a_patch()
    got = rig.confirm(mid, handler)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert got["consent_status"] == status
    wf = rig.wf
    assert wf.missions.confirmed(mid) is False
    # no terminal fallback, no other way to confirm: the reply offers none, even with no dialog at all
    assert "g6.workforce.decide" not in _says(got) and "--as" not in _says(got)
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8")
    moved = rig.submit(mid)
    assert moved["completion_state"] == "blocked-escalated" and check_envelope(moved) == []
    assert moved["warning_card"]["code"] == "G6_E_HUMAN_APPROVAL_REQUIRED"
    assert TERMINAL not in _says(moved)
    assert rig.reviewed == [] and rig.submitted == []            # nothing ran, nothing was recorded
    assert wf.missions.get(mid)["status"] == "awaiting_plan" and not wf.missions.get(mid)["plan_revision"]


def test_a_mission_nothing_waits_on_is_never_put_to_the_person(rig):
    mid = rig.create()["mission_id"]
    rig.confirm(mid)
    rig.asked.clear()
    again = rig.confirm(mid)
    assert again["completion_state"] == "blocked-escalated" and rig.asked == [] and check_envelope(again) == []


def test_a_standard_that_changed_after_the_dialog_is_not_confirmed(rig):
    """The confirmation is bound to the digest the person read."""
    mid = rig.create()["mission_id"]
    lines, sha, _ = mission.asked(mid)
    got = mission.confirm(mid, lines, "0" * 64, "Ada")
    assert got["completion_state"] == "blocked-escalated" and sha != "0" * 64
    assert rig.wf.missions.confirmed(mid) is False


def test_lines_that_changed_after_the_dialog_are_not_confirmed(rig):
    mid = rig.create()["mission_id"]
    lines, sha, _ = mission.asked(mid)
    got = mission.confirm(mid, [*lines, "a line nobody read"], sha, "Ada")
    assert got["completion_state"] == "blocked-escalated"
    assert rig.wf.missions.confirmed(mid) is False


# --- the check step ---------------------------------------------------------------------------------------------


def _confirmed(rig: Rig) -> str:
    mid = rig.create()["mission_id"]
    rig.confirm(mid)
    rig.waiting_for_a_patch()
    return mid


def test_the_check_step_is_review_patch_with_the_frozen_tests_as_stated_checks(rig):
    mid = _confirmed(rig)
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    got = rig.submit(mid)
    [review] = rig.reviewed
    wf = rig.wf
    assert Path(review["repo"]) == rig.repo.resolve()
    assert review["task"] == OBJECTIVE and review["base"] == coding.frozen(wf, mid)["baseline_revision"]
    # what the frozen standard reproduced failing is the stated requirement; the rest must keep passing
    assert review["requirements"] == [{"id": "standard", "text": OBJECTIVE, "checks": [NODES[0]]}]
    assert review["tests"] == [NODES[1]]
    assert review["expected_artifact_digest"]
    # one patch, the same bytes, to the mission's own repair ladder and certificate
    assert rig.submitted == [{"calc.py": FIXED}]
    assert got["assessment"]["report"] == "# the report" and got["mission"]["mission_id"] == mid
    assert got["merge"]["status"] == "requires_separate_approval" and check_envelope(got) == []


def test_each_review_is_kept_on_the_mission(rig):
    mid = _confirmed(rig)
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    rig.submit(mid)
    wf = rig.wf
    [kept] = [m for m in wf.missions.messages(mission_id=mid) if "review" in m["refs"]]
    assert kept["refs"]["review"] == {"outcome": patch_review.SUPPORTED, "artifact_digest": "d"}


def test_a_check_the_premortem_constructed_is_left_to_the_bench(rig, monkeypatch):
    """A constructed check lives in the frozen copy, never in the person's tree."""
    mid = _confirmed(rig)
    real = coding.frozen
    monkeypatch.setattr(coding, "frozen", lambda wf, m: {
        **real(wf, m), "required_tests": [*NODES, ".g6_harness/test_replay_x.py::test_x"],
        "original_required_tests": list(NODES)})
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    rig.submit(mid)
    assert not any(".g6_harness" in _says(rig.reviewed[0][key]) for key in ("requirements", "tests"))


def test_with_nothing_reproduced_every_frozen_test_is_a_stated_check(rig, monkeypatch):
    mid = _confirmed(rig)
    monkeypatch.setattr(coding, "_reproduction", lambda wf, m: {"tests": {NODES[0]: True, NODES[1]: True}})
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    rig.submit(mid)
    assert rig.reviewed[0]["requirements"][0]["checks"] == list(NODES) and rig.reviewed[0]["tests"] == []


def test_a_requirement_holds_what_the_review_takes_and_the_rest_still_runs(rig, monkeypatch):
    mid = _confirmed(rig)
    many = [f"tests/test_calc.py::test_{n}" for n in range(mission.MAX_CHECKS_STATED + 5)]
    monkeypatch.setattr(coding, "_reproduction", lambda wf, m: {"tests": dict.fromkeys(many, False)})
    wf = rig.wf
    [stated], rest = mission._stated(wf, mid, {"required_tests": many})
    assert stated["checks"] == many[:mission.MAX_CHECKS_STATED] and rest == many[mission.MAX_CHECKS_STATED:]
    patch_review.Requirement.model_validate(stated)   # the review's own limit


def test_a_change_to_a_frozen_test_never_reaches_the_review_or_the_mission(rig):
    mid = _confirmed(rig)
    (rig.repo / "tests" / "test_calc.py").write_text(TEST.replace("== 4", "== 5"), encoding="utf-8")
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    got = rig.submit(mid)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert got["warning_card"]["code"] == "G6_E_MISSION_OUT_OF_SCOPE" and "tests/test_calc.py" in got["error"]
    assert rig.reviewed == [] and rig.submitted == []


def test_a_change_outside_the_permitted_file_is_refused(rig):
    mid = _confirmed(rig)
    (rig.repo / "notes.py").write_text("x = 1\n", encoding="utf-8")
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    got = rig.submit(mid)
    assert got["warning_card"]["code"] == "G6_E_MISSION_OUT_OF_SCOPE" and rig.reviewed == []


def test_a_permitted_file_that_is_a_link_out_of_the_repository_is_never_read(rig, tmp_path):
    mid = _confirmed(rig)
    secret = tmp_path / "secret.txt"
    secret.write_text("a line G6 must not carry", encoding="utf-8")
    (rig.repo / "calc.py").unlink()
    try:
        (rig.repo / "calc.py").symlink_to(secret)
    except OSError:
        pytest.skip("this account cannot make a symbolic link")
    got = rig.submit(mid)
    assert got["warning_card"]["code"] == "G6_E_MISSION_OUT_OF_SCOPE"
    assert rig.reviewed == [] and rig.submitted == [] and "a line G6" not in _says(got)


def test_the_text_a_mission_takes_keeps_the_files_own_line_endings(rig):
    mid = _confirmed(rig)
    (rig.repo / "calc.py").write_bytes(FIXED.replace("\n", "\r\n").encode("utf-8"))
    rig.submit(mid)
    assert rig.submitted == [{"calc.py": FIXED.replace("\n", "\r\n")}]


def test_what_a_test_run_leaves_behind_is_no_change(rig):
    mid = _confirmed(rig)
    (rig.repo / "__pycache__").mkdir()
    (rig.repo / "__pycache__" / "calc.cpython-312.pyc").write_bytes(b"\0")
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    assert rig.submit(mid)["assessment"]["outcome"] == patch_review.SUPPORTED


def test_no_change_at_all_is_refused_and_uses_no_attempt(rig):
    mid = _confirmed(rig)
    got = rig.submit(mid)
    assert got["warning_card"]["code"] == "G6_E_MISSION_NO_CHANGE" and rig.submitted == [] and rig.reviewed == []


def test_a_review_that_refuses_leaves_the_mission_as_it_was(rig, monkeypatch):
    from g6.core.door import blocked_reply

    mid = _confirmed(rig)
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    monkeypatch.setattr(patch_review, "review_patch", lambda repo, **k: blocked_reply(
        "[G6_E_ISOLATION_UNAVAILABLE] no boundary here"))
    got = rig.submit(mid)
    assert got["warning_card"]["code"] == "G6_E_ISOLATION_UNAVAILABLE" and rig.submitted == []


def test_a_sign_of_compromised_tests_stops_the_patch_before_the_mission_takes_it(rig):
    mid = _confirmed(rig)
    rig.outcome = patch_review.INTEGRITY
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    got = rig.submit(mid)
    assert rig.submitted == [] and got["assessment"]["outcome"] == patch_review.INTEGRITY
    assert got["assessment"]["report"] == "# the report" and got["mission"]["mission_id"] == mid
    assert [m for m in rig.wf.missions.messages(mission_id=mid) if m["refs"].get("review", {}).get("outcome")
            == patch_review.INTEGRITY]


def test_a_mission_that_stopped_makes_the_reply_blocked(rig, monkeypatch):
    mid = _confirmed(rig)
    monkeypatch.setattr(api, "submit_patch", lambda wf, m, files: {
        "mission_id": m, "status": "cancelled", "completion_state": "blocked-escalated", "awaiting_chat": []})
    (rig.repo / "calc.py").write_text(FIXED, encoding="utf-8", newline="\n")
    got = rig.submit(mid)
    assert got["completion_state"] == got["reliability_label"] == "blocked-escalated" and got["ok"] is False
    assert check_envelope(got) == []


# --- reading a mission ------------------------------------------------------------------------------------------


def test_status_is_read_only_and_needs_no_permission(rig, monkeypatch):
    mid = rig.create()["mission_id"]
    monkeypatch.delenv("G6_MCP_SENSITIVE")
    got = rig.call("g6_mission_status", mission_id=mid)
    assert got["mission_id"] == mid and check_envelope(got) == [] and got["completion_state"] == "qualified-draft"
    assert 'g6_mission_status with detail="full"' in got["more"]
    assert TERMINAL not in _says(got)
    full = rig.call("g6_mission_status", mission_id=mid, detail="full")
    assert "acceptance" in full and TERMINAL not in _says(full) and check_envelope(full) == []


def test_a_mission_that_says_verified_is_held_at_the_door(rig, monkeypatch):
    mid = rig.create()["mission_id"]
    monkeypatch.setattr(api, "status", lambda wf, m, detail="summary": {
        "mission_id": m, "status": "complete", "completion_state": "verified"})
    got = rig.call("g6_mission_status", mission_id=mid)
    assert got["completion_state"] == "qualified-draft" and got["claimed_completion_state"] == "verified"
    assert got["warning_card"]["code"] == "G6_E_PROMOTION_LAW" and check_envelope(got) == []


def test_an_unknown_mission_is_a_refusal_with_a_stable_code(rig):
    for tool in ("g6_mission_status", "g6_mission_certificate"):
        got = rig.call(tool, mission_id="nope")
        assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
        assert got["warning_card"]["code"] == "G6_E_MISSION_REFUSED"


def test_a_certificate_before_any_patch_is_refused_and_a_kept_one_comes_in_the_envelope(rig, monkeypatch):
    mid = rig.create()["mission_id"]
    before = rig.call("g6_mission_certificate", mission_id=mid)
    assert before["completion_state"] == "blocked-escalated" and check_envelope(before) == []
    monkeypatch.setattr(api, "certificate", lambda wf, m: {"overall": "verified", "predicates": []})
    got = rig.call("g6_mission_certificate", mission_id=mid)
    assert check_envelope(got) == [] and got["completion_state"] == "qualified-draft"
    assert got["certificate"]["overall"] == "qualified-draft"          # the door corrects the copy it hands on


# --- the replay question ----------------------------------------------------------------------------------------


def test_an_objective_about_retries_can_be_answered_and_the_answer_is_no_confirmation(rig, monkeypatch):
    arguments = {"repo": str(rig.repo), "objective": "Fix the retry logic in double.", "module": "calc.py",
                 "function": "double"}

    async def flow():
        async with rig.client() as client:
            return await finished(client, data(await client.call_tool("g6_mission_create", arguments)))

    asked = asyncio.run(flow())
    mid = asked["mission_id"]
    assert asked["status"] == "eliciting" and asked["interaction_state"] == "awaiting_input"
    assert asked["clarification"]["next_action"]["tool"] == "g6_mission_clarify"
    answered = []
    monkeypatch.setattr(api, "answer_clarification", lambda wf, m, value: answered.append((m, value)) or {
        "mission_id": m, "status": "awaiting_plan", "completion_state": ""})

    async def answer():
        async with Client(rig.server) as client:
            return await finished(client, data(await client.call_tool("g6_mission_clarify", {
                "mission_id": mid, "replay_semantics": "not_applicable"})))

    got = asyncio.run(answer())
    assert answered == [(mid, "not_applicable")] and check_envelope(got) == [] and got["mission_id"] == mid


# --- the premortem ----------------------------------------------------------------------------------------------


def test_the_premortem_calls_no_model_runs_no_code_and_asks_at_most_three_questions(rig, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("the premortem runs nothing")

    monkeypatch.setattr(truth, "run_suite", forbidden)
    monkeypatch.setenv("G6_MCP_SENSITIVE", "")
    got = rig.call("g6_premortem", repo=str(rig.repo), task=OBJECTIVE)
    assert check_envelope(got) == [] and got["completion_state"] == "qualified-draft"
    assert got["model_calls"] == 0 and len(got["questions"]) <= 3 and got["questions"]
    assert got["intent"]["goal"] == OBJECTIVE
    ids = [h["id"] for h in got["hypotheses"]]
    assert set(PREMORTEM_FAILURE_MODES) < set(ids) and len(ids) == len(set(ids)) == 19
    assert {"meets_the_task", "missed_a_boundary", "broke_kept_behaviour", "tests_too_weak", "misleading_environment",
            "misread_intent"} <= set(ids)
    assert all(h["question"] and h["check"] for h in got["hypotheses"])


def test_the_premortems_standard_says_what_is_stated_and_what_is_not_established(rig):
    intent = {"goal": OBJECTIVE, "success_criteria": ["tests/test_calc.py::test_double passes", "Doubling is cheap."],
              "non_goals": ["Changing the rounding."]}
    got = rig.call("g6_premortem", repo=str(rig.repo), task=OBJECTIVE, intent=intent)
    standard = {r["text"]: r for r in got["standard"]}
    named = standard["tests/test_calc.py::test_double passes"]
    assert named["checks"] == ["tests/test_calc.py"] and named["status"] == "stated_checks"
    cheap = standard["Doubling is cheap."]
    assert cheap["checks"] == [] and cheap["status"] == "not_established"
    assert standard["Changing the rounding."]["kind"] == "preserve"
    assert got["test_files"] == ["tests/test_calc.py"]


@pytest.mark.parametrize("arguments, code", [({"repo": "relative/path"}, "G6_E_INPUT_VALIDATION"),
                                             ({"intent": {"no_such_field": 1}}, "G6_E_INPUT_VALIDATION"),
                                             ({"task": "   "}, "G6_E_INPUT_VALIDATION")])
def test_the_premortem_refuses_what_it_cannot_read(rig, arguments, code):
    got = rig.call("g6_premortem", **{"repo": str(rig.repo), "task": OBJECTIVE, **arguments})
    assert got["warning_card"]["code"] == code and check_envelope(got) == []


# --- the whole mission, against real checks, inside the boundary ------------------------------------------------------


@pytest.fixture
def live(tmp_path, monkeypatch, bwrap_backend):
    """A person's repository and a server, with every check real and confined."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(bench.ENV_DB_DIR, raising=False)
    monkeypatch.setenv("G6_MCP_SENSITIVE", "workforce.mission")
    repo = _commit(tmp_path / "project", {"calc.py": BUGGY, "tests/test_calc.py": TEST})
    return SimpleNamespace(repo=repo, server=build_server(), failing=NODES[0])


def _write(repo: Path, text: str) -> None:
    (repo / "calc.py").write_text(text, encoding="utf-8", newline="\n")


def test_a_whole_mission_inside_the_boundary(live):
    async def flow():
        async with Client(live.server, elicitation_handler=named) as client:
            async def job(tool, **arguments):
                return await finished(client, data(await client.call_tool(tool, arguments)))

            made = await job("g6_mission_create", repo=str(live.repo), objective=OBJECTIVE, module="calc.py",
                             function="double")
            mid = made["mission_id"]
            confirmed = await job("g6_mission_confirm", mission_id=mid)
            _write(live.repo, "def double(x):\n    return x + x + 2\n")
            wrong = await job("g6_mission_submit", mission_id=mid, repo=str(live.repo))
            _write(live.repo, FIXED)
            right = await job("g6_mission_submit", mission_id=mid, repo=str(live.repo))
            status = data(await client.call_tool("g6_mission_status", {"mission_id": mid}))
            certificate = data(await client.call_tool("g6_mission_certificate", {"mission_id": mid}))
            return made, confirmed, wrong, right, status, certificate

    made, confirmed, wrong, right, status, certificate = asyncio.run(flow())
    assert made["status"] == "awaiting_plan" and made["awaiting_human"], made
    assert confirmed["awaiting_chat"] and confirmed["awaiting_chat"][0]["unit"] == "patch", confirmed
    # the brief tells the agent what this tool takes: its folder, not a map of files
    assert "<the full new text>" not in _says(confirmed), confirmed
    assert "g6_mission_submit" in confirmed["awaiting_chat"][0]["instructions"], confirmed
    assert wrong["assessment"]["outcome"] == patch_review.DEFECT, wrong
    assert wrong["mission"]["awaiting_chat"] and wrong["mission"]["status"] != "done", wrong   # sent back to repair
    assert right["assessment"]["outcome"] == patch_review.SUPPORTED, right
    assert right["completion_state"] == "qualified-draft" and right["merge"]["status"] == "requires_separate_approval"
    assert status["completion_state"] == "qualified-draft" and status["status"] == "done", status
    assert check_envelope(right) == [] and check_envelope(status) == [] and check_envelope(certificate) == []
    assert certificate["certificate"]
    assert "verified" not in right["assessment"]["report"].lower().replace("unverified", "")
    # each round's review is on the mission's record, bound to the tree it read, and the two trees differ
    kept = [m["refs"]["review"] for m in api.bind().missions.messages(made["mission_id"]) if "review" in m["refs"]]
    assert [k["outcome"] for k in kept] == [patch_review.DEFECT, patch_review.SUPPORTED]
    assert all(k["artifact_digest"] for k in kept) and kept[0]["artifact_digest"] != kept[1]["artifact_digest"]


def test_a_repeated_patch_counts_as_repeated_work_on_the_missions_own_ladder(live):
    async def flow():
        async with Client(live.server, elicitation_handler=named) as client:
            async def job(tool, **arguments):
                return await finished(client, data(await client.call_tool(tool, arguments)))

            mid = (await job("g6_mission_create", repo=str(live.repo), objective=OBJECTIVE, module="calc.py",
                             function="double"))["mission_id"]
            await job("g6_mission_confirm", mission_id=mid)
            _write(live.repo, "def double(x):\n    return x + x + 2\n")
            first = await job("g6_mission_submit", mission_id=mid, repo=str(live.repo))
            return first, await job("g6_mission_submit", mission_id=mid, repo=str(live.repo))

    first, again = asyncio.run(flow())
    assert first["mission"]["awaiting_chat"][0]["attempts_left"] == 2, first
    assert coding.REPEAT_RULE not in _says(first["mission"]), first
    assert again["mission"]["awaiting_chat"][0]["attempts_left"] == 1, again   # it used an attempt
    assert coding.REPEAT_RULE in _says(again["mission"]), again                # and the brief changed


def test_a_high_consequence_mission_waits_for_a_person_after_its_checks(live):
    """Its patch approval is not over MCP yet: the mission waits, the state is no pass, and the status says who decides."""
    async def flow():
        async with Client(live.server, elicitation_handler=named) as client:
            async def job(tool, **arguments):
                return await finished(client, data(await client.call_tool(tool, arguments)))

            mid = (await job("g6_mission_create", repo=str(live.repo), objective=OBJECTIVE, module="calc.py",
                             function="double", risk="R3"))["mission_id"]
            await job("g6_mission_confirm", mission_id=mid)
            _write(live.repo, FIXED)
            return await job("g6_mission_submit", mission_id=mid, repo=str(live.repo))

    got = asyncio.run(flow())
    mission_now = got["mission"]
    assert [w["unit"] for w in mission_now["awaiting_human"]] == ["approve-patch"], mission_now
    assert mission_now["status"] != "done" and mission_now["completion_state"] == "qualified-draft", mission_now
    assert "g6.workforce.decide list" in _says(mission_now["result"]), mission_now["result"]


def test_the_repair_ladder_is_the_missions_own_and_stops_for_a_person(live):
    async def flow():
        async with Client(live.server, elicitation_handler=named) as client:
            async def job(tool, **arguments):
                return await finished(client, data(await client.call_tool(tool, arguments)))

            mid = (await job("g6_mission_create", repo=str(live.repo), objective=OBJECTIVE, module="calc.py",
                             function="double"))["mission_id"]
            await job("g6_mission_confirm", mission_id=mid)
            replies = []
            for attempt in range(1, 4):
                _write(live.repo, f"def double(x):\n    return x + x + {attempt + 1}\n")
                replies.append(await job("g6_mission_submit", mission_id=mid, repo=str(live.repo)))
                if replies[-1]["mission"].get("status") in ("cancelled", "failed", "blocked"):
                    break
            return replies

    replies = asyncio.run(flow())
    last = replies[-1]
    assert last["completion_state"] == "blocked-escalated" and last["ok"] is False, last
    assert "Human action required" in _says(last["mission"]), last


def test_a_mission_from_a_linked_worktree_and_a_replay_question_inside_the_boundary(live, tmp_path):
    """The folder a coding agent works in is often a linked worktree, whose .git is a file; and the words retry and
    duplicate ask a question first, which the agent answers and the person still confirms."""
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(linked)], cwd=live.repo, check=True,
                   capture_output=True)

    async def flow():
        async with Client(live.server, elicitation_handler=named) as client:
            async def job(tool, **arguments):
                return await finished(client, data(await client.call_tool(tool, arguments)))

            made = await job("g6_mission_create", repo=str(linked), objective="Fix the retry logic in double.",
                             module="calc.py", function="double")
            mid = made["mission_id"]
            unconfirmed = await job("g6_mission_submit", mission_id=mid, repo=str(linked))
            answered = await job("g6_mission_clarify", mission_id=mid, replay_semantics="not_applicable")
            still = await job("g6_mission_submit", mission_id=mid, repo=str(linked))
            confirmed = await job("g6_mission_confirm", mission_id=mid)
            _write(linked, FIXED)
            return made, unconfirmed, answered, still, confirmed, await job("g6_mission_submit", mission_id=mid,
                                                                            repo=str(linked))

    made, unconfirmed, answered, still, confirmed, right = asyncio.run(flow())
    assert made["status"] == "eliciting" and unconfirmed["completion_state"] == "blocked-escalated", (made, unconfirmed)
    assert answered["awaiting_human"] and still["warning_card"]["code"] == "G6_E_HUMAN_APPROVAL_REQUIRED", (answered, still)
    assert confirmed["awaiting_chat"], confirmed
    assert right["assessment"]["outcome"] == patch_review.SUPPORTED and right["mission"]["status"] == "done", right


def test_a_test_the_agent_changed_in_its_tree_is_refused_inside_the_boundary(live):
    async def flow():
        async with Client(live.server, elicitation_handler=named) as client:
            async def job(tool, **arguments):
                return await finished(client, data(await client.call_tool(tool, arguments)))

            mid = (await job("g6_mission_create", repo=str(live.repo), objective=OBJECTIVE, module="calc.py",
                             function="double"))["mission_id"]
            await job("g6_mission_confirm", mission_id=mid)
            (live.repo / "tests" / "test_calc.py").write_text(TEST.replace("== 4", "== 5"), encoding="utf-8")
            return await job("g6_mission_submit", mission_id=mid, repo=str(live.repo))

    got = asyncio.run(flow())
    assert got["warning_card"]["code"] == "G6_E_MISSION_OUT_OF_SCOPE", got
