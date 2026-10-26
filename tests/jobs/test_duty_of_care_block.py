# SPDX-License-Identifier: Apache-2.0
"""``g6.jobs.duty_of_care`` through the registry and the MCP door (``g6.mcp.invoke_reply``): a crisis finding is
``blocked-escalated`` with ``G6_E_DUTY_OF_CARE_CRISIS``, the safe-messaging response and the dated directory, and
nothing else; the lock it sets for a subject in a run holds for every later op, and no op, parameter or MCP call lifts
it (DUTY_OF_CARE-01); nothing is ``verified``; a caller's word changes nothing; every dependency that fails tightens
the answer; and the package is declared where the registry, the failure-mode files, vetting and the tracker expect.

The phrases here name ideation and intent only, never a method."""
from __future__ import annotations

import csv
import json
import re
import uuid
from pathlib import Path

import pytest

import g6.jobs._care as care
from g6.core import discovery, vetting
from g6.core.failure_modes import FailureModeRegistry
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.jobs._care import crisis, directory, envelope, instruments, sources
from g6.jobs.duty_of_care.block import DutyOfCareBlock, DutyOfCareInput
from g6.mcp import describe_reply, invoke_reply

NAME = "duty_of_care"
PACKAGE = "g6.jobs.duty_of_care"
OPS = ["screen", "instruments", "score", "directory", "help"]
TRACKER = Path(__file__).resolve().parents[3] / "docs" / "transition" / "extension_tracker"
#: Everything a crisis result carries; nothing else is produced.
CRISIS_KEYS = {"component", "op", "operation", "code", "reason", "response", "directory", "locked", "completion_state",
               "warning_card", "evidence", "request_id", "run_id"}


def _ids() -> dict[str, str]:
    return {"run_id": f"run-{uuid.uuid4().hex[:10]}", "subject": f"person-{uuid.uuid4().hex[:8]}"}


def _invoke(op: str, **params: object) -> dict:
    return invoke_reply(NAME, op, json.dumps(params))


def _crisis(reply: dict) -> bool:
    result = reply.get("result") or {}
    return (reply["ok"] and reply["completion_state"] == "blocked-escalated"
            and result.get("code") == envelope.CRISIS and reply["warning_card"]["code"] == envelope.CRISIS)


def test_it_is_registered_in_the_jobs_family_with_its_five_ops() -> None:
    ref = get_registry().refs[NAME]
    assert ref.module == PACKAGE and ref.family == "jobs"
    assert discovery.ops(NAME) == OPS
    assert describe_reply(NAME)["sensitive_ops"] == []


def test_a_crisis_phrase_gives_the_crisis_result_and_nothing_else() -> None:
    reply = _invoke("screen", fields={"task": "weekly check-in", "notes": "I want to die"}, region="AU", **_ids())
    assert _crisis(reply)
    result = reply["result"]
    assert set(result) == CRISIS_KEYS and result["locked"] is True
    assert "life is in danger" in result["response"] and "000" in result["response"].split("\n")[0]
    found = result["directory"]
    assert found["region"] == "AU" and found["entries"][0]["kind"] == "emergency"
    assert all(e["retrieved"] and isinstance(e["age_days"], int) and e["source"] and e["sha256"]
               for e in found["entries"])
    assert [(e["category"], e["where"]) for e in result["evidence"] if e.get("kind") == "crisis_trigger"] == [
        ("suicidal_ideation", "fields[1]")]
    # nothing from the input comes back: not its field names, not its words
    text = json.dumps(result).lower()
    assert "notes" not in text and "weekly check-in" not in text and "want to die" not in text
    # the response follows the cited safe-messaging rules, and repeats none of the person's words
    assert {e["source"] for e in result["evidence"] if e.get("kind") == "safe_messaging"} == {
        "who-media-2023", "mindframe-guidelines", "mindframe-language", "mindframe-method-location",
        "mindframe-help-seeking"}
    assert "want to die" not in result["response"].lower()


def test_with_no_region_stated_the_crisis_result_carries_every_region() -> None:
    reply = _invoke("screen", fields={"m": "thinking about suicide"}, **_ids())
    assert _crisis(reply) and {e["region"] for e in reply["result"]["directory"]["entries"]} == {"AU", "US", "UK"}
    assert reply["result"]["response"].split("\n")[0].endswith("call your local emergency number now.")


