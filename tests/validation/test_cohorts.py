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


IDS = {"task_image_id": "sha256:" + "a" * 64, "oracle_image_id": "sha256:" + "b" * 64}


def _seen(results, exit="1"):
    return {"call": "passed", "pytest_exit": exit, "report": True, "results": results}


def _passed(ids=IDS):
    """A control record as ``cohorts.controls`` keeps it: each control's observed outcome and the two ids it ran on."""
    return {"failed": "", "why": "",
            "validate": {"run": "validate/x/1", "images": dict(ids), "fail_to_pass": ["t::bug"], "pass_to_pass": ["t::ok"],
                         "base": _seen({"t::bug": "failed"}), "fixed": _seen({"t::bug": "passed", "t::ok": "passed"}, "0")},
            "selfcheck": {"run": "selfcheck/x/1", "images": dict(ids), "gold": _seen({"t::bug": "passed", "t::ok": "passed"}, "0"),
                          "empty": _seen({"t::bug": "failed", "t::ok": "passed"})},
            "round_trip": {"holds": True, "status": "finished", "error": None, "kept_read": True, "known_bytes": 3,
                           "known_sha256": "c" * 64, "kept_bytes": 3, "kept_sha256": "c" * 64, "same_bytes": True,
                           "images": dict(ids)},
            "images_after": dict(ids)}


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
    results[bugs[2]] = _passed()
    results[bugs[2]]["round_trip"].update(kept_sha256="d" * 64, same_bytes=False)
    results[bugs[3]] = _passed()
    results[bugs[3]]["validate"]["images"]["oracle_image_id"] = "sha256:" + "d" * 64
    body = cohorts.manifest("selection", _images(bugs, missing=(bugs[0],)), results)
    why = {e["id"]: e["why"] for e in body["excluded"]}
    assert why == {bugs[0]: "docker holds no task or oracle image for it", bugs[1]: "no control result",
                   bugs[2]: "round trip: the kept patch is not the known patch, byte for byte",
                   bugs[3]: "the base ran on other images than these: {'task_image_id': 'sha256:" + "a" * 64
                            + "', 'oracle_image_id': 'sha256:" + "d" * 64 + "'}"}
    assert body["usable"] == list(bugs[4:]) and body["left_out"] == [{"id": "pydantic-settings-1089",
                                                                      "why": cohorts.LEFT_OUT["selection"]["pydantic-settings-1089"]}]


def test_the_final_manifest_holds_names_and_image_digests_only_none_usable_before_its_controls_and_says_what_its_builder_saw():
    body = cohorts.manifest("final", _images(cohorts.FINAL, missing=cohorts.FINAL[10:]))
    assert all("controls" not in bug for bug in body["bugs"]) and isinstance(body["controls"], str)
    assert set(body["bugs"][0]) <= {"id", "task_image", "oracle_image", "task_image_id", "oracle_image_id", "usable",
                                    "seen_by_builder"}
    # ruled 2026-11-06 (evening): a final-cohort bug anyone working on G6 has seen any part of is excluded, never pending
    assert body["usable"] == [] and len(body["pending"]) == 84 - len(cohorts.FINAL_SEEN) == 83
    assert body["excluded"] == [{"id": bug, "why": "excluded: seen by a builder before the final measurement"}
                                for bug in cohorts.FINAL_SEEN] == [{"id": "strix-1116", "why": cohorts.SEEN_WHY}]
    why = {p["id"]: p["why"] for p in body["pending"]}
    assert {why[b] for b in cohorts.FINAL[:10]} == {"its images are present; its controls have not run"}
    assert {why[b] for b in cohorts.FINAL[10:] if b not in cohorts.FINAL_SEEN} == {"docker holds no task or oracle image for it"}
    seen = [b for b in body["bugs"] if "seen_by_builder" in b]
    assert [b["id"] for b in seen] == list(cohorts.FINAL_SEEN) and all(b["usable"] is False for b in seen)
    assert not {p["id"] for p in body["pending"]} & set(cohorts.FINAL_SEEN)


def test_a_manifest_is_written_once_and_never_with_a_digest_that_is_not_its_own(tmp_path):
    body = cohorts.manifest("final", _images(cohorts.FINAL))
    path = cohorts.write_manifest(tmp_path, body)
    assert path.name == f"final-{body['digest'][:12]}.json" and cohorts.holds(json.loads(path.read_text("utf-8")))
    with pytest.raises(FileExistsError):
        cohorts.write_manifest(tmp_path, body)
    with pytest.raises(ValueError):
        cohorts.write_manifest(tmp_path / "other", {**body, "g6_commit": "0" * 40})
    assert not (tmp_path / "other").exists()


