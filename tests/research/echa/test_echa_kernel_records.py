# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA Checkpoint 1 (founder decision A1, 2026-10-09): the evidence kernel's records, ledger and episode trace.

Research code only, default OFF. What must hold:
- with the flag off nothing is created and nothing is written, and no product module can reach the kernel;
- an unknown result is never a pass, and a record that claims a result its category cannot give is refused;
- every record is content-hashed the same way (canonical JSON, sha256), and the ledger is a hash chain, so an edited,
  removed or reordered entry is found by ``verify``;
- an episode's events are append-only and chained, a forecast cannot be written after the outcome, and the outcome is
  one of the existing wire states (no alias).
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from g6.research.echa import (
    FLAG,
    Episode,
    EvidenceRecord,
    Ledger,
    LedgerCorrupt,
    LedgerError,
    ResearchDisabled,
    StandardRef,
    digest,
    research_enabled,
)
from g6.research.echa import records as kernel_records

import g6 as _g6

SRC = Path(_g6.__file__).resolve().parent
KERNEL = SRC / "research" / "echa"

FX = "ab" * 32  # the digest of a sealed vault
STD = StandardRef.from_text("std-keep-helpers", "A change must not alter a function it was not asked to alter.",
                            "owner-approved task document", ("p-keep-helpers",))


def ev(evidence_id: str = "e1", **over) -> EvidenceRecord:
    base = {"evidence_id": evidence_id, "predicate_id": "p-keep-helpers", "oracle_category": "INDEPENDENT_EXECUTABLE",
                "oracle_version": "probe-v1", "fixture_version": FX, "independent": True, "independence_group": "oracle-a",
                "independence_rationale": "held outside the adapting process", "result": "pass", "scope": "12 held-out inputs",
                "known_limitations": ("covers only the 12 inputs",), "source_ref": "log:1",
                "standard_sha256": STD.standard_sha256, "negative_control_ref": "", "purpose": "check",
                "oracle_error_bound": None}
    base.update(over)
    return EvidenceRecord(**base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


@pytest.fixture
def led(on):
    ledger = Ledger()
    ledger.add_standard(STD)
    ledger.add_vault("v1", FX, files=1, roots=1)
    return ledger


def _episode(**over) -> Episode:
    base = {"episode_id": "ep1", "task_id": "t1", "task_family": "collateral-edit", "repository_or_group_id": "repo-a",
                "split_id": "dev", "standard_hash": STD.standard_sha256, "executor_version": "x1", "harness_version": "h1",
                "oracle_version": "o1", "component_versions": {"differential_probe": "1"}, "model_id": "local-model",
                "model_configuration": {"temperature": 0}, "cost_budget": 0}
    base.update(over)
    return Episode(**base)


# --- the flag: default off, and nothing happens when it is off -------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "enabled", "2", " maybe "])
def test_the_flag_is_off_unless_it_says_a_plain_yes(monkeypatch, value) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    assert research_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_the_flag_is_on_for_a_plain_yes(monkeypatch, value) -> None:
    monkeypatch.setenv(FLAG, value)
    assert research_enabled() is True


def test_with_the_flag_off_every_entry_point_refuses_and_writes_nothing(monkeypatch, tmp_path) -> None:
    from g6.research.echa import (
        Vault,
        evaluate_claim,
        guarded_read,
        trace_claim,
    )

    monkeypatch.delenv(FLAG, raising=False)
    target = tmp_path / "ledger.jsonl"
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "t.py").write_text("x", encoding="utf-8")
    calls = [lambda: Ledger(target), lambda: Ledger(), lambda: _episode(),
             lambda: Vault.seal([hidden], ledger=None), lambda: guarded_read(None, "builder", hidden / "t.py"),
             lambda: evaluate_claim(None, "c1"), lambda: trace_claim(None, "c1")]
    for call in calls:
        with pytest.raises(ResearchDisabled):
            call()
    assert not target.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hidden"]


def test_the_flag_gate_can_fail(monkeypatch, tmp_path) -> None:
    # survival control: with the flag on the same call succeeds, so the refusals above come from the flag
    monkeypatch.setenv(FLAG, "1")
    target = tmp_path / "ledger.jsonl"
    Ledger(target).add_standard(STD)
    assert target.exists()