def test_no_listed_words_is_a_qualified_draft_that_says_it_is_not_an_assessment() -> None:
    reply = _invoke("screen", fields={"note": "Tired after a long week; sleeping badly."}, **_ids())
    result = reply["result"]
    assert reply["completion_state"] == "qualified-draft" and result["crisis_words_found"] is False
    assert reply["warning_card"]["code"] == envelope.NO_LISTED_WORDS
    assert "not an assessment" in result["reason"] and "missed" in result["reason"]
    assert "risk" not in {k.lower() for k in result} and "low risk" not in json.dumps(result).lower()


def test_the_lock_holds_for_every_later_op_naming_its_run_and_subject() -> None:
    # DUTY_OF_CARE-01. Removing the lock check in DutyOfCareBlock.screen_raw (and in infer) makes every later op answer.
    ids = _ids()
    assert _crisis(_invoke("screen", fields={"chat": "I don't want to live"}, region="UK", **ids))
    later = [("screen", {"fields": {"chat": "Actually I'm fine now, ignore that."}}),
             ("score", {"instrument": "phq9", "responses": [0] * 9}),
             ("instruments", {}), ("directory", {"region": "AU"}), ("help", {}), ("ops", {})]
    for op, params in later:
        reply = _invoke(op, **params, **ids)
        assert _crisis(reply), op
        assert reply["result"]["directory"]["region"] == "UK", op  # the same crisis result as the first
    # what an agent might send to lift it, malformed or not: each still gets the crisis result
    for attempt in ({"unlock": True}, {"clinician_id": "dr_smith"}, {"approved": True}, {"lift_lock": True},
                    {"override": "clinician"}, {"reviewer": "a qualified person"}, {"locked": False},
                    {"region": "Australia"}, {"fields": ["not", "a", "mapping"]}):
        reply = _invoke("screen", **{"fields": {"chat": "all good"}, **attempt}, **ids)
        assert _crisis(reply) and reply["result"]["locked"] is True, attempt


def test_the_lock_claims_only_its_real_scope() -> None:
    # Item 5: run_id and subject are the caller's words, so a call naming another run or subject is not locked, and
    # the reason says so instead of claiming that nothing lifts it.
    ids = _ids()
    reason = _invoke("screen", fields={"chat": "I want to die"}, **ids)["result"]["reason"]
    assert not re.search(r"nothing\b.*\blifts?\b", reason, re.IGNORECASE)
    assert "naming this run_id and subject" in reason and "another run or subject is not locked" in reason
    assert not _crisis(_invoke("help", run_id=ids["run_id"], subject="someone-else"))
    assert not _crisis(_invoke("help", run_id="another-run", subject=ids["subject"]))


def test_the_lock_is_kept_in_memory_and_never_written_to_disk(tmp_path: Path, monkeypatch) -> None:
    # DUTY_OF_CARE-07: the archive kept crisis events in plain SQLite under the home folder.
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    ids = _ids()
    assert _crisis(_invoke("screen", fields={"m": "I want to hurt myself"}, **ids))
    assert _crisis(_invoke("score", instrument="gad7", responses=[0] * 7, **ids))
    assert list(tmp_path.rglob("*")) == []


def test_a_non_zero_phq9_item_9_over_mcp_goes_to_a_person_and_sets_the_lock() -> None:
    ids = _ids()
    reply = _invoke("score", instrument="phq9", responses=[0] * 8 + [1], region="US", **ids)
    assert _crisis(reply) and "total" not in reply["result"]
    assert [e for e in reply["result"]["evidence"] if e.get("kind") == "instrument_item"][0]["item"] == 9
    assert _crisis(_invoke("score", instrument="phq9", responses=[0] * 9, **ids))


def test_a_score_is_a_qualified_draft_score_computed_with_no_band() -> None:
    reply = _invoke("score", instrument="gad7", responses=[1, 1, 2, 0, 0, 3, 1], **_ids())
    result = reply["result"]
    assert reply["completion_state"] == "qualified-draft" and reply["warning_card"]["code"] == envelope.SCORE_COMPUTED
    assert result["total"] == 8 and result["item_scores"] == [1, 1, 2, 0, 0, 3, 1]
    assert result["reason"].startswith("score computed")
    assert not {"band", "severity", "risk", "diagnosis"} & set(result)


