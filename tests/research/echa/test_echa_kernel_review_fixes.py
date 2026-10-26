# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The independent review's findings on the evidence kernel (2026-10-09), each pinned by a case that failed before the fix.

K-1 a sealed file read through another spelling of its path or a hard link; K-2 an access incident that voids nothing unless a
free-text field happens to match; K-3 a negative control that is not about the check it protects; K-4 a failure outweighed by a
pass; K-5 independence counted as labels; K-7 an episode that breaks the builder's rules still accepted by the ledger; K-8
duplicate keys and extra fields in a ledger file; K-9 a valid ledger that cannot be reopened; K-10 a tail cut that no reader
could be told about; K-11 a refusal that says it recorded an incident when it could not; and the small ones.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from g6.research.echa import (
    FLAG,
    AccessDenied,
    ClaimRecord,
    Episode,
    EvidenceRecord,
    Ledger,
    LedgerCorrupt,
    LedgerError,
    ResearchDisabled,
    StandardRef,
    Vault,
    digest,
    evaluate_claim,
    guarded_read,
    trace_claim,
)
from g6.research.echa.episodes import verify_episode_body

SEAL = "ab" * 32
STD = StandardRef.from_text("std", "A change must not alter a function it was not asked to alter.", "owner doc", ("p1",))


def ev(evidence_id: str, **over) -> EvidenceRecord:
    base = {"evidence_id": evidence_id, "predicate_id": "p1", "oracle_category": "INDEPENDENT_EXECUTABLE",
            "oracle_version": "probe-v1", "fixture_version": SEAL, "independent": True, "independence_group": "oracle-a",
            "independence_rationale": "held outside the adapting process", "result": "pass", "scope": "12 inputs",
            "known_limitations": ("12 inputs only",), "source_ref": f"log:{evidence_id}",
            "standard_sha256": STD.standard_sha256, "negative_control_ref": "ctl", "purpose": "check",
            "oracle_error_bound": None}
    base.update(over)
    return EvidenceRecord(**base)


def control(evidence_id: str = "ctl", **over) -> EvidenceRecord:
    return ev(evidence_id, **{"purpose": "negative_control", "result": "fail", "negative_control_ref": "",
                              "scope": "an injected defect", **over})


