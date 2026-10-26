# SPDX-License-Identifier: Apache-2.0
"""After the skill review: a theory becomes active only on a decision that follows from held-out evidence by
``promote``'s rules, accepted under a name that is neither empty nor automated; a bundle's review is sealed by its
digest. Offline; no model."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from g6.learning import (HeldOutEvidence, PairedOutcome, PromotionDecision, ReviewRecord, Theory, TheoryStore,
                         build_addendum, export_bundle, import_bundle, promote)
from g6.learning.promotion import RECOMMENDED


def _evidence(theory: Theory, wins: int, losses: int, ties: int) -> HeldOutEvidence:
    outcomes = [(True, False)] * wins + [(False, True)] * losses + [(True, True)] * ties
    pairs = tuple(PairedOutcome(f"h{k}", w, wo) for k, (w, wo) in enumerate(outcomes))
    return HeldOutEvidence(theory.theory_id, theory.digest, pairs)


@pytest.fixture
def store():
    with TheoryStore(":memory:", scope="sums") as s:
        yield s


@pytest.fixture
def theory(store):
    [t] = store.add_candidates([Theory.new("WRONG_FORMAT", "Answer with digits only.")])
    return t


def _still_a_candidate(store: TheoryStore, theory: Theory) -> None:
    assert store.get(theory.theory_id).status == "candidate" and build_addendum(store).text == ""


# --- a decision activates only when it follows from its evidence --------------------------------------------------

def test_a_hand_built_recommendation_does_not_activate_a_theory(store, theory) -> None:
    fake = PromotionDecision(theory_id=theory.theory_id, theory_digest=theory.digest, recommendation="promote",
                             code=RECOMMENDED, reasons=(), evidence_digest="none")
    with pytest.raises(ValueError, match="G6_E_LEARNING_DECISION_UNFOUNDED"):
        store.activate(fake, accepted_by="Ada")
    _still_a_candidate(store, theory)


def test_a_refusal_relabelled_as_a_recommendation_does_not_activate(store, theory) -> None:
    refused = promote(theory, _evidence(theory, 0, 8, 4))
    assert refused.recommendation == "refuse"
    relabelled = dataclasses.replace(refused, recommendation="promote", code=RECOMMENDED)
    with pytest.raises(ValueError, match="G6_E_LEARNING_DECISION_UNFOUNDED"):
        store.activate(relabelled, accepted_by="Ada")
    _still_a_candidate(store, theory)


def test_a_recommendation_whose_evidence_was_swapped_does_not_activate(store, theory) -> None:
    good = promote(theory, _evidence(theory, 10, 0, 2))
    swapped = dataclasses.replace(good, evidence=_evidence(theory, 0, 8, 4))
    with pytest.raises(ValueError, match="G6_E_LEARNING_DECISION_UNFOUNDED"):
        store.activate(swapped, accepted_by="Ada")
    _still_a_candidate(store, theory)


def test_survival_a_recommendation_from_promote_activates_and_reaches_the_hint_text(store, theory) -> None:
    decision = promote(theory, _evidence(theory, 10, 0, 2))
    assert decision.recommendation == "promote" and decision.evidence is not None
    active = store.activate(decision, accepted_by="Ada")
    assert (active.status, active.activated_by) == ("active", "Ada")
    assert "Answer with digits only." in build_addendum(store).text


# --- the name that accepts is a person's -------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["auto:learner", "  AUTO:strategy  ", "", "   "])
def test_an_empty_or_automated_name_cannot_accept_a_recommendation(store, theory, name) -> None:
    decision = promote(theory, _evidence(theory, 10, 0, 2))
    with pytest.raises(ValueError, match="G6_E_LEARNING_ACCEPTANCE_REQUIRED"):
        store.activate(decision, accepted_by=name)
    _still_a_candidate(store, theory)


# --- LEARNING-01: a theory's proposer cannot accept it, as in g6.metaharness --------------------------------------

@pytest.mark.parametrize("name", ["Bo", " bo ", "BO"])
def test_learning_01_a_theorys_proposer_cannot_accept_it(store, name) -> None:
    # Fails without TheoryStore.activate's "if who.lower() == theory.proposer.strip().lower()" refusal.
    [theory] = store.add_candidates([Theory.new("WRONG_FORMAT", "Answer with digits only.", proposer="Bo")])
    decision = promote(theory, _evidence(theory, 10, 0, 2))
    with pytest.raises(ValueError, match="G6_E_LEARNING_APPROVER_IS_PROPOSER: the person accepting a change cannot "
                                         "be its proposer"):
        store.activate(decision, accepted_by=name)
    _still_a_candidate(store, theory)


def test_learning_01_another_person_accepts_it_and_the_store_keeps_the_proposer(tmp_path: Path) -> None:
    # Fails if add_candidates stops writing the proposer column or _row_to_theory stops reading it.
    path = tmp_path / "theories.db"
    with TheoryStore(path, scope="sums") as first:
        [theory] = first.add_candidates([Theory.new("WRONG_FORMAT", "Answer with digits only.", proposer="Bo")])
    with TheoryStore(path, scope="sums") as again:
        assert again.get(theory.theory_id).proposer == "Bo"
        with pytest.raises(ValueError, match="G6_E_LEARNING_APPROVER_IS_PROPOSER"):
            again.activate(promote(theory, _evidence(theory, 10, 0, 2)), accepted_by="bo")
        active = again.activate(promote(theory, _evidence(theory, 10, 0, 2)), accepted_by="Ada")
        assert (active.status, active.activated_by, active.proposer) == ("active", "Ada", "Bo")


def test_learning_01_a_store_made_before_proposers_were_kept_opens_and_keeps_one(tmp_path: Path) -> None:
    # Fails without TheoryStore.__init__'s "ALTER TABLE theories ADD COLUMN proposer" for an older store.
    import sqlite3

    path = tmp_path / "older.db"
    TheoryStore(path, scope="sums").close()
    with sqlite3.connect(path) as older:
        older.execute("ALTER TABLE theories DROP COLUMN proposer")
    with TheoryStore(path, scope="sums") as store:
        [theory] = store.add_candidates([Theory.new("WRONG_FORMAT", "Answer with digits only.", proposer="Bo")])
        assert store.get(theory.theory_id).proposer == "Bo"


def test_learning_01_a_learning_run_records_who_asked_for_it_as_each_theorys_proposer(store) -> None:
    # Fails without runner._admit stamping run_learning's proposer on each theory it adds.
    import re

    from g6.learning import Dataset, T3Strategy, TaskInstance, run_learning

    def tasks(prefix: str, n: int, split: str) -> Dataset:
        return Dataset(prefix, [TaskInstance(f"[{prefix}#{i}] what is {i} + {i}?", str(2 * i), f"{prefix}{i}")
                                for i in range(n)], split=split)

    def agent(prompt: str) -> str:  # right only with the format hint in the prompt
        a, b = re.search(r"what is (\d+) \+ (\d+)", prompt).groups()
        return str(int(a) + int(b)) if "[WRONG_FORMAT]" in prompt else "the answer is a number"

    report = run_learning(T3Strategy(), tasks("t", 10, "train"), agent, holdout=tasks("h", 10, "holdout"),
                          store=store, proposer="Bo")
    [theory] = report.harness
    decision = promote(store.get(theory.theory_id), report.evidence[theory.theory_id])
    assert store.get(theory.theory_id).proposer == "Bo"
    with pytest.raises(ValueError, match="G6_E_LEARNING_APPROVER_IS_PROPOSER"):
        store.activate(decision, accepted_by="Bo")
    assert store.activate(decision, accepted_by="Ada").status == "active"


# --- the bundle's review is sealed by its digest -------------------------------------------------------------------

def _export(tmp_path: Path, review: ReviewRecord | None) -> Path:
    with TheoryStore(":memory:", scope="x") as source:
        source.add_candidates([Theory.new("WRONG_FORMAT", "Answer with digits only.")])
        export_bundle(tmp_path / "bundle", source, [], review)
    return tmp_path / "bundle"


def _edit_manifest(bundle: Path, edit) -> None:
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


REVIEW = ReviewRecord("reviewer-a", "2026-10-20", "one format hint")


@pytest.mark.parametrize("edit", [
    lambda m: m["review"].update(reviewer_id="someone-else"),
    lambda m: m["review"].update(scope_summary="every hint we have"),
    lambda m: m["review"].update(residual_risk_notes="none"),
    lambda m: m.update(review=None),
])
def test_a_review_edited_after_export_is_refused(tmp_path: Path, edit) -> None:
    bundle = _export(tmp_path, REVIEW)
    _edit_manifest(bundle, edit)
    imported = import_bundle(bundle)
    assert (imported.code, imported.theories, imported.review) == ("G6_E_LEARNING_BUNDLE_DIGEST_MISMATCH", (), None)
    assert imported.completion_state == "blocked-escalated"


def test_a_review_added_to_an_unreviewed_bundle_is_refused(tmp_path: Path) -> None:
    bundle = _export(tmp_path, None)
    _edit_manifest(bundle, lambda m: m.update(review=REVIEW.to_dict()))
    assert import_bundle(bundle).code == "G6_E_LEARNING_BUNDLE_DIGEST_MISMATCH"


@pytest.mark.parametrize("review", [REVIEW, None])
def test_survival_an_unedited_bundle_imports_with_its_review(tmp_path: Path, review) -> None:
    imported = import_bundle(_export(tmp_path, review))
    assert imported.completion_state == "qualified-draft" and imported.review == review
    assert [t.status for t in imported.theories] == ["candidate"]
