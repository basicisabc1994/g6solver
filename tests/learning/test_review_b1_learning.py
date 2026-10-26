# SPDX-License-Identifier: Apache-2.0
"""Review B1: a bundle made by an earlier version of G6 is refused as old, not as tampered (B1-3), and a decision
cannot lower the bar it is activated by (B1-5). Offline; no model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from g6.learning import (HeldOutEvidence, PairedOutcome, PromotionPolicy, Theory, TheoryStore, export_bundle,
                         import_bundle, promote)
from g6.learning.bundle import FORMAT

# --- B1-3: the bundle format ----------------------------------------------------------------------------------------


def _export(tmp_path: Path) -> Path:
    with TheoryStore(":memory:", scope="x") as source:
        source.add_candidates([Theory.new("WRONG_FORMAT", "Answer with digits only.")])
        export_bundle(tmp_path / "bundle", source, [])
    return tmp_path / "bundle"


def _as_made_by_format_1(bundle: Path) -> None:
    """The manifest an earlier G6 wrote: format /1, its digest over the theories and artifacts only."""
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    theories = json.loads((bundle / "theories.json").read_text(encoding="utf-8"))
    artifacts = json.loads((bundle / "artifacts.json").read_text(encoding="utf-8"))
    canonical = json.dumps({"theories": theories, "artifacts": artifacts}, sort_keys=True, separators=(",", ":"))
    manifest.update(format="g6.learning.bundle/1", content_digest=hashlib.sha256(canonical.encode()).hexdigest())
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_a_bundle_an_earlier_version_made_is_refused_as_old_not_as_tampered(tmp_path: Path) -> None:
    bundle = _export(tmp_path)
    _as_made_by_format_1(bundle)
    imported = import_bundle(bundle)
    assert imported.code == "G6_E_LEARNING_BUNDLE_OLD_FORMAT", (imported.code, imported.reason)
    assert "earlier version of G6" in imported.reason and "export it again" in imported.reason
    assert (imported.theories, imported.completion_state) == ((), "blocked-escalated")
    assert imported.to_envelope()["warning_card"]["code"] == "G6_E_LEARNING_BUNDLE_OLD_FORMAT"


def test_export_writes_the_current_format_and_it_imports(tmp_path: Path) -> None:
    bundle = _export(tmp_path)
    assert FORMAT == "g6.learning.bundle/2"
    assert json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["format"] == FORMAT
    assert import_bundle(bundle).completion_state == "qualified-draft"   # survival


def test_an_unknown_format_is_still_invalid(tmp_path: Path) -> None:
    bundle = _export(tmp_path)
    path = bundle / "manifest.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "format": "something/9"}),
                    encoding="utf-8")
    assert import_bundle(bundle).code == "G6_E_LEARNING_BUNDLE_INVALID"


# --- B1-5: the decision cannot lower its own bar ----------------------------------------------------------------------

LOWEST = PromotionPolicy(min_pairs=1, min_discordant=1, alpha=1.0)
LENIENT = PromotionPolicy(min_pairs=5, min_discordant=3, alpha=0.2)


def _evidence(theory: Theory, wins: int, ties: int = 0) -> HeldOutEvidence:
    outcomes = [(True, False)] * wins + [(True, True)] * ties
    return HeldOutEvidence(theory.theory_id, theory.digest,
                           tuple(PairedOutcome(f"h{k}", w, wo) for k, (w, wo) in enumerate(outcomes)))


def _candidate(store: TheoryStore, text: str = "Answer with digits only.") -> Theory:
    [theory] = store.add_candidates([Theory.new("WRONG_FORMAT", text)])
    return theory


def test_a_decision_under_its_own_lowered_policy_does_not_activate() -> None:
    with TheoryStore(":memory:", scope="sums") as store:
        theory = _candidate(store)
        lowered = promote(theory, _evidence(theory, 1), LOWEST)   # one made-up winning pair
        assert lowered.recommendation == "promote"                 # its own bar says yes
        with pytest.raises(ValueError, match="G6_E_LEARNING_DECISION_UNFOUNDED"):
            store.activate(lowered, accepted_by="Ada")
        assert store.get(theory.theory_id).status == "candidate"


def test_the_store_owner_sets_the_bar_and_a_stricter_decision_keeps_its_own() -> None:
    with TheoryStore(":memory:", scope="sums") as default, \
            TheoryStore(":memory:", scope="sums", promotion_policy=LENIENT) as lenient:
        for store in (default, lenient):
            theory = _candidate(store)
            decision = promote(theory, _evidence(theory, 3, ties=2), LENIENT)
            assert decision.recommendation == "promote"
            if store is default:
                with pytest.raises(ValueError, match="G6_E_LEARNING_DECISION_UNFOUNDED"):
                    store.activate(decision, accepted_by="Ada")
            else:
                assert store.activate(decision, accepted_by="Ada").status == "active"
        theory = _candidate(lenient, "Reply with the number only.")   # a decision under the default policy
        assert lenient.activate(promote(theory, _evidence(theory, 10, ties=2)), accepted_by="Ada").status == "active"