def claim(evidence_ids, claim_id: str = "c1", **over) -> ClaimRecord:
    base = {"claim_id": claim_id, "subject": "patch", "predicate_id": "p1", "scope": "12 inputs", "breadth": "covered_cases",
            "estimand": "oracle_scoped", "population": "task", "sample_unit": "one task", "provenance": "sealed fixture",
            "assumptions": ("the oracle is right",), "known_limitations": ("12 inputs",), "versions": {"standard": "std"},
            "evidence_ids": tuple(evidence_ids), "standard_sha256": STD.standard_sha256}
    base.update(over)
    return ClaimRecord(**base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


@pytest.fixture
def led(on):
    ledger = Ledger()
    ledger.add_standard(STD)
    ledger.add_vault("v1", SEAL, files=1, roots=1)
    ledger.add_evidence(control())
    return ledger


# --- K-1: a sealed file is the same file whatever the spelling ----------------------------------------------------------


@pytest.fixture
def world(tmp_path):
    (tmp_path / "hidden").mkdir()
    (tmp_path / "hidden" / "secret.py").write_bytes(b"SECRET")
    (tmp_path / "work").mkdir()
    return tmp_path


def _vault(led, world):
    return Vault.seal([world / "hidden"], ledger=led, vault_id="sealed")


def test_k1_a_prefixed_or_unc_spelling_of_a_sealed_file_is_refused(led, world) -> None:
    if os.name != "nt":
        pytest.skip("these spellings are Windows forms")
    vault = _vault(led, world)
    secret = str((world / "hidden" / "secret.py").resolve())
    spellings = [r"\\?" + "\\" + secret]
    drive, rest = os.path.splitdrive(secret)
    unc = rf"\\localhost\{drive[0]}$" + rest
    if os.path.exists(unc):
        spellings += [unc, r"\\?\UNC\localhost" + "\\" + f"{drive[0]}$" + rest]
    for spelling in spellings:
        with pytest.raises(AccessDenied):
            guarded_read(vault, "builder", spelling, ledger=led)
    assert guarded_read(vault, "evaluator", spellings[0], ledger=led) == b"SECRET"  # the evaluator is not harmed


def test_k1_a_hard_link_made_outside_the_vault_is_the_same_file(led, world) -> None:
    link = world / "work" / "alias.py"
    try:
        os.link(world / "hidden" / "secret.py", link)
    except (OSError, NotImplementedError):
        pytest.skip("hard links are not available here")
    vault = _vault(led, world)
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", link, ledger=led)


def test_k1_the_vault_itself_knows_a_hard_link_is_a_sealed_file(led, world) -> None:
    link = world / "work" / "alias.py"
    try:
        os.link(world / "hidden" / "secret.py", link)
    except (OSError, NotImplementedError):
        pytest.skip("hard links are not available here")
    vault = _vault(led, world)
    assert vault.covers(link)  # by file identity: the link's resolved location is outside every sealed root
    assert not vault.covers(world / "work")


def test_k1_a_file_swapped_in_after_the_membership_check_is_caught_at_the_handle(led, world, monkeypatch) -> None:
    link = world / "work" / "alias.py"
    try:
        os.link(world / "hidden" / "secret.py", link)
    except (OSError, NotImplementedError):
        pytest.skip("hard links are not available here")
    vault = _vault(led, world)
    monkeypatch.setattr(Vault, "covers", lambda self, path: False)  # the path looked innocent when it was checked
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", link, ledger=led)


def test_k1_a_file_added_to_a_sealed_folder_later_is_still_refused(led, world) -> None:
    vault = _vault(led, world)
    (world / "hidden" / "later.py").write_bytes(b"LATE")
    with pytest.raises(AccessDenied):
        guarded_read(vault, "learner", world / "hidden" / "later.py", ledger=led)


def test_k11_a_refusal_with_no_ledger_does_not_claim_it_recorded_an_incident(led, world) -> None:
    vault = _vault(led, world)
    with pytest.raises(AccessDenied) as caught:
        guarded_read(vault, "builder", world / "hidden" / "secret.py")
    assert "incident recorded" not in str(caught.value) and "nothing was recorded" in str(caught.value)
    with pytest.raises(AccessDenied) as logged:
        guarded_read(vault, "builder", world / "hidden" / "secret.py", ledger=led)
    assert "incident recorded" in str(logged.value)


# --- K-2: evidence names the sealed fixture it was taken against --------------------------------------------------------


def test_k2_evidence_must_name_a_vault_the_ledger_holds_or_say_unsealed(led) -> None:
    with pytest.raises(LedgerError, match="fixture"):  # a well-formed digest that no sealed vault in the ledger has
        led.add_evidence(ev("e1", fixture_version="cd" * 32))
    for label in ("fx-made-up-by-the-author", ""):  # a free-text label is not a fixture
        with pytest.raises(ValueError, match="fixture_version"):
            ev("e1", fixture_version=label)
    led.add_evidence(ev("u1", fixture_version="unsealed", independent=False, independence_group="",
                        independence_rationale="", oracle_category="PARTIAL_FALSIFIER", negative_control_ref=""))


def test_k2_an_independent_record_must_name_a_sealed_fixture() -> None:
    with pytest.raises(ValueError, match="sealed"):
        ev("e1", fixture_version="unsealed")


def test_k2_an_access_incident_voids_every_record_taken_against_that_fixture(led) -> None:
    led.add_evidence(ev("e1"))
    led.add_claim(claim(["e1"]))
    assert evaluate_claim(led, "c1")["result"] == "supported"
    led.add_incident("access_denied", fixture_digest=SEAL, role="builder")
    got = evaluate_claim(led, "c1")
    assert got["result"] == "inconclusive" and "contaminated_fixture" in got["reasons"]


# --- K-3: a negative control is about the same check --------------------------------------------------------------------


@pytest.mark.parametrize("over", [{"predicate_id": "p2"}, {"oracle_version": "another-oracle"},
                                  {"oracle_category": "HEURISTIC_REVIEW"}])
def test_k3_a_negative_control_must_be_about_the_same_predicate_and_oracle_and_executable(led, over) -> None:
    led.add_evidence(control("other", **over))
    with pytest.raises(LedgerError, match="negative_control_ref"):
        led.add_evidence(ev("e1", negative_control_ref="other"))


# --- K-4 and K-5: a failure is not outweighed; independence is not labels ----------------------------------------------


def test_k4_a_failing_check_without_a_control_stops_a_pass_from_supporting_the_claim(led) -> None:
    led.add_evidence(ev("e1"))
    led.add_evidence(ev("e2", oracle_category="PARTIAL_FALSIFIER", result="fail", negative_control_ref="",
                        independent=False, independence_group="", independence_rationale=""))
    led.add_claim(claim(["e1", "e2"]))
    got = evaluate_claim(led, "c1")
    assert got["result"] == "inconclusive" and "unexplained_failure" in got["reasons"] and got["leads"] == ["e2"]


def test_k5_two_group_labels_over_one_oracle_are_not_two_independent_groups(led) -> None:
    led.add_evidence(ev("e1", independence_group="a"))
    led.add_evidence(ev("e2", independence_group="b", scope="the same oracle again"))
    led.add_claim(claim(["e1", "e2"], requires_independent_groups=2))
    got = evaluate_claim(led, "c1")
    assert got["independent_groups"] == 1 and got["result"] == "inconclusive"
    led.add_evidence(control("ctl2", oracle_version="probe-v2"))
    led.add_evidence(ev("e3", independence_group="b", oracle_version="probe-v2", negative_control_ref="ctl2"))
    led.add_claim(claim(["e1", "e3"], "c2", requires_independent_groups=2))
    assert evaluate_claim(led, "c2")["independent_groups"] == 2


# --- K-7: the ledger re-applies every episode rule ----------------------------------------------------------------------


def _episode_body() -> dict:
    ep = Episode(episode_id="ep", task_id="t", task_family="f", repository_or_group_id="r", split_id="s",
                 standard_hash=STD.standard_sha256, executor_version="x", harness_version="h", oracle_version="o",
                 component_versions={}, model_id="m", model_configuration={}, cost_budget=0)
    ep.append("forecast", {"status": "not_available"})
    return ep.freeze(candidate_artifact_hash="c" * 64, outcome_state="qualified-draft", evidence_refs=(),
                     known_unknowns=(), run_cost=0, latency_seconds=1).to_dict()


def _reseal(body: dict) -> dict:
    keys = ("episode_id", "task_id", "task_family", "repository_or_group_id", "split_id", "standard_hash", "executor_version",
            "harness_version", "oracle_version", "component_versions", "model_id", "model_configuration", "seed",
            "cost_budget", "independent_oracle_access")
    prev = body["genesis"] = digest({k: body[k] for k in keys})
    for i, event in enumerate(body["events"]):
        event.update(seq=i, prev=prev)
        event["sha256"] = prev = digest({"seq": i, "kind": event["kind"], "detail": event["detail"], "prev": event["prev"]})
    body["head"] = prev
    return body


def _bad_episodes() -> dict:
    out = {}
    body = _episode_body()
    body["outcome_state"] = "verified_ready"
    body["events"][-1]["detail"]["state"] = "verified_ready"
    out["alias"] = _reseal(body)
    body = _episode_body()
    body["events"].append({"kind": "forecast", "detail": {"class": "low"}})
    out["forecast after the outcome"] = _reseal(body)
    body = _episode_body()
    body["events"].append({"kind": "outcome", "detail": {"state": "qualified-draft"}})
    out["two outcomes"] = _reseal(body)
    body = _episode_body()
    body["events"][0]["detail"] = {"output": "x" * 5000}
    out["long text"] = _reseal(body)
    body = _episode_body()
    body["events"][0]["detail"] = {"nested": {"a": "b"}}
    out["nested detail"] = _reseal(body)
    for field, value in (("independent_oracle_access", "yes please"), ("run_cost", math.nan), ("latency_seconds", -5),
                         ("candidate_artifact_hash", "not-a-hash"), ("schema_version", "whatever")):
        body = _episode_body()
        body[field] = value
        out[field] = _reseal(body)
    return out


def test_k7_a_valid_episode_passes_and_every_builder_rule_is_re_applied_to_a_stored_one(on) -> None:
    assert verify_episode_body(_episode_body()) == []
    for name, body in _bad_episodes().items():
        assert verify_episode_body(body), name


def test_k7_the_ledger_refuses_a_hand_built_episode_that_breaks_the_rules(led) -> None:
    from g6.research.echa.episodes import EpisodeRecord

    body = _bad_episodes()["alias"]
    record = EpisodeRecord(header={k: body[k] for k in ("episode_id", "task_id", "task_family", "repository_or_group_id",
                                                        "split_id", "standard_hash", "executor_version", "harness_version",
                                                        "oracle_version", "component_versions", "model_id",
                                                        "model_configuration", "seed", "cost_budget",
                                                        "independent_oracle_access")},
                           genesis=body["genesis"], events=tuple(body["events"]), head=body["head"],
                           candidate_artifact_hash=body["candidate_artifact_hash"], outcome_state=body["outcome_state"],
                           evidence_refs=(), known_unknowns=(), run_cost=0, latency_seconds=1)
    with pytest.raises(LedgerError):
        led.add_episode(record)


# --- K-8, K-9, K-10: the file ------------------------------------------------------------------------------------------


def _file(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.add_standard(STD)
    ledger.add_vault("v1", SEAL, files=1, roots=1)
    ledger.add_evidence(control())
    return path


def test_k8_a_duplicate_key_or_an_extra_field_in_an_entry_is_reported(on, tmp_path) -> None:
    path = _file(tmp_path)
    assert Ledger.audit(path) == ()
    lines = path.read_text(encoding="utf-8").splitlines()
    forged = lines[:]
    forged[0] = '{"body": {"forged": true}, ' + lines[0][1:]
    path.write_text("\n".join(forged) + "\n", encoding="utf-8")
    assert any("duplicate" in p for p in Ledger.audit(path))
    row = json.loads(lines[0])
    row["reviewer_note"] = "independently verified by the founder"
    path.write_text("\n".join([json.dumps(row, sort_keys=True), *lines[1:]]) + "\n", encoding="utf-8")
    assert any("unexpected" in p for p in Ledger.audit(path))


def test_k9_a_standard_with_a_unicode_line_separator_still_reopens(on, tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    odd = StandardRef.from_text("odd", "a line\u2028separator and a next\u0085line", "owner", ("p1",))
    Ledger(path).add_standard(odd)
    assert Ledger.audit(path) == () and Ledger(path).verify() == ()


def test_k10_a_reader_can_hold_the_head_when_opening_and_a_trace_says_whether_it_was_anchored(on, tmp_path) -> None:
    path = _file(tmp_path)
    kept = Ledger(path).head()
    path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[:-1]) + "\n", encoding="utf-8")
    assert Ledger(path).head() != kept  # a plain open cannot know
    with pytest.raises(LedgerCorrupt):
        Ledger(path, expect_head=kept)


def test_k10_the_trace_reports_whether_the_ledger_was_anchored_to_a_kept_head(led) -> None:
    led.add_evidence(ev("e1"))
    led.add_claim(claim(["e1"]))
    assert trace_claim(led, "c1")["anchored"] is False
    anchored = Ledger(expect_head=None)
    assert anchored.expect_head is None


# --- the small ones ---------------------------------------------------------------------------------------------------


def test_every_write_checks_the_flag_even_on_a_ledger_built_while_it_was_on(led, monkeypatch) -> None:
    monkeypatch.delenv(FLAG)
    with pytest.raises(ResearchDisabled):
        led.add_standard(StandardRef.from_text("s2", "another", "p", ()))


def test_an_incident_cannot_be_given_another_type_by_a_field(led) -> None:
    led.add_incident("access_denied", type="note", fixture_digest=SEAL)
    assert SEAL in led.contaminated_fixtures()


def test_an_episode_can_be_frozen_once_and_its_record_does_not_share_the_builders_state(on) -> None:
    ep = Episode(episode_id="ep", task_id="t", task_family="f", repository_or_group_id="r", split_id="s",
                 standard_hash=STD.standard_sha256, executor_version="x", harness_version="h", oracle_version="o",
                 component_versions={"k": "1"}, model_id="m", model_configuration={}, cost_budget=0)
    ep.append("forecast", {"status": "not_available"})
    record = ep.freeze(candidate_artifact_hash="", outcome_state="not_recorded", evidence_refs=(), known_unknowns=(),
                       run_cost=0, latency_seconds=0)
    record.header["component_versions"]["k"] = "tampered"
    record.events[0]["detail"]["status"] = "tampered"
    assert ep._header["component_versions"]["k"] == "1" and ep._events[0]["detail"]["status"] == "not_available"
    with pytest.raises(ValueError, match="frozen"):
        ep.freeze(candidate_artifact_hash="", outcome_state="not_recorded", evidence_refs=(), known_unknowns=(),
                  run_cost=0, latency_seconds=0)


@pytest.mark.parametrize("field", ["run_cost", "latency_seconds"])
def test_a_number_that_is_not_finite_is_refused(on, field) -> None:
    ep = Episode(episode_id="ep", task_id="t", task_family="f", repository_or_group_id="r", split_id="s",
                 standard_hash=STD.standard_sha256, executor_version="x", harness_version="h", oracle_version="o",
                 component_versions={}, model_id="m", model_configuration={}, cost_budget=0)
    ep.append("forecast", {"status": "not_available"})
    args = {"candidate_artifact_hash": "", "outcome_state": "not_recorded", "evidence_refs": (), "known_unknowns": (),
            "run_cost": 0, "latency_seconds": 0, field: math.nan}
    with pytest.raises(ValueError, match=field):
        ep.freeze(**args)


def test_a_ledger_file_that_is_not_utf8_is_reported_not_raised(on, tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"\xff\xfe\x00 not a ledger")
    assert Ledger.audit(path) != ()
    with pytest.raises(LedgerCorrupt):
        Ledger(path)