def _imports(source: str) -> set[str]:
    """Full dotted module names imported at absolute level."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


# The chain and the vault graduated into product code (owner ruling Q5, DECISIONS #26; audit 037). The kernel builds on
# exactly these two stdlib-only modules and imports nothing else from the product.
GRADUATED = frozenset({"g6.core.ledger", "g6.core.vault"})


def _outside_the_kernel(directory: Path) -> set[str]:
    """Imports that are neither the standard library, the kernel itself, nor a graduated module."""
    import sys

    found: set[str] = set()
    for path in directory.rglob("*.py"):
        for name in _imports(path.read_text(encoding="utf-8")):
            top = name.split(".")[0]
            if (top in sys.stdlib_module_names or top == "__future__" or name.startswith("g6.research.echa")
                    or name in GRADUATED):
                continue
            found.add(name)
    return found


def test_the_kernel_imports_nothing_from_the_product(tmp_path) -> None:
    assert _outside_the_kernel(KERNEL) == set()
    # what the kernel does import from the product is exactly the graduated modules, so the allowance is not idle
    assert {n for p in KERNEL.rglob("*.py") for n in _imports(p.read_text(encoding="utf-8"))} & GRADUATED == GRADUATED
    # survival control: the scan finds a product import when one is planted
    (tmp_path / "bad.py").write_text("from g6.venture import harness\nimport requests\n", encoding="utf-8")
    assert _outside_the_kernel(tmp_path) == {"g6.venture", "requests"}


def _reaches_the_kernel(directory: Path) -> list[str]:
    hits = []
    for path in directory.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "g6.research.echa" in text or FLAG in text:
            hits.append(str(path))
    return hits


def test_no_product_module_reaches_the_kernel_or_its_flag(tmp_path) -> None:
    for folder in SRC.iterdir():
        if folder.is_dir() and folder.name not in {"research", "__pycache__"}:
            assert _reaches_the_kernel(folder) == [], folder.name
    # survival control
    (tmp_path / "wired.py").write_text("from g6.research.echa import Ledger\n", encoding="utf-8")
    assert len(_reaches_the_kernel(tmp_path)) == 1


# --- hashing ----------------------------------------------------------------------------------------------------------


def test_a_record_is_hashed_as_canonical_json_whatever_the_key_order() -> None:
    body = {"b": [1, 2], "a": {"y": 1, "x": "é"}}
    expect = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    assert digest(body) == expect
    assert digest({"a": {"x": "é", "y": 1}, "b": [1, 2]}) == expect
    assert digest({"a": 1}) != digest({"a": 2})


def test_an_evidence_record_hash_changes_with_any_field() -> None:
    base = ev()
    assert base.sha256 == ev().sha256
    for field, value in (("result", "fail"), ("scope", "other scope"), ("oracle_version", "probe-v2"),
                         ("independence_group", "oracle-b"), ("fixture_version", "cd" * 32), ("source_ref", "log:2")):
        assert ev(**{field: value}).sha256 != base.sha256, field


# --- the records refuse what the claim rule forbids -------------------------------------------------------------------


def test_no_discriminating_evidence_can_never_read_pass_or_fail() -> None:
    for result in ("pass", "fail"):
        with pytest.raises(ValueError, match="NO_DISCRIMINATING_EVIDENCE"):
            ev(oracle_category="NO_DISCRIMINATING_EVIDENCE", result=result, independent=False, independence_group="",
               independence_rationale="")
    kept = ev(oracle_category="NO_DISCRIMINATING_EVIDENCE", result="unknown", independent=False, independence_group="",
              independence_rationale="", scope="no test covers the helper")
    assert kept.result == "unknown"


@pytest.mark.parametrize("over,needle", [
    ({"result": "passed"}, "result"),
    ({"oracle_category": "TRUSTED_ORACLE"}, "oracle_category"),
    ({"purpose": "decoration"}, "purpose"),
    ({"scope": ""}, "scope"),
    ({"source_ref": ""}, "source_ref"),
    ({"independence_rationale": ""}, "independence_rationale"),
    ({"oracle_error_bound": 1.5}, "oracle_error_bound"),
    ({"oracle_error_bound": -0.1}, "oracle_error_bound"),
    ({"oracle_error_bound": True}, "oracle_error_bound"),
    ({"known_limitations": "covers little"}, "known_limitations"),
])
def test_an_evidence_record_that_leaves_out_what_the_rule_requires_is_refused(over, needle) -> None:
    with pytest.raises(ValueError, match=needle):
        ev(**over)


def test_an_author_controlled_check_cannot_be_recorded_as_an_independent_executable() -> None:
    with pytest.raises(ValueError, match="independent"):
        ev(oracle_category="INDEPENDENT_EXECUTABLE", independent=False, independence_group="",
           independence_rationale="the adapting agent wrote it")
    kept = ev(oracle_category="PARTIAL_FALSIFIER", independent=False, independence_group="",
              independence_rationale="the adapting agent wrote it")
    assert kept.independent is False


def test_an_independent_record_names_its_group() -> None:
    with pytest.raises(ValueError, match="independence_group"):
        ev(independence_group="")


def test_a_not_applicable_record_must_say_why() -> None:
    with pytest.raises(ValueError, match="scope"):
        ev(result="not_applicable", scope="", oracle_category="NO_DISCRIMINATING_EVIDENCE", independent=False,
           independence_group="", independence_rationale="")
    ok = ev(result="not_applicable", scope="the step is not code", oracle_category="NO_DISCRIMINATING_EVIDENCE",
            independent=False, independence_group="", independence_rationale="")
    assert ok.result == "not_applicable"


def test_a_standard_hash_is_the_hash_of_its_text() -> None:
    assert STD.standard_sha256 == hashlib.sha256(
        b"A change must not alter a function it was not asked to alter.").hexdigest()
    assert StandardRef.from_text("s", "other", "p", ()).standard_sha256 != STD.standard_sha256


def test_a_standard_built_with_a_hash_that_is_not_its_text_is_refused() -> None:
    with pytest.raises(ValueError, match="hash of the standard"):
        StandardRef("s", "the text", "provenance", ("p",), "0" * 64)
    with pytest.raises(ValueError, match="sha256"):
        StandardRef("s", "the text", "provenance", ("p",), "not-a-hash")


def test_every_record_type_refuses_a_schema_version_it_does_not_know() -> None:
    from g6.research.echa import ClaimRecord

    with pytest.raises(ValueError, match="schema_version"):
        StandardRef("s", "t", "p", (), digest("t"), schema_version="echa.v9")
    body = {"claim_id": "c", "subject": "s", "predicate_id": "p", "scope": "x", "breadth": "covered_cases",
            "estimand": "oracle_scoped", "population": "p", "sample_unit": "u", "provenance": "p",
            "assumptions": ["a"], "known_limitations": ["l"], "versions": {"v": "1"}, "evidence_ids": ["e"],
            "standard_sha256": STD.standard_sha256}
    assert ClaimRecord(**body).claim_id == "c"
    with pytest.raises(ValueError, match="schema_version"):
        ClaimRecord(**body, schema_version="echa.v9")


def test_the_schema_version_is_part_of_the_record() -> None:
    assert kernel_records.SCHEMA == "echa.v1"
    assert ev().to_dict()["schema_version"] == "echa.v1"
    with pytest.raises(ValueError, match="schema_version"):
        ev(schema_version="echa.v9")


# --- the ledger: references, duplicates, and a chain that notices tampering ------------------------------------------


def test_evidence_must_name_a_standard_the_ledger_holds(on) -> None:
    ledger = Ledger()
    with pytest.raises(LedgerError, match="standard"):
        ledger.add_evidence(ev())


def test_a_negative_control_must_already_be_in_the_ledger_and_must_be_one(led) -> None:
    with pytest.raises(LedgerError, match="negative_control_ref"):
        led.add_evidence(ev("e1", negative_control_ref="missing"))
    led.add_evidence(ev("c-pass", purpose="negative_control", result="pass", scope="injected defect d1"))
    with pytest.raises(LedgerError, match="negative_control_ref"):
        led.add_evidence(ev("e2", negative_control_ref="c-pass"))  # a control that did not reject the defect
    led.add_evidence(ev("c1", purpose="negative_control", result="fail", scope="injected defect d1"))
    led.add_evidence(ev("e3", negative_control_ref="c1"))


def test_an_id_is_written_once(led) -> None:
    led.add_evidence(ev("e1"))
    with pytest.raises(LedgerError, match="e1"):
        led.add_evidence(ev("e1", result="fail"))


def test_a_clean_ledger_verifies_and_every_entry_is_chained(led) -> None:
    led.add_evidence(ev("e1"))
    led.add_evidence(ev("e2", scope="another scope"))
    assert led.verify() == ()
    entries = led.entries()
    assert [e["seq"] for e in entries] == [0, 1, 2, 3]
    assert entries[0]["prev"] == "" and entries[1]["prev"] == entries[0]["entry_sha256"]
    assert led.head() == entries[-1]["entry_sha256"]


def _file_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.add_standard(STD)
    ledger.add_vault("v1", FX, files=1, roots=1)
    ledger.add_evidence(ev("e1"))
    ledger.add_evidence(ev("e2", scope="another scope"))
    return path


def test_a_ledger_reopens_from_its_file_with_the_same_head(on, tmp_path) -> None:
    path = _file_ledger(tmp_path)
    first = Ledger(path).head()
    again = Ledger(path)
    assert again.head() == first and again.verify() == ()
    again.add_evidence(ev("e3", scope="third"))
    assert Ledger(path).get("evidence", "e3")["scope"] == "third"


@pytest.mark.parametrize("tamper", ["edit", "delete", "reorder"])
def test_an_edited_removed_or_reordered_entry_is_found(on, tmp_path, tamper) -> None:
    path = _file_ledger(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    if tamper == "edit":
        lines[2] = lines[2].replace('"pass"', '"fail"')
    elif tamper == "delete":
        del lines[1]
    else:
        lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert Ledger.audit(path) != ()
    with pytest.raises(LedgerCorrupt):
        Ledger(path)


def test_a_dropped_last_entry_is_found_by_the_head_a_reviewer_kept(on, tmp_path) -> None:
    # a chain cut at its tail is still a consistent chain: only a head recorded elsewhere shows the cut
    path = _file_ledger(tmp_path)
    kept = Ledger(path).head()
    assert Ledger.audit(path, expect_head=kept) == ()
    path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[:-1]) + "\n", encoding="utf-8")
    assert Ledger.audit(path) == ()
    assert any("head" in p for p in Ledger.audit(path, expect_head=kept))


def _rewrite(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def _seal_row(row: dict) -> None:
    row["body_sha256"] = digest(row["body"])
    row["entry_sha256"] = digest({k: row[k] for k in ("seq", "kind", "id", "body_sha256", "prev")})


def test_a_middle_entry_rewritten_with_consistent_hashes_is_found_by_the_link_after_it(on, tmp_path) -> None:
    path = _file_ledger(tmp_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["body"]["scope"] = "a scope nobody recorded"
    _seal_row(rows[1])  # this entry is now internally consistent; the entry after it still points at the old one
    _rewrite(path, rows)
    assert any("prev" in p for p in Ledger.audit(path))


def test_an_entry_removed_and_the_chain_relinked_is_found_by_the_sequence(on, tmp_path) -> None:
    path = _file_ledger(tmp_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    del rows[2]  # the first evidence entry; the last entry is re-linked to the vault entry before it
    rows[2]["prev"] = rows[1]["entry_sha256"]
    _seal_row(rows[2])
    _rewrite(path, rows)
    problems = Ledger.audit(path)
    assert any("seq" in p for p in problems) and not any("prev" in p for p in problems)


def test_a_changed_entry_header_with_its_body_untouched_is_found_by_the_entry_hash(on, tmp_path) -> None:
    path = _file_ledger(tmp_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[2]["id"] = "e2-renamed"
    _rewrite(path, rows)
    problems = Ledger.audit(path)
    assert any("entry_sha256" in p for p in problems) and not any("body" in p for p in problems)


def test_a_corrupt_ledger_accepts_no_further_entry(on, tmp_path) -> None:
    path = _file_ledger(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace('"pass"', '"fail"', 1), encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        Ledger(path).add_evidence(ev("e9", scope="x"))


def test_a_changed_body_with_a_recomputed_entry_hash_is_still_found(on, tmp_path) -> None:
    # an attacker who fixes the entry hash but not the next entry's link, or the body hash, is found
    path = _file_ledger(tmp_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["body"]["result"] = "fail"
    rows[1]["entry_sha256"] = digest({k: rows[1][k] for k in ("seq", "kind", "id", "body_sha256", "prev")})
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    problems = Ledger.audit(path)
    assert any("body" in p for p in problems), problems


# --- episodes: append-only, chained, a forecast comes before the outcome ----------------------------------------------


def test_episode_events_are_chained_in_order(on) -> None:
    ep = _episode()
    ep.append("plan", {"steps": 2})
    ep.append("forecast", {"status": "not_available"})
    ep.append("tool_result", {"check": "probe", "executed": True})
    record = ep.freeze(candidate_artifact_hash="a" * 64, outcome_state="qualified-draft", evidence_refs=("e1",),
                       known_unknowns=("helper h2 not compared",), run_cost=0, latency_seconds=1.5)
    events = record.events
    assert [e["kind"] for e in events] == ["plan", "forecast", "tool_result", "outcome"]
    assert events[0]["prev"] == record.genesis
    assert all(events[i + 1]["prev"] == events[i]["sha256"] for i in range(len(events) - 1))
    assert record.head == events[-1]["sha256"]
    assert record.independent_oracle_access is False


def test_a_forecast_cannot_be_written_after_the_outcome(on) -> None:
    ep = _episode()
    ep.append("forecast", {"status": "not_available"})
    ep.append("outcome", {"state": "qualified-draft"})
    with pytest.raises(ValueError, match="forecast"):
        ep.append("forecast", {"class": "low"})


def test_an_outcome_needs_a_forecast_or_a_stated_absence_before_it(on) -> None:
    ep = _episode()
    ep.append("plan", {"steps": 1})
    with pytest.raises(ValueError, match="forecast"):
        ep.append("outcome", {"state": "qualified-draft"})


def test_only_the_existing_wire_states_are_outcomes(on) -> None:
    for state in ("verified", "qualified-draft", "blocked-escalated", "not_recorded"):
        ep = _episode(episode_id=f"ep-{state}")
        ep.append("forecast", {"status": "not_available"})
        assert ep.freeze(candidate_artifact_hash="b" * 64, outcome_state=state, evidence_refs=(), known_unknowns=(),
                         run_cost=0, latency_seconds=0).outcome_state == state
    for alias in ("verified_ready", "qualified_draft", "blocked_escalated", "Use with caution", "0.93"):
        ep = _episode()
        ep.append("forecast", {"status": "not_available"})
        with pytest.raises(ValueError, match="outcome_state"):
            ep.freeze(candidate_artifact_hash="b" * 64, outcome_state=alias, evidence_refs=(), known_unknowns=(),
                      run_cost=0, latency_seconds=0)


def test_an_event_holds_ids_and_counts_never_text(on) -> None:
    ep = _episode()
    with pytest.raises(ValueError, match="detail"):
        ep.append("tool_result", {"output": "x" * 300})
    with pytest.raises(ValueError, match="detail"):
        ep.append("tool_result", {"nested": {"a": "b"}})
    with pytest.raises(ValueError, match="kind"):
        ep.append("gossip", {})
    ep.append("tool_result", {"check": "probe", "ok": True, "n": 3, "ratio": 0.5, "ids": ["a", "b"]})


def test_an_independent_oracle_access_flag_is_explicit_and_recorded(on) -> None:
    ep = _episode(independent_oracle_access=True)
    ep.append("forecast", {"status": "not_available"})
    rec = ep.freeze(candidate_artifact_hash="c" * 64, outcome_state="not_recorded", evidence_refs=(), known_unknowns=(),
                    run_cost=0, latency_seconds=0)
    assert rec.independent_oracle_access is True
    assert rec.to_dict()["independent_oracle_access"] is True


def test_a_stored_episode_is_checked_on_its_own_not_only_by_the_ledger_around_it(on) -> None:
    from g6.research.echa.episodes import verify_episode_body

    def fresh() -> dict:
        ep = _episode()
        ep.append("plan", {"steps": 2})
        ep.append("forecast", {"status": "not_available"})
        return ep.freeze(candidate_artifact_hash="e" * 64, outcome_state="qualified-draft", evidence_refs=(),
                         known_unknowns=(), run_cost=0, latency_seconds=0).to_dict()

    assert verify_episode_body(fresh()) == []
    edited = fresh()
    edited["events"][0]["detail"]["steps"] = 9  # content changed, every stored hash left as it was
    assert any("hash" in p for p in verify_episode_body(edited))
    relinked = fresh()
    relinked["events"][1]["prev"] = "0" * 64
    assert any("linked" in p for p in verify_episode_body(relinked))
    regenesis = fresh()
    regenesis["genesis"] = "0" * 64
    assert any("genesis" in p for p in verify_episode_body(regenesis))
    rehead = fresh()
    rehead["head"] = "0" * 64
    assert any("head" in p for p in verify_episode_body(rehead))
    restated = fresh()
    restated["outcome_state"] = "verified"
    assert any("outcome_state" in p for p in verify_episode_body(restated))
    unforecast = fresh()
    unforecast["events"] = [e for e in unforecast["events"] if e["kind"] != "forecast"]
    assert any("forecast" in p for p in verify_episode_body(unforecast))


def test_a_stored_episode_is_found_tampered(on, tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.add_standard(STD)
    ep = _episode()
    ep.append("plan", {"steps": 2})
    ep.append("forecast", {"status": "not_available"})
    ledger.add_episode(ep.freeze(candidate_artifact_hash="d" * 64, outcome_state="qualified-draft", evidence_refs=(),
                                 known_unknowns=(), run_cost=0, latency_seconds=0))
    assert Ledger.audit(path) == ()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["kind"] == "episode":
            row["body"]["events"][0]["detail"]["steps"] = 9  # edit an event, keep every hash as it was
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    assert Ledger.audit(path) != ()
