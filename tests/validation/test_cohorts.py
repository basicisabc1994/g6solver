# SPDX-License-Identifier: Apache-2.0
"""The replay cohorts (``cohorts.py``; PLAN.md, 2026-11-06 night): their names from each stage's own saved record, disjoint;
the manifest's digest, which any change to a manifest changes; who is excluded and why; the memory floor before each container
step; and the scripted session the round-trip control runs in Claude Code's place. Fakes only: no docker, no model."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import cohorts  # noqa: E402
import run as we  # noqa: E402

HERE = ROOT / "validation" / "worked_examples"


def ids(name: str) -> list[str]:
    return [t["id"] for t in json.loads((HERE / name).read_text("utf-8"))["tasks"]]


# --- the names --------------------------------------------------------------------------------------------------------------
def test_each_stages_names_are_its_own_saved_records():
    assert list(cohorts.STAGE_1_FRESH) == ids("tasks_fresh.json")
    assert cohorts.STAGE_1_HELD == we.R3_HELD and set(cohorts.STAGE_1_HELD) <= set(ids("tasks.json"))
    assert list(cohorts.STAGE_2) == ids("tasks_unseen.json")
    draw = ids("tasks_stage4.json")
    gave_way = {"yarl-1852", "yarl-1919", "pytz-142", "opentelemetry-python-5854", "pip-14011", "hatch-2473", "hatch-2257",
                "s3fs-1065", "s3fs-1056"}
    assert list(cohorts.STAGE_4_WHOLE) == [bug for bug in draw if bug not in gave_way] and len(cohorts.STAGE_4_WHOLE) == 22
    assert len(cohorts.K3_PILOT) == 7 and len(set(cohorts.K3_PILOT)) == 7
    assert len(cohorts.FINAL) == len(set(cohorts.FINAL)) == 84


def test_the_three_cohorts_are_disjoint_and_sized_as_registered_with_the_overlap_named():
    dev, sel, final = map(set, (cohorts.DEVELOPMENT, cohorts.SELECTION, cohorts.FINAL))
    assert not dev & sel and not dev & final and not sel & final
    assert len(dev) == 35 == len(cohorts.DEVELOPMENT)       # stages 1 and 2's 34, and the one pilot bug from stage 4
    assert set(cohorts.K3_PILOT) <= dev and {*cohorts.STAGE_1_FRESH, *cohorts.STAGE_1_HELD, *cohorts.STAGE_2} <= dev
    assert sel == set(cohorts.STAGE_4_WHOLE) - set(cohorts.K3_PILOT) and len(sel) == 21
    assert set(cohorts.LEFT_OUT["selection"]) == {"pydantic-settings-1089"}
    assert set(cohorts.FINAL_SEEN) <= final


#: Each cohort's names, in order, by digest: frozen by name (PLAN.md, 2026-11-06 night), so any change to a list fails here first.
PINNED = {"development": "6b1b30e8ee6f1e95840860f171ed1e58dd825dc9a017d92b1e1174a986c6a12e",
          "selection": "7c3cc64f83fef084e77382b3b60e006a32dbef1c0109841b7880e212a41870ca",
          "final": "6cb1afde1a8a4796a7ca6fd4d5f160acfb79bbbfefa167255322cea593b06ddb"}


def test_each_cohorts_names_are_pinned_by_digest_so_a_change_to_them_shows():
    assert {name: hashlib.sha256("\n".join(bugs).encode()).hexdigest() for name, bugs in cohorts.COHORTS.items()} == PINNED
    assert cohorts.CONTROLLED == ("development", "selection")


# --- the manifest ---------------------------------------------------------------------------------------------------------------
def _images(bugs, *, missing=()):
    return {bug: {"task_image": f"g6we-task:{bug}", "oracle_image": f"g6we-oracle:{bug}",
                  "task_image_id": None if bug in missing else "sha256:" + "a" * 64,
                  "oracle_image_id": None if bug in missing else "sha256:" + "b" * 64} for bug in bugs}


def _passed():
    return {"failed": "", "why": "", "validate": {"oracle_image_id": "sha256:" + "b" * 64, "base_fails": True},
            "selfcheck": {"gold": {"passes": True}, "empty": {"passes": False}}, "round_trip": {"holds": True}}


def test_a_manifest_holds_its_own_digest_and_any_change_to_it_changes_the_digest():
    bugs = cohorts.SELECTION
    body = cohorts.manifest("selection", _images(bugs), {bug: _passed() for bug in bugs})
    assert cohorts.holds(body) and body["digest"] == cohorts.digest(body) and len(body["usable"]) == 21
    assert cohorts.manifest("selection", _images(bugs), {bug: _passed() for bug in bugs}) == body      # the same inputs, the same
    changes = [
        lambda m: m["bugs"][0].update(task_image_id="sha256:" + "c" * 64),          # an image digest
        lambda m: m["bugs"][3]["controls"]["round_trip"].update(holds=False),       # a control result
        lambda m: m["bugs"].reverse(),                                              # the order
        lambda m: m["usable"].pop(),                                                # who is usable
        lambda m: m["excluded"].append({"id": "x", "why": "y"}),                    # who is excluded
        lambda m: m.update(g6_commit="0" * 40),                                     # the G6 the images are of
    ]
    for change in changes:
        edited = copy.deepcopy(body)
        change(edited)
        assert not cohorts.holds(edited) and cohorts.digest(edited) != body["digest"]


def test_a_bug_with_no_image_no_control_or_a_failed_control_is_excluded_and_named_with_why():
    bugs = cohorts.SELECTION
    results = {bug: _passed() for bug in bugs[2:]}
    results[bugs[2]] = {**_passed(), "failed": "round trip", "why": "the kept patch is not the known patch, byte for byte"}
    results[bugs[3]] = {**_passed(), "validate": {"oracle_image_id": "sha256:" + "d" * 64}}
    body = cohorts.manifest("selection", _images(bugs, missing=(bugs[0],)), results)
    why = {e["id"]: e["why"] for e in body["excluded"]}
    assert why == {bugs[0]: "docker holds no task or oracle image for it", bugs[1]: "no control result",
                   bugs[2]: "round trip: the kept patch is not the known patch, byte for byte",
                   bugs[3]: "the controls ran on another oracle image than docker holds now"}
    assert body["usable"] == list(bugs[4:]) and body["left_out"] == [{"id": "pydantic-settings-1089",
                                                                      "why": cohorts.LEFT_OUT["selection"]["pydantic-settings-1089"]}]


def test_the_final_manifest_holds_names_and_image_digests_only_and_says_what_its_builder_saw():
    body = cohorts.manifest("final", _images(cohorts.FINAL, missing=cohorts.FINAL[10:]))
    assert all("controls" not in bug for bug in body["bugs"]) and isinstance(body["controls"], str)
    assert set(body["bugs"][0]) <= {"id", "task_image", "oracle_image", "task_image_id", "oracle_image_id", "usable",
                                    "seen_by_builder"}
    assert body["usable"] == list(cohorts.FINAL[:10]) and len(body["excluded"]) == 74
    seen = [b for b in body["bugs"] if "seen_by_builder" in b]
    assert [b["id"] for b in seen] == list(cohorts.FINAL_SEEN)


def test_a_manifest_is_written_once_and_never_with_a_digest_that_is_not_its_own(tmp_path):
    body = cohorts.manifest("final", _images(cohorts.FINAL))
    path = cohorts.write_manifest(tmp_path, body)
    assert path.name == f"final-{body['digest'][:12]}.json" and cohorts.holds(json.loads(path.read_text("utf-8")))
    with pytest.raises(FileExistsError):
        cohorts.write_manifest(tmp_path, body)
    with pytest.raises(ValueError):
        cohorts.write_manifest(tmp_path, {**body, "usable": []})


# --- the controls -----------------------------------------------------------------------------------------------------------
def _controls(monkeypatch, tmp_path, *, f2p=("t",), gold=True, empty=False, tree="tree", trip=None):
    archive = we.c.Archive(tmp_path / "controls")
    ctx = SimpleNamespace(archive=archive, g6=cohorts.g6_for(), variant=we.ROUND3, docker=None)
    task = SimpleNamespace(id="bug-1")

    def validate(ctx_, task_):
        run = archive.start("validate", task_.id)
        run.finish({"ok": bool(f2p), "fail_to_pass": list(f2p), "pass_to_pass": [], "oracle_image_id": "sha256:o",
                    "fixed": {"results": {"t": "passed"}}, "why": "" if f2p else "nothing"}, "finished")

    def selfcheck(ctx_, task_):
        run = archive.start("selfcheck", task_.id)
        run.write("gold.patch", b"gold")
        run.finish({"ok": gold and not empty, "gold": {"passes": gold}, "empty": {"passes": empty}}, "finished")

    trips = []
    monkeypatch.setattr(we, "validate", validate)
    monkeypatch.setattr(we, "selfcheck", selfcheck)
    monkeypatch.setattr(cohorts, "recorded_tree", lambda study, bug: tree)
    monkeypatch.setattr(cohorts, "round_trip", lambda ctx_, task_, g, folder, t: (trips.append((g.read_bytes(), t)), trip or {
        "holds": True, "status": "finished", "error": None})[1])
    return cohorts.controls(ctx, task, None), trips


@pytest.mark.parametrize(("kw", "failed", "why"), [
    ({"f2p": ()}, "validate", "nothing"),
    ({"gold": False}, "selfcheck", "the maintainers' fix, given as a patch, does not pass the maintainers' tests"),
    ({"empty": True}, "selfcheck", "an empty patch was not seen failing the maintainers' tests"),
    ({"tree": ""}, "round trip", "no earlier run recorded upstream's tree for the base commit"),
    ({"trip": {"holds": False, "status": "finished"}}, "round trip", "the kept patch is not the known patch, byte for byte"),
    ({"trip": {"holds": False, "status": "error", "error": "RuntimeError: boom"}}, "round trip", "RuntimeError: boom"),
])
def test_a_bug_that_fails_a_control_is_named_with_the_control_and_why_and_no_later_control_runs(monkeypatch, tmp_path, kw, failed,
                                                                                                  why):
    got, trips = _controls(monkeypatch, tmp_path, **kw)
    assert (got["failed"], got["why"]) == (failed, why)
    assert bool(trips) == (failed == "round trip" and kw.get("tree", "tree") != "")


def test_a_bug_that_passes_every_control_has_each_result_and_the_round_trip_reads_selfchecks_own_copy_of_the_fix(monkeypatch,
                                                                                                                  tmp_path):
    got, trips = _controls(monkeypatch, tmp_path)
    assert got["failed"] == "" and got["validate"]["base_fails"] and got["selfcheck"]["gold"]["passes"]
    assert got["selfcheck"]["empty"]["passes"] is False and got["round_trip"]["holds"] and trips == [(b"gold", "tree")]


def test_each_container_step_waits_while_memory_is_under_the_floor():
    readings, slept, ran = iter([5.0, None, 6.4, 7.0, 3.0]), [], []
    docker = cohorts.Floored(reader=lambda: next(readings), sleep=slept.append, every=1.0,
                             runner=lambda argv, **kw: (ran.append(argv), SimpleNamespace(returncode=0, stdout=b"", stderr=b""))[1])
    docker("image", "inspect", "x")                         # no container step: no wait
    docker("run", "-d", "x")                                # 5.0, unreadable, 6.4: three waits, then 7.0
    assert slept == [1.0, 1.0, 1.0] and docker.waited == 3.0 and [a[1] for a in ran] == ["image", "run"]
    assert cohorts.FLOOR_GB == 6.5


def test_image_ids_name_the_images_as_the_runner_does_and_read_only_their_ids():
    asked = []

    def docker(*args, timeout=None):
        asked.append(args)
        return SimpleNamespace(returncode=0 if "oracle" in args[2] else 1, stdout=b"sha256:" + b"f" * 64, stderr=b"")
    got = cohorts.image_ids("click-3769", docker)
    task_image, oracle = we.image_names(SimpleNamespace(id="click-3769"), cohorts.G6_COMMIT, we.ROUND3)
    assert got == {"task_image": task_image, "oracle_image": oracle, "task_image_id": None, "oracle_image_id": "sha256:" + "f" * 64}
    assert all(a[:2] == ("image", "inspect") and a[3:] == ("--format", "{{.Id}}") for a in asked)


# --- the scripted session, in Claude Code's place --------------------------------------------------------------------------------
FAKE_BOX = textwrap.dedent('''
    import json, sys
    calls = []
    for line in sys.stdin:
        msg = json.loads(line)
        calls.append(msg)
        if msg.get("method") == "tools/call":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text",
                             "text": "exit code 0\\n" + msg["params"]["arguments"]["command"]}], "isError": False}}) + "\\n")
        elif "id" in msg:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}) + "\\n")
        sys.stdout.flush()
    open("box_calls.json", "w").write(json.dumps(calls))
''')


def test_the_scripted_session_writes_the_known_patch_calls_the_box_once_and_streams_an_init_the_host_executor_accepts(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "box.py").write_text(FAKE_BOX, encoding="utf-8")
    (tmp_path / "known.patch").write_bytes(b"diff --git a/x b/x\n")
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"box": {"command": sys.executable, "args": ["-I", str(tmp_path / "box.py")]}}}), "utf-8")
    argv = we.host_args(mcp, tmp_path / "settings.json", we.R3_MODELS[1])
    done = subprocess.run([sys.executable, "-I", str(HERE / "scripted_session.py"), "--known", str(tmp_path / "known.patch"), *argv],
                          input=b"the prompt", capture_output=True, cwd=ws, timeout=120)
    assert done.returncode == 0, done.stderr
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    assert we.host_init(lines[0])["why"] == "" and we.host_init(lines[0])["servers"] == ["box"]   # exactly the six tools and the box
    assert we.result_status(lines[-1]) == "finished" and lines[-1]["session_id"] == lines[0]["session_id"]
    assert (ws / ".we-known.patch").read_bytes() == b"diff --git a/x b/x\n"     # the Write tool's way, in the workspace
    calls = json.loads((ws / "box_calls.json").read_text("utf-8"))
    assert [c.get("method") for c in calls] == ["initialize", "notifications/initialized", "tools/call"]
    assert calls[-1]["params"] == {"name": "run", "arguments": {"command": "git apply --binary --whitespace=nowarn "
                                                                           ".we-known.patch && rm -f .we-known.patch"}}