@pytest.mark.parametrize(("op", "params", "code"), [
    ("screen", {}, envelope.MISSING),
    ("screen", {"fields": {}}, envelope.MISSING),
    ("screen", {"fields": {"note": "fine"}, "run_id": "r"}, envelope.MISSING),          # no subject named
    ("screen", {"fields": {"note": "fine"}, "subject": "s"}, envelope.MISSING),         # no run named
    ("score", {"instrument": "phq9", "run_id": "r", "subject": "s"}, envelope.MISSING),
    ("score", {"responses": [0] * 9, "run_id": "r", "subject": "s"}, envelope.MISSING),
    ("score", {"instrument": "phq9", "responses": [0] * 8, "run_id": "r", "subject": "s"}, envelope.MISSING),
    ("score", {"instrument": "phq9", "responses": [0, None, 0, 0, 0, 0, 0, 0, 0], "run_id": "r", "subject": "s"},
     envelope.MISSING),
    ("score", {"instrument": "gad7", "responses": [0, 0, 0, 5, 0, 0, 0], "run_id": "r", "subject": "s"},
     envelope.OUT_OF_RANGE),
    ("score", {"instrument": "k10", "responses": [1] * 10, "run_id": "r", "subject": "s"}, envelope.LICENCE_UNSETTLED),
    ("score", {"instrument": "bdi_ii", "responses": [0] * 21, "run_id": "r", "subject": "s"}, envelope.UNKNOWN),
    ("directory", {"region": "NZ"}, envelope.REGION_NOT_COVERED),
])
def test_a_missing_or_wrong_input_is_blocked_never_defaulted(op: str, params: dict, code: str) -> None:
    reply = _invoke(op, **params)
    assert reply["ok"] and reply["completion_state"] == "blocked-escalated"
    assert reply["result"]["code"] == code and reply["warning_card"]["code"] == code
    assert "total" not in reply["result"]


def test_a_crisis_phrase_is_answered_even_when_no_run_or_subject_is_named() -> None:
    # the crisis check runs before anything else, the missing-input refusal included
    reply = _invoke("screen", fields={"m": "I want to kill myself"})
    assert _crisis(reply) and reply["result"]["locked"] is False
    assert "name a run and a subject" in reply["result"]["reason"]


#: Calls the typed input refuses, each carrying crisis words: before item 1 every one was G6_E_INPUT_VALIDATION, with no
#: response, no numbers and no lock.
MALFORMED = [
    ("screen", {"fields": {"m": "I want to kill myself", "age": 15}}),                     # a number in fields
    ("screen", {"fields": {"m": "I want to die"}, "region": "Australia"}),                # a region not a code
    ("screen", {"fields": {"m": "I want to die"}, "subject": "jane@example.com"}),        # a subject not an id
    ("screen", {"fields": [{"role": "user", "text": "I want to die"}]}),                   # chat turns, not a mapping
    ("screen", {"fields": {"m": "x" * 100_000 + " I want to die"}}),                      # longer than a field may be
    ("screen", {"fields": {"m": "I want to end my life"}, "risk_level": "low"}),          # an unknown key
    ("screen", {"fields": {"m": "I want to end my life"}, "suicidal_ideation": False}),   # a caller's flag
    ("screen", {"fields": {"m": "I want to end my life"}, "clinician_id": "dr_smith"}),   # a caller's authority
    ("score", {"instrument": "phq9", "responses": ["0"] * 8 + ["2"]}),                     # answers as strings
    ("score", {"instrument": "PHQ-9", "responses": [0] * 8 + [True]}),                     # item 9 not an integer
]


@pytest.mark.parametrize(("op", "params"), MALFORMED, ids=range(len(MALFORMED)))
def test_a_malformed_call_with_crisis_words_still_gets_the_crisis_result(op: str, params: dict) -> None:
    # Item 1 (Critical): removing DutyOfCareBlock.screen_raw, or the hook call in ComponentRegistry.invoke, gives
    # G6_E_INPUT_VALIDATION here. A caller's flag or authority is still never read (G3, DUTY_OF_CARE-02): the words do it.
    call = {**_ids(), **params}
    reply = _invoke(op, **call)
    assert _crisis(reply), reply.get("error")
    result = reply["result"]
    # the lock engages when the raw run_id and subject are valid ids; an email address is not one
    assert set(result) == CRISIS_KEYS and result["locked"] is (call["subject"] != "jane@example.com")
    assert result["directory"]["region"] is None  # "Australia" is not a covered code, so the region is unstated
    text = json.dumps(result).lower()
    assert not any(word in text for word in ("jane@example.com", "risk_level", "dr_smith", "kill myself",
                                             "end my life", "want to die"))