def test_the_committed_manifests_hold_their_digests_and_every_usable_bug_passed_all_four_controls():
    found = {json.loads(p.read_text("utf-8"))["cohort"]: (p, json.loads(p.read_text("utf-8")))
             for p in sorted((HERE / "manifests").glob("*.json"))}
    assert set(found) == set(cohorts.COHORTS)
    for name, (path, body) in found.items():
        assert cohorts.holds(body) and path.name == f"{name}-{body['digest'][:12]}.json"
        assert [b["id"] for b in body["bugs"]] == list(cohorts.cohort(name))
        assert set(body["usable"]) | {e["id"] for e in body["excluded"]} | {p["id"] for p in body.get("pending", [])} == \
            set(cohorts.cohort(name))
        for bug in body["bugs"]:
            if bug["usable"]:      # each control seen to hold, on both of the bug's ids (repair 1: observed, never inferred)
                got, ids = bug["controls"], cohorts.the_ids(bug)
                assert got["failed"] == "" and got["validate"]["base"]["state"] == "failed"
                assert got["validate"]["fixed"]["state"] == "passed" and got["selfcheck"]["gold"]["state"] == "passed"
                assert got["selfcheck"]["empty"]["state"] == "failed" and got["round_trip"]["kept_read"] is True
                assert got["round_trip"]["kept_sha256"] == got["round_trip"]["known_sha256"] and got["round_trip"]["known_bytes"]
                assert all(cohorts.the_ids(x) == ids for x in (got["validate"]["images"], got["selfcheck"]["images"],
                                                              got["round_trip"]["images"], got["images_after"]))
                assert all(ids.values()) and len(got["record"]["sha256"]) == 64
    assert found["final"][1]["usable"] == [] and all("controls" not in b for b in found["final"][1]["bugs"])
    assert found["final"][1]["excluded"] == [{"id": bug, "why": cohorts.SEEN_WHY} for bug in cohorts.FINAL_SEEN]


# --- the controls -----------------------------------------------------------------------------------------------------------
TRIP = {"holds": True, "status": "finished", "error": None, "kept_read": True, "known_bytes": 4, "known_sha256": "e" * 64,
        "kept_bytes": 4, "kept_sha256": "e" * 64, "same_bytes": True}


