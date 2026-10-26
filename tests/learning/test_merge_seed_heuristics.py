# SPDX-License-Identifier: Apache-2.0
"""First-party hints use the existing candidate, credit and promotion path."""
import pytest

from g6.learning import (HeldOutEvidence, PairedOutcome, TheoryStore, build_addendum,
                         load_seed_heuristics, promote, render_addendum)


def test_seeds_reach_addendum_only_after_held_out_promotion():
    # Removal: seeds.py `return store.add_candidates(theories)` loses the existing lifecycle.
    with TheoryStore(":memory:", scope="seeds") as store:
        seeds = load_seed_heuristics(store)
        chosen = next(t for t in seeds if t.theory_id == "seed:seeds:constraint_propagation")
        assert chosen.status == "candidate" and build_addendum(store, {"constraints": []}).text == ""
        evidence = HeldOutEvidence(chosen.theory_id, chosen.digest,
                                   tuple(PairedOutcome(f"h{i}", True, False) for i in range(12)))
        decision = promote(chosen, evidence)
        assert decision.completion_state == "qualified-draft"  # ADAPT_EURISKO-01
        store.activate(decision, accepted_by="reviewer")
        assert build_addendum(store, {"constraints": []}).theory_ids == (chosen.theory_id,)
        assert build_addendum(store, {"unrelated": True}).text == ""


def test_seed_selection_and_equal_outcomes_earn_no_credit():
    # Removal: seeds.py `return store.add_candidates(theories)` removes paired-only credit (ADAPT_EURISKO-02/D1).
    with TheoryStore(":memory:", scope="seeds") as store:
        seed = load_seed_heuristics(store)[0]
        for i in range(20):
            render_addendum([seed], {})
            store.record_paired_outcome(seed.theory_id, f"t{i}", True, True)
        saved = store.get(seed.theory_id)
        assert saved.confirmations == saved.disconfirmations == 0
        assert saved.strength == seed.prior_strength and saved.status == "candidate"
        store.record_paired_outcome(seed.theory_id, "loss", False, True)
        assert store.get(seed.theory_id).strength < seed.prior_strength


def test_only_first_party_seed_set_is_loaded():
    # Removal: seeds.py `theories = [...]` loses the bounded first-party set (ADAPT_EURISKO-03).
    with TheoryStore(":memory:", scope="seeds") as store:
        seeds = load_seed_heuristics(store)
        assert len(seeds) == 16
        assert len({t.theory_id for t in seeds}) == 16
        assert all(t.rationale == "First-party seed; unmeasured until paired evaluation." for t in seeds)
        assert all(t.status == "candidate" and not t.source_instances for t in seeds)


def test_condition_keys_are_metadata_not_truth_claims():
    # Removal: addendum.py `return ... any(key in task_meta ...)` prevents key-presence matching.
    with TheoryStore(":memory:", scope="seeds") as store:
        seed = next(t for t in load_seed_heuristics(store) if t.theory_id == "seed:seeds:exploit_symmetry")
        assert render_addendum([seed], {"symmetric_structure": False}).theory_ids == (seed.theory_id,)
        assert render_addendum([seed], {"symmetry": True}).text == ""


def test_each_scope_of_one_database_loads_its_own_seeds(tmp_path):
    # Removal: seeds.py `f"seed:{store.scope}:{name}"` (back to `seed:{name}`) refuses the second scope's seeds.
    path = tmp_path / "theories.db"
    with TheoryStore(path, scope="maths") as maths, TheoryStore(path, scope="code") as code:
        assert len(load_seed_heuristics(maths)) == len(load_seed_heuristics(code)) == 16
        assert {t.theory_id for t in maths.theories()}.isdisjoint(t.theory_id for t in code.theories())
        with pytest.raises(ValueError, match="G6_E_LEARNING_ID_EXISTS"):
            load_seed_heuristics(maths)


def test_store_failure_is_not_reported_as_a_loaded_seed_set(monkeypatch):
    # Removal: seeds.py `return store.add_candidates(theories)` bypasses the failing dependency.
    with TheoryStore(":memory:", scope="seeds") as store:
        def broken(items):
            raise OSError("store unavailable")
        monkeypatch.setattr(store, "add_candidates", broken)
        with pytest.raises(OSError, match="store unavailable"):
            load_seed_heuristics(store)