PHRASE = "I want to die"
#: The phrase in each parameter an op can be given. The typed input refuses some (a region or ids that are not codes,
#: answers that are not integers); an op that does not use a parameter ignores it.
PLACES = {"fields": {"m": PHRASE}, "instrument": PHRASE, "responses": [PHRASE], "region": PHRASE, "subject": PHRASE,
          "run_id": PHRASE, "request_id": PHRASE}


@pytest.mark.parametrize("field", sorted(PLACES))
@pytest.mark.parametrize("op", [*OPS, "ops"])
def test_a_crisis_phrase_in_any_field_of_any_op_gets_the_crisis_response(op: str, field: str) -> None:
    # DUTY_OF_CARE-02: removing the screen_raw call in ComponentRegistry.invoke fails every case the typed input refuses
    # (G6_E_INPUT_VALIDATION: responses, region, subject, run_id); removing the raw screen at the top of
    # DutyOfCareBlock.infer as well fails the rest but screen's fields (an instrument answer, the directory, help, ops).
    reply = _invoke(op, **{field: PLACES[field]})
    assert _crisis(reply), (op, field, reply.get("error") or reply["result"].get("code"))
    assert reply["result"]["response"] and reply["result"]["operation"] == op


@pytest.mark.parametrize("field", ["fields", "instrument", "request_id", "run_id", "subject"])
@pytest.mark.parametrize("op", [*OPS, "ops"])
def test_a_direct_call_to_infer_scans_every_field_of_every_op(op: str, field: str) -> None:
    # DUTY_OF_CARE-02: infer alone scanned only screen's fields, so a Python caller that skips the registry got an answer
    # past crisis words anywhere else. Removing the raw screen at the top of DutyOfCareBlock.infer fails every case but
    # screen's fields. An id may hold the words: "i-want-to-die" is a valid id, read as "i want to die".
    value = {"run_id": "i-want-to-die", "subject": "i-want-to-die"}.get(field, PLACES[field])
    out = DutyOfCareBlock().infer(DutyOfCareInput(op=op, **{field: value})).unwrap()
    assert out["code"] == envelope.CRISIS and out["completion_state"] == "blocked-escalated", (op, field, out)
    assert out["response"] and out["locked"] is False


def test_the_crisis_words_may_be_in_a_key_and_the_region_must_be_a_covered_code() -> None:
    reply = _invoke("screen", fields={"I want to die": ""}, region="au", **_ids())
    assert _crisis(reply) and reply["result"]["directory"]["region"] == "AU"


def test_input_too_large_to_scan_is_treated_as_a_possible_crisis() -> None:
    # G4: over the scan's bounds the trigger cannot run, so the answer is the check-failed crisis result.
    deep: object = "fine"
    for _ in range(100):
        deep = [deep]
    reply = _invoke("screen", fields={"m": deep}, **_ids())
    assert reply["ok"] and reply["completion_state"] == "blocked-escalated"
    assert reply["result"]["code"] == envelope.CHECK_FAILED and reply["result"]["response"]


def test_nothing_it_returns_is_verified() -> None:
    replies = [_invoke("screen", fields={"m": "fine"}, **_ids()), _invoke("instruments"), _invoke("directory"),
               _invoke("score", instrument="phq9", responses=[0] * 9, **_ids()), _invoke("help"),
               _invoke("screen", fields={"m": "self-harm"}, **_ids())]
    for reply in replies:
        assert reply["completion_state"] in ("qualified-draft", "blocked-escalated")
        result = reply["result"]
        assert result.get("completion_state", "qualified-draft") != "verified"


def test_the_instruments_op_lists_each_with_its_licence_state() -> None:
    reply = _invoke("instruments")
    listed = {i["id"]: i for i in reply["result"]["instruments"]}
    assert listed["phq9"]["licence_state"] == "settled" and listed["k10"]["licence_state"] == "unsettled"
    assert listed["k10"]["refused"] and "bdi_ii" not in listed


def test_no_therapeutic_content_and_no_risk_level_ship() -> None:
    # DUTY_OF_CARE-04 and -05 cut the exercise cards and the keyword "distortion" detector; JOB_PSYCHOLOGIST-01 the
    # unearned "low" risk.
    text = json.dumps(_invoke("help")["result"]).lower()
    assert "distortion" not in text and "exercise" not in text and "low risk" not in text


