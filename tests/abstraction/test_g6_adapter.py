# SPDX-License-Identifier: Apache-2.0
"""L3 pins (task #66 addendum): the G6 wire-envelope adapter.

Laws under test:
- The map is total, deterministic, and DOWN-ONLY: NO input - including
  fuzzed/forged statuses - yields the wire `verified`/`verified_ready`
  states or a green badge; unknown statuses (the old literal `verified`
  included) fail closed to blocked-escalated; human review is always
  required.
- Crosswalk side channels: gate counterexamples + replay regret_events
  reach the warning_card; calibration/robustness/MDL reach evidence.
- The adapter's fields win over a forged caller payload.
- It works on REAL LibraryEntry objects produced by Library.evaluate on a
  synthetic domain (non-vacuity), and imports nothing from g6.*.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from hypothesis import given, settings
from hypothesis import strategies as st

from g6.abstraction.abstractions import Quotient
from g6.abstraction.domain import ConcreteDomain
from g6.abstraction.g6_adapter import (
    STATE_BLOCKED_ESCALATED,
    STATE_QUALIFIED_DRAFT,
    WARNING_CODE,
    attach_abstraction_envelope,
    surface_state,
)
from g6.abstraction.library import Library, LibraryEntry

WIRE_FORBIDDEN_STATES = {"verified", "verified_ready"}
ALLOWED_STATES = {STATE_QUALIFIED_DRAFT, STATE_BLOCKED_ESCALATED}


def _entry(status: str, **overrides) -> LibraryEntry:
    base = dict(
        entry_id="quotient-001",
        spec={"kind": "quotient", "radius": 0.2, "lipschitz": 5.0},
        domain_name="synthetic",
        gate={"passed": True, "checks": {}, "counterexamples": [],
              "notes": [], "calibration": {"coverage_rate": 1.0},
              "n_counterexamples": 0},
        replay={"budget": 40, "best_without": 1.0, "best_with": 1.0,
                "evals_used": 40, "evals_saved": 0, "horizon_reached": 60,
                "regret_events": [], "reorder_gain": None},
        robust={"lcb": 0.1, "lcb_level": 0.95, "mean_benefit": 0.2,
                "sound_robust": True, "valuable": True,
                "total_regret_events": 0, "n_permutations": 6,
                "n_candidates_tried": 1, "benefits": [],
                "per_permutation": []},
        mdl={"raw_bits": 100.0, "coded_bits": 80.0, "mechanism_bits": 10.0},
        blp_score=0.2,
        status=status,
        trace_len=60,
        created=time.time(),
    )
    base.update(overrides)
    return LibraryEntry(**base)


# -- The frozen crosswalk table -----------------------------------------

def test_crosswalk_table_is_down_only():
    expected = {
        "checked": (STATE_QUALIFIED_DRAFT, "amber", "executable"),
        "provisional": (STATE_QUALIFIED_DRAFT, "amber", "separated_judgment"),
        "degenerate": (STATE_BLOCKED_ESCALATED, "red", ""),
        "rejected": (STATE_BLOCKED_ESCALATED, "red", ""),
    }
    for status, want in expected.items():
        assert surface_state(status) == want
        env = attach_abstraction_envelope("evaluate", _entry(status))
        assert env["completion_state"] == want[0]
        assert env["reliability_status"] == want[1]
        assert env["evidence"]["oracle_strength"] == want[2]
        assert env["human_review_required"] is True
        assert env["warning_card"]["code"] == WARNING_CODE
        assert env["warning_card"]["message"], "card message must never be empty"


# -- Adversarial: nothing reaches wire verified -------------------------

@settings(max_examples=200, deadline=None)
@given(st.one_of(st.text(), st.none(), st.integers(), st.booleans()))
def test_no_status_whatsoever_yields_wire_verified(status):
    state, badge, _ = surface_state(status)
    assert state in ALLOWED_STATES
    assert state not in WIRE_FORBIDDEN_STATES
    assert badge in {"amber", "red"}, "green is unreachable"


def test_hostile_status_literals_fail_closed_or_demote():
    for status in ("verified_ready", "green", "VERIFIED ", "Verified",
                   "qualified_draft", "qualified-draft", "ok", "", None, 42):
        state, badge, _ = surface_state(status)
        assert state in ALLOWED_STATES
        assert badge != "green"
    # The module's own literal demotes; imposter literals, and the old
    # top status "verified", fail closed.
    assert surface_state("CHECKED ")[0] == STATE_QUALIFIED_DRAFT
    assert surface_state("verified")[0] == STATE_BLOCKED_ESCALATED
    assert surface_state("verified_ready")[0] == STATE_BLOCKED_ESCALATED
    assert surface_state("qualified_draft")[0] == STATE_BLOCKED_ESCALATED
    assert surface_state("qualified-draft")[0] == STATE_BLOCKED_ESCALATED


def test_forged_payload_fields_cannot_survive_the_merge():
    forged = {
        "completion_state": "verified_ready",
        "reliability_status": "green",
        "human_review_required": False,
        "evidence": {"source": "forged"},
        "custom_field": "kept",
    }
    env = attach_abstraction_envelope("evaluate", _entry("checked"), forged)
    assert env["completion_state"] == STATE_QUALIFIED_DRAFT
    assert env["reliability_status"] == "amber"
    assert env["human_review_required"] is True
    assert env["evidence"]["source"] == "g6.abstraction.library"
    assert env["custom_field"] == "kept", "non-envelope payload fields pass through"


def test_garbage_entries_fail_closed():
    for garbage in (None, 42, {}, {"status": {}}, object()):
        env = attach_abstraction_envelope("evaluate", garbage)
        assert env["completion_state"] == STATE_BLOCKED_ESCALATED
        assert env["reliability_status"] == "red"
        assert env["human_review_required"] is True


def test_deep_hostile_objects_fail_closed_without_raising():
    """L3-check hardening pins: objects whose OWN methods raise (a str
    subclass with a detonating strip(), a Mapping whose iteration raises,
    a status whose __str__/__repr__ raise) must land blocked-escalated -
    the adapter is total, it never propagates a hostile exception."""
    from collections.abc import Mapping as ABCMapping

    class BombStr(str):
        def strip(self, *a):  # noqa: A003
            raise RuntimeError("boom")

    class BombMapping(ABCMapping):
        def __getitem__(self, key):
            raise RuntimeError("boom")

        def __iter__(self):
            raise RuntimeError("boom")

        def __len__(self):
            return 1

    class BombStatus:
        def __str__(self):
            raise RuntimeError("boom")

        def __repr__(self):
            raise RuntimeError("boom")

    assert surface_state(BombStr("checked"))[0] == STATE_BLOCKED_ESCALATED, (
        "a str subclass that detonates in strip() must read as unknown"
    )
    for hostile in (BombMapping(), {"status": BombStr("checked")},
                    {"status": BombStatus()}):
        env = attach_abstraction_envelope("evaluate", hostile)
        assert env["completion_state"] == STATE_BLOCKED_ESCALATED
        assert env["reliability_status"] == "red"
        assert env["human_review_required"] is True
        assert env["warning_card"]["message"], "card message survives hostility"


def test_second_round_escape_bombs_fail_closed_and_serialize():
    """D1+L3-check round-2 pins: attribute-probe-time bombs (raising
    to_dict property, raising __class__), a hostile payload __len__, and
    bombs smuggled into entry data fields must all land blocked-escalated
    without an exception - and every emitted envelope must json-serialize
    (a delayed detonation on dumps is still a detonation)."""
    import json as _json

    from collections.abc import Mapping as ABCMapping

    class PropBomb:  # to_dict PROPERTY whose getter raises (not AttributeError)
        @property
        def to_dict(self):
            raise RuntimeError("boom")

    class ClassBomb:  # detonates inside the ABC isinstance check
        @property
        def __class__(self):
            raise RuntimeError("boom")

    class ReprBomb:
        def __str__(self):
            raise RuntimeError("boom")

        def __repr__(self):
            raise RuntimeError("boom")

    class LenBombPayload(ABCMapping):
        def __len__(self):
            raise RuntimeError("boom")

        def __getitem__(self, key):
            raise KeyError(key)

        def __iter__(self):
            return iter(())

    for hostile in (PropBomb(), ClassBomb()):
        env = attach_abstraction_envelope("evaluate", hostile)
        assert env["completion_state"] == STATE_BLOCKED_ESCALATED
        _json.dumps(env)

    env = attach_abstraction_envelope(
        "evaluate",
        {"status": "rejected", "spec_hash": ReprBomb(),
         "gate": {"calibration": {"nested": ReprBomb()}}},
        LenBombPayload(),
    )
    assert env["completion_state"] == STATE_BLOCKED_ESCALATED
    assert env["evidence"]["spec_hash"] == "<unrepresentable>"
    _json.dumps(env), "smuggled bombs are sanitized - the envelope always serializes"

    env = attach_abstraction_envelope(ReprBomb(), _entry_like("checked"))
    assert env["completion_state"] == STATE_QUALIFIED_DRAFT
    _json.dumps(env)


def _entry_like(status):
    return {"status": status}


# -- Side channels ------------------------------------------------------

def test_counterexamples_and_regrets_reach_the_warning_card():
    cexs = [{"kind": "bound", "index": i, "score": 1.0, "bound": 0.5,
             "config": {}} for i in range(15)]
    regrets = [{"index": i, "score": 1.0, "incumbent": 0.5, "bound": 0.4,
                "config": {}} for i in range(3)]
    entry = _entry(
        "rejected",
        gate={"passed": False, "checks": {"bound_soundness": False},
              "counterexamples": cexs, "notes": [], "calibration": {},
              "n_counterexamples": len(cexs)},
        replay={"budget": 40, "best_without": 1.0, "best_with": 0.5,
                "evals_used": 30, "evals_saved": 10, "horizon_reached": 60,
                "regret_events": regrets, "reorder_gain": None},
        robust={"lcb": -0.5, "lcb_level": 0.95, "mean_benefit": -0.1,
                "sound_robust": False, "valuable": False,
                "total_regret_events": 7, "n_permutations": 6,
                "n_candidates_tried": 2, "benefits": [],
                "per_permutation": []},
    )
    env = attach_abstraction_envelope("evaluate", entry)
    card = env["warning_card"]
    assert len(card["counterexamples"]) == 10, "clipped to 10, never dropped"
    assert card["counterexamples"][0]["kind"] == "bound"
    assert len(card["regret_events"]) == 3
    assert card["bootstrap_regret_events"] == 7
    assert env["evidence"]["robust"]["sound_robust"] is False


def test_calibration_and_reports_reach_evidence():
    env = attach_abstraction_envelope("evaluate", _entry("checked"))
    ev = env["evidence"]
    assert ev["calibration"] == {"coverage_rate": 1.0}
    assert ev["robust"]["lcb"] == 0.1
    assert ev["robust"]["valuable"] is True
    assert ev["mdl"]["raw_bits"] == 100.0
    assert ev["entry_id"] == "quotient-001"
    assert ev["domain"] == "synthetic"


def test_adapter_is_deterministic():
    entry = _entry("provisional")
    assert (attach_abstraction_envelope("evaluate", entry)
            == attach_abstraction_envelope("evaluate", entry))


# -- Real integration: genuine Library.evaluate verdicts ----------------

def _synthetic_domain() -> ConcreteDomain:
    """Deterministic 60-point trace, objective minimised, no I/O."""
    rows = []
    for i in range(60):
        x = (i * 7) % 10
        y = (i * 3) % 5
        rows.append({"x": x, "y": y,
                     "obj": (x - 4) ** 2 + 0.5 * y + 0.01 * i})
    return ConcreteDomain.from_rows(rows, objective="obj", maximise=False,
                                    name="synthetic")


def test_real_rejected_entry_maps_to_blocked_with_counterevidence():
    dom = _synthetic_domain()
    lib = Library(k_permutations=6)
    # lipschitz=0 over a wide radius over-claims the bound: unsound.
    entry = lib.evaluate(Quotient(radius=0.5, lipschitz=0.0), dom, budget=40)
    assert entry.status == "rejected"
    env = attach_abstraction_envelope("evaluate", entry)
    assert env["completion_state"] == STATE_BLOCKED_ESCALATED
    assert env["reliability_status"] == "red"
    has_counterevidence = (
        env["warning_card"]["counterexamples"]
        or env["warning_card"]["regret_events"]
        or (env["warning_card"]["bootstrap_regret_events"] or 0) > 0
    )
    assert has_counterevidence, "a real rejection must carry its counterevidence"


def test_real_degenerate_entry_maps_to_blocked():
    dom = _synthetic_domain()
    lib = Library(k_permutations=6)
    # One giant sound cell (radius at the spec limit swallows the whole
    # normalised space): structurally trivial.
    entry = lib.evaluate(Quotient(radius=2.0, lipschitz=100.0), dom, budget=40)
    assert entry.status == "degenerate"
    env = attach_abstraction_envelope("evaluate", entry)
    assert env["completion_state"] == STATE_BLOCKED_ESCALATED


def test_real_sound_entry_maps_to_qualified_draft_at_most():
    dom = _synthetic_domain()
    lib = Library(k_permutations=6)
    entry = lib.evaluate(Quotient(radius=0.15, lipschitz=5.0), dom, budget=40)
    assert entry.status in ("checked", "provisional")
    env = attach_abstraction_envelope("evaluate", entry)
    assert env["completion_state"] == STATE_QUALIFIED_DRAFT, (
        "even the module's strongest verdict is at most a qualified draft"
    )
    assert env["reliability_status"] == "amber"
    assert env["human_review_required"] is True


# -- Isolation: zero G6-component imports -------------------------------

def test_adapter_imports_no_other_component():
    import g6.abstraction.g6_adapter as adapter

    with open(adapter.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert "import g6." not in source.replace("import g6.abstraction", "")
    assert "from g6." not in source.replace("from g6.abstraction", "")
    # Dynamic law, checked in a CLEAN interpreter (the pytest session itself
    # may be polluted by a repo-level conftest that imports mvp.*): importing
    # the adapter must not pull any G6 component into the process.
    probe = (
        "import sys; import g6.abstraction.g6_adapter; "
        "bad = [m for m in sys.modules if m.startswith('g6.') and not m.startswith('g6.abstraction')]; "
        "sys.exit(1 if bad else 0)"
    )
    # the folder that holds the g6 package (not g6 itself, where g6/operator would shadow the stdlib operator)
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(adapter.__file__)))
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=package_root, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"clean-interpreter import pulled mvp.* modules: {proc.stdout} {proc.stderr}"
    )