def _controls(monkeypatch, tmp_path, *, f2p=("t",), gold=True, empty=False, tree="tree", trip=None, ids=None):
    archive = we.c.Archive(tmp_path / "controls")
    readings, asked = list(ids or [IDS] * 4), []

    def docker(*args, **kw):          # each reading of the two ids (``image_ids``) answers from the next of ``readings``
        asked.append(args)
        now = readings[(len(asked) - 1) // 2]
        return SimpleNamespace(returncode=0, stdout=now["task_image_id" if "task" in args[2] else "oracle_image_id"].encode(),
                               stderr=b"")
    ctx = SimpleNamespace(archive=archive, g6=cohorts.g6_for(), variant=we.ROUND3, docker=docker)
    task = SimpleNamespace(id="bug-1")
    lists = {"fail_to_pass": list(f2p), "pass_to_pass": []}

    def validate(ctx_, task_, **kw):          # kw: report=True, pytest's own report, as the controls ask
        run = archive.start("validate", task_.id)
        run.finish({"ok": bool(f2p), **lists, "oracle_image_id": "sha256:o", "base": _seen({"t": "failed"}),
                    "fixed": _seen({"t": "passed"}, "0"), "why": "" if f2p else "nothing"}, "finished")

    def selfcheck(ctx_, task_, **kw):
        run = archive.start("selfcheck", task_.id)
        run.write("gold.patch", b"gold")
        run.finish({"ok": gold and not empty, "gold": {"observed": _seen({"t": "passed" if gold else "failed"})},
                    "empty": {"observed": _seen({"t": "passed" if empty else "failed"})}}, "finished")

    trips = []
    monkeypatch.setattr(we, "validate", validate)
    monkeypatch.setattr(we, "selfcheck", selfcheck)
    monkeypatch.setattr(cohorts, "recorded_tree", lambda study, bug: tree)
    monkeypatch.setattr(cohorts, "round_trip", lambda ctx_, task_, g, folder, t: (trips.append((g.read_bytes(), t)), trip or TRIP)[1])
    return cohorts.controls(ctx, task, None), trips


@pytest.mark.parametrize(("kw", "failed", "why"), [
    ({"f2p": ()}, "base", "no test fails on the parent's code and passes with the fix"),
    ({"gold": False}, "gold patch", "not seen to pass the maintainers' tests: failed: 1 of 1 seen to fail"),
    ({"empty": True}, "empty patch", "not seen to fail the maintainers' tests: passed: "),
    ({"tree": ""}, "round trip", "no earlier run recorded upstream's tree for the base commit"),
    ({"trip": {**TRIP, "kept_sha256": "f" * 64, "same_bytes": False}}, "round trip",
     "the kept patch is not the known patch, byte for byte"),
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
    assert got["failed"] == "" and cohorts.admit(got, IDS) == "" and trips == [(b"gold", "tree")]
    assert [cohorts.control_why(got, c) for c in cohorts.CONTROLS] == ["", "", "", ""]
    assert got["validate"]["base"] == {"call": "passed", "pytest_exit": "1", "report": True, "results": {"t": "failed"}}


# --- findings 7 and 11: admission decided from the recorded outcomes, by one rule, on both image ids ------------------------------
def test_finding_7_a_round_trip_on_an_old_task_image_does_not_admit_its_replacement():
    bug = cohorts.SELECTION[0]
    images = {bug: {"task_image_id": "sha256:new-task", "oracle_image_id": "sha256:oracle"}}
    controls = {bug: {
        "failed": "", "why": "", "images": {"task_image_id": "sha256:old-task", "oracle_image_id": "sha256:oracle"},
        "validate": {"base_fails": True, "oracle_image_id": "sha256:oracle"},
        "selfcheck": {"gold": {"passes": True}, "empty": {"passes": False}},
        "round_trip": {"holds": True, "same_bytes": True, "status": "finished"},
    }}
    manifest = cohorts.manifest("selection", images, controls)
    assert bug not in manifest["usable"]
    old = {**IDS, "task_image_id": "sha256:" + "9" * 64}         # every control observed to hold, all on the old task image
    why = {e["id"]: e["why"] for e in cohorts.manifest("selection", _images([bug]), {bug: _passed(old)})["excluded"]}
    assert why[bug].startswith("the base ran on other images than these") and "9" * 64 in why[bug]
    for step in ("validate", "selfcheck", "round_trip"):          # one control on the old task image is enough to exclude
        record = _passed()
        record[step]["images"] = dict(old)
        assert cohorts.admit(record, IDS).endswith(f"ran on other images than these: {old}"), step
    record = _passed()
    record["images_after"] = dict(old)                             # the image changed while the controls ran
    assert cohorts.admit(record, IDS).startswith("the images changed while the controls ran")
    record = _passed()
    del record["selfcheck"]["images"]
    assert cohorts.admit(record, IDS) == "the image ids the gold and empty patches ran on were never recorded"


def test_finding_7_a_change_to_either_image_id_runs_the_controls_again_and_only_an_admitted_record_is_kept(monkeypatch, tmp_path):
    bug = cohorts.SELECTION[0]
    now = {"task_image": "t", "oracle_image": "o", **IDS}
    ran = []
    monkeypatch.setattr(cohorts, "Floored", lambda: SimpleNamespace(waited=0.0))
    monkeypatch.setattr(cohorts, "image_ids", lambda b, docker: dict(now))
    monkeypatch.setattr(we, "task_by_id", lambda b: SimpleNamespace(id=b))
    monkeypatch.setattr(cohorts, "controls", lambda ctx, task, study: (ran.append(task.id), {"failed": "x", "why": "y"})[1])
    for record, again in ((_passed(), False), (_passed({**IDS, "task_image_id": "sha256:" + "9" * 64}), True),
                          (_passed({**IDS, "oracle_image_id": "sha256:" + "9" * 64}), True), ({**_passed(), "round_trip": {}}, True)):
        monkeypatch.setattr(cohorts, "latest_controls", lambda archive, record=record: {bug: record})
        ran.clear()
        assert cohorts.main(["controls", "--cohort", "selection", "--only", bug, "--archive", str(tmp_path)]) == 0
        assert ran == ([bug] if again else []), record
        ran.clear()                                   # --again: every record made again by the code as it is now
        assert cohorts.main(["controls", "--cohort", "selection", "--only", bug, "--archive", str(tmp_path), "--again"]) == 0
        assert ran == [bug]


def test_finding_11_a_manifest_admits_by_the_recorded_outcomes_and_never_by_a_callers_failed_flag():
    bug = cohorts.SELECTION[0]
    images = {bug: {"task_image_id": "sha256:task", "oracle_image_id": "sha256:oracle"}}
    results = {bug: {"failed": "", "validate": {"oracle_image_id": "sha256:oracle", "base_fails": False},
                     "selfcheck": {"gold": {"passes": False}, "empty": {"passes": True}},
                     "round_trip": {"holds": False, "same_bytes": False, "status": "error"}}}
    assert bug not in cohorts.manifest("selection", images, results)["usable"]
    contrary = _passed()             # the flag says nothing failed; each outcome as observed says the opposite of what it must
    contrary["validate"]["base"] = _seen({"t::bug": "passed"}, "0")
    contrary["selfcheck"]["gold"] = _seen({"t::bug": "failed", "t::ok": "passed"})
    contrary["selfcheck"]["empty"] = _seen({"t::bug": "passed", "t::ok": "passed"}, "0")
    contrary["round_trip"].update(kept_read=False, kept_sha256=None)
    body = cohorts.manifest("selection", _images([bug]), {bug: contrary})
    assert body["usable"] == [] and body["excluded"][0]["why"].startswith("base: not seen to fail")
    for control, change in (("gold patch", ("selfcheck", "gold")), ("empty patch", ("selfcheck", "empty"))):
        record = _passed()
        record[change[0]][change[1]] = contrary[change[0]][change[1]]
        assert cohorts.admit(record, IDS).startswith(control + ": not seen to"), control
    record = _passed()
    record["round_trip"].update(kept_read=False)
    assert cohorts.admit(record, IDS) == "round trip: the kept patch was not read: its capture or its copy was not seen to pass"
    flagged = {**_passed(), "failed": "base", "why": "a caller's word"}     # the flag carries no authority either way
    assert cohorts.admit(flagged, IDS) == ""


@pytest.mark.parametrize("record", [None, {}, {"validate": []}, {"validate": {"fail_to_pass": "t", "pass_to_pass": []}},
                                    {"validate": {"fail_to_pass": ["t"], "pass_to_pass": [], "base": "failed"}},
                                    {**_passed(), "round_trip": {"status": "finished", "known_bytes": 3}},
                                    {**_passed(), "images_after": None}])
def test_a_record_that_cannot_be_read_as_the_controls_write_it_is_never_admitted(record):
    assert cohorts.admit(record, IDS) != ""


def test_a_manifest_over_a_record_in_slice_1s_form_excludes_the_bug_and_names_what_was_never_recorded():
    bug = cohorts.SELECTION[0]
    old = {"failed": "", "why": "", "images": IDS, "validate": {"run": "validate/x/1", "ok": True, "fail_to_pass": 7,
                                                                "pass_to_pass": 3, "oracle_image_id": IDS["oracle_image_id"],
                                                                "base_fails": True},
           "selfcheck": {"run": "selfcheck/x/1", "gold": {"passes": True, "failing": 0}, "empty": {"passes": False, "failing": 7}},
           "round_trip": {"holds": True, "status": "finished", "same_bytes": True}}
    body = cohorts.manifest("selection", _images([bug]), {bug: old})
    assert body["usable"] == [] and body["excluded"][0]["why"] == \
        "base: the lists the maintainers' tests are scored by were never recorded"
    assert body["bugs"][0]["controls"]["validate"]["base"]["state"] == "unknown"


def test_each_control_record_carries_both_ids_it_ran_on_read_before_it_and_again_after_the_last(monkeypatch, tmp_path):
    moved = {**IDS, "task_image_id": "sha256:" + "9" * 64}
    got, _ = _controls(monkeypatch, tmp_path, ids=[IDS, IDS, IDS, moved])
    assert [cohorts.the_ids(got[k]["images"]) for k in ("validate", "selfcheck", "round_trip")] == [IDS, IDS, IDS]
    assert got["images_after"] == moved and cohorts.admit(got, IDS).startswith("the images changed while the controls ran")
    got, _ = _controls(monkeypatch, tmp_path / "2", ids=[IDS, moved, moved, moved])
    assert cohorts.admit(got, IDS) == f"the gold and empty patches ran on other images than these: {moved}"


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