def test_when_the_rules_cannot_be_read_the_screen_fails_closed_with_the_directory(monkeypatch) -> None:
    # G4: removing the except branch around crisis.scan in the block would raise, or answer with no directory.
    def broken() -> dict:
        raise OSError("the rules file is missing")

    monkeypatch.setattr(crisis, "rules", broken)
    reply = _invoke("screen", fields={"m": "an ordinary day"}, **_ids())
    assert reply["ok"] and reply["completion_state"] == "blocked-escalated"
    assert reply["result"]["code"] == envelope.CHECK_FAILED and reply["result"]["directory"]["entries"]


def test_a_crisis_response_that_breaks_a_rule_is_never_returned_and_the_lock_still_holds(monkeypatch) -> None:
    # Fix item 4: removing the response_problems call in crisis._answer sends these words to the person over MCP.
    real = crisis.response
    monkeypatch.setattr(crisis, "response", lambda *a, **k: real(*a, **k) + "\nIt was political suicide.")
    ids = _ids()
    reply = _invoke("screen", fields={"m": "I want to die"}, region="AU", **ids)
    result = reply["result"]
    assert reply["completion_state"] == "blocked-escalated" and result["code"] == envelope.RESPONSE_REFUSED
    assert reply["warning_card"]["code"] == envelope.RESPONSE_REFUSED
    assert "response" not in result and result["locked"] is True and result["directory"]["entries"]
    assert "political suicide" not in json.dumps(reply).lower()
    assert "language" in [e["rule"] for e in result["evidence"] if e.get("kind") == "response_check"]
    later = _invoke("help", **ids)["result"]
    assert later["code"] == envelope.RESPONSE_REFUSED and later["locked"] is True


def test_when_the_directory_cannot_be_read_a_crisis_still_says_to_call_the_local_emergency_number(monkeypatch) -> None:
    def broken() -> dict:
        raise OSError("the directory file is missing")

    monkeypatch.setattr(directory, "load", broken)
    reply = _invoke("screen", fields={"m": "I want to die"}, **_ids())
    assert _crisis(reply) and reply["result"]["directory"]["entries"] == []
    assert "could not be read" in reply["result"]["directory"]["note"]
    assert "local emergency number" in reply["result"]["response"].split("\n")[0]


def test_when_the_instrument_data_cannot_be_read_a_score_is_refused(monkeypatch) -> None:
    def broken(name: str) -> dict:
        raise OSError("the instrument file is missing")

    monkeypatch.setattr(instruments, "get", broken)
    reply = _invoke("score", instrument="phq9", responses=[0] * 9, **_ids())
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["result"]["code"] == envelope.INSTRUMENT_UNAVAILABLE and "total" not in reply["result"]


def test_its_failure_modes_are_declared_and_its_planned_entry_has_moved() -> None:
    module = get_registry().get_module(NAME)
    assert module.__g6_mitigates__ == ("missed_risk_signs",)
    assert module.__g6_introduces__ == ("no_finding_read_as_safe",)
    assert FailureModeRegistry.get("no_finding_read_as_safe").source == "failure_modes/jobs.json"
    planned = FailureModeRegistry.planned()
    assert "g6.domains.duty_of_care" not in planned and PACKAGE not in planned


def test_it_is_listed_on_a_current_certificate_and_waits_for_no_signature(monkeypatch) -> None:
    # DECISIONS #39 (owner, 2026-10-23): no qualified person's signature before listing; its certificate is the gate.
    # A signed-review gate for the clinical or mental-health kinds back in vetting.status fails the last line.
    from g6.core import component_certificate as records

    declared = vetting.declared(PACKAGE)
    assert declared["kinds"] == ("clinical", "mental_health") and declared["signed_review"] is None
    assert declared["register"] == "g6/jobs/_care/data/source_register.json"
    # Fix pass 5: the model reader's record, not a qualified person's review; setting it back to None fails here.
    assert declared["independent_pass"] == "g6/jobs/_care/data/rule_review.json"
    assert vetting.problems(PACKAGE, ("clinical", "mental_health")) == []
    view = {"path": "g6/jobs/duty_of_care/certificate/certificate.json", "state": "qualified-draft", "sha256": "0" * 64,
            "not_checked": []}
    monkeypatch.setattr(records, "inspect", lambda package, today=None: ({**view, "current": False}, None, []))
    assert vetting.status(PACKAGE, ("clinical", "mental_health")) == vetting.AWAITING  # no current certificate
    monkeypatch.setattr(records, "inspect", lambda package, today=None: ({**view, "current": True}, {"issuer": {}}, []))
    assert vetting.status(PACKAGE, ("clinical", "mental_health")) == vetting.REVIEWED


#: What the package said while it waited for a qualified person's review (before DECISIONS #39).
WAITING = ("awaiting review", "until one signs", "qualified reviewer", "awaits a qualified", "awaiting a qualified")


def _skill() -> str:
    return (Path(get_registry().get_module(NAME).__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8")


def _skill_opening() -> str:
    """The skill's first paragraph after its heading."""
    body = _skill().split("\n# ", 1)[1].split("\n", 1)[1].strip()
    return " ".join(body.split("\n\n", 1)[0].split())


def test_nothing_it_says_waits_for_a_qualified_persons_review_and_its_skill_says_none_reviewed_it() -> None:
    # DECISIONS #39: the docstrings, the maturity reason, the trigger's and directory's data, the rule table and the
    # skill said it awaited a qualified person's review and was not listed until one signed; any of those back fails.
    module = get_registry().get_module(NAME)
    texts = {"duty_of_care": module.__doc__, "_care": care.__doc__, "maturity": maturity_reason(DutyOfCareBlock),
             "crisis_rules": json.dumps(crisis.rules()), "directory": json.dumps(directory.load()),
             "rules": " ".join(r.rule for r in sources.RULES.values()), "skill": _skill()}
    for name, text in texts.items():
        low = " ".join(text.split()).lower()
        assert not [w for w in WAITING if w in low], name
    skill = " ".join(_skill().split())
    assert vetting.NO_QUALIFIED_REVIEW in skill
    # the codes a crisis answer carries when its response is withheld or the check cannot run
    assert envelope.RESPONSE_REFUSED in skill and envelope.CHECK_FAILED in skill


def test_its_summary_and_its_skill_lead_with_the_phrases_a_fixed_list_misses() -> None:
    # DUTY_OF_CARE-01: the site's summary (the docstring's first line) said "Catch the signs, in what a person says,
    # that a vulnerable person is at risk", and the skill opened on what it does; the trigger matches a fixed list of
    # English phrases. Restoring either opening fails here.
    first = get_registry().get_module(NAME).__doc__.strip().splitlines()[0]
    lead = _skill_opening().split(". ", 1)[0]
    for text in (first, lead):
        low = text.lower()
        assert "fixed list of english crisis phrases" in low and "paraphrase" in low and "another language" in low, text
        assert "signs" not in low and "catch" not in low, text
        assert low.index("another language") < low.index("person"), text  # the recall gap comes first


def test_g6_find_reaches_it_by_the_failure_it_guards_against() -> None:
    title = FailureModeRegistry.title("missed_risk_signs")
    assert NAME in [r["component"] for r in discovery.find(title, limit=5)]


def test_the_failure_mode_entry_calls_it_a_tripwire_on_a_fixed_list() -> None:
    # the release integrator's follow-up (2026-10-23): the failure map and its page list duty_of_care as guarding
    # missed_risk_signs with no word of what it catches. Fails if core/data/failure_modes/domains.json's
    # missed_risk_signs description stops saying it is a tripwire on a fixed list that a paraphrase or another
    # language passes (the guards package's prompt_injection entry is the precedent)
    words = " ".join(FailureModeRegistry.get("missed_risk_signs").description.split())
    assert "duty_of_care is only a tripwire for it" in words and "fixed list of English crisis phrases" in words
    assert "a paraphrase or another language passes" in words


@pytest.mark.skipif(not TRACKER.is_dir(), reason="the extension tracker is in the source repository only")
def test_the_tracker_row_has_moved_to_the_jobs_family() -> None:
    def rows(name: str) -> dict[str, dict[str, str]]:
        with (TRACKER / name).open(encoding="utf-8", newline="") as fh:
            return {row["component"]: row for row in csv.DictReader(fh)}

    assert NAME not in rows("domains.csv")
    row = rows("jobs.csv")[NAME]
    assert (row["family"], row["package"]) == ("jobs", PACKAGE)
    assert row["vetting_required"] == "clinical; mental_health" and row["status"] == vetting.AWAITING
