# SPDX-License-Identifier: Apache-2.0
"""The door rule (DECISIONS #40, plan A1): ``verified`` stands only when G6's own code re-runs a check at the door
and reproduces it; a witness the producer hands over is ignored. Offline."""
from __future__ import annotations

import hashlib
import re
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "g6"

#: Environment switches that once turned a door off, narrowed it or staged it. In one process any switch is a
#: bypass, so none may be read anywhere in the library.
RETIRED_SWITCHES = (
    "G6_TASK_CONTRACT_ENFORCE",
    "G6_TASK_CONTRACT_ENFORCE_SURFACES",
    "G6_METHOD_CONTRACT",
    "G6_KERNEL_PROMOTION_LAW",
    "G6_PROMOTION_ENFORCE_SURFACES",
    "G6_PROMOTION_REQUIRE_SUBJECT_BINDING",
)


def _library_text() -> dict[str, str]:
    return {str(p.relative_to(SRC)): p.read_text(encoding="utf-8", errors="replace")
            for p in SRC.rglob("*.py") if "__pycache__" not in p.parts}


@pytest.mark.parametrize("switch", RETIRED_SWITCHES)
def test_no_retired_switch_is_read_anywhere_in_the_library(switch: str) -> None:
    pattern = re.compile(rf"\b{switch}\b")
    readers = sorted(path for path, text in _library_text().items() if pattern.search(text))
    assert readers == [], f"{switch} still appears in {readers}"


def test_the_task_contract_module_keeps_only_what_intake_uses() -> None:
    """The task-contract floor had no inputs: nothing fed ``task_mode`` or ``task_success_criteria`` to an envelope.
    What intake uses (``verifier_available`` and ``HUMAN_VERIFIER_PREFIX``) stays."""
    from g6.core import task_contract_verifier as tcv

    assert sorted(tcv.__all__) == ["HUMAN_VERIFIER_PREFIX", "verifier_available"]
    for gone in ("task_contract_floor_reasons", "task_contract_enforce_mode", "task_contract_enforce_scope",
                 "effective_task_contract_enforce_mode", "method_contract_enabled"):
        assert not hasattr(tcv, gone), gone
    assert tcv.verifier_available("human:expert_review") and not tcv.verifier_available("")


def test_an_envelope_has_no_task_contract_floor() -> None:
    from g6.core.reliability_envelope import ReliabilityEnvelope

    assert not hasattr(ReliabilityEnvelope, "_apply_task_contract_floor")


# --------------------------------------------------------------------------------------------------------------------
# The core law: a stated `verified` stands only on G6's own re-check, and nothing switches that off
# --------------------------------------------------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stated_verified(**fields):
    from g6.core.reliability_envelope import ReliabilityEnvelope

    return ReliabilityEnvelope(completion_state="verified", reliability_label="verified", **fields)


@pytest.fixture
def demo_rechecker(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A re-checker registered for the component ``demo``, as G6's own are: the door names the artifact from the
    result's content, and the re-check reads the artifact as it is now (``world``) and hashes it itself."""
    from g6.core import promotion_evaluator, reliability_envelope

    world: dict[str, str] = {}
    module = types.ModuleType("g6_test_demo_recheck")

    def claim(value):
        name, text = value.get("name"), value.get("text")
        if not isinstance(name, str) or not isinstance(text, str):
            return None
        return _sha(text), {"name": name}

    def replay(witness):
        name = (witness.get("details") or {}).get("name")
        if name not in world:
            return promotion_evaluator.ReplayOutcome(False, detail="the artifact is gone")
        return promotion_evaluator.ReplayOutcome(True, execution_id=_sha(world[name]))

    module.claim, module.replay = claim, replay
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(reliability_envelope._G6_RECHECKERS, "demo", (module.__name__, "demo.recheck"))
    return world


@pytest.mark.parametrize(("name", "value"), [
    *(("G6_KERNEL_PROMOTION_LAW", word) for word in ("", "0", "off", "false", "no", "shadow", "observe", "log")),
    ("G6_PROMOTION_ENFORCE_SURFACES", "coder"),
    ("G6_PROMOTION_REQUIRE_SUBJECT_BINDING", "0"),
])
def test_off_words_do_not_disable_the_law(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    envelope = _stated_verified()
    assert envelope.completion_state == envelope.reliability_label == "qualified-draft"
    assert envelope.degradation_reason.startswith("kernel promotion law:")


def test_a_self_minted_witness_does_not_promote() -> None:
    from g6.core.reliability_envelope import build_promotion_witness

    witness = build_promotion_witness(evaluator_id="agent:producer", execution_id="exec-1", oracle="producer_tests",
                                      run_id="run-1")
    envelope = _stated_verified(run_id="run-1", promotion_witness=witness)
    assert envelope.completion_state == "qualified-draft"
    assert "not one of G6's own re-checkers" in envelope.degradation_reason


def test_an_oracle_anyone_else_registers_does_not_promote(monkeypatch: pytest.MonkeyPatch) -> None:
    """``register_oracle`` is open to any code; the law reads only the re-checkers G6 ships (``_G6_RECHECKERS``)."""
    from g6.core import promotion_evaluator
    from g6.core.reliability_envelope import build_promotion_witness

    monkeypatch.setattr(promotion_evaluator, "_ORACLE_REPLAYS", {})
    promotion_evaluator.register_oracle("producer_tests",
                                        lambda w: promotion_evaluator.ReplayOutcome(True, execution_id="exec-1"))
    witness = build_promotion_witness(evaluator_id="agent:producer", execution_id="exec-1", oracle="producer_tests")
    assert _stated_verified(promotion_witness=witness).completion_state == "qualified-draft"


def test_a_registered_g6_rechecker_promotes(demo_rechecker: dict[str, str]) -> None:
    from g6.core.reliability_envelope import door_witness

    demo_rechecker["a"] = "the artifact"
    witness = door_witness("demo", {"name": "a", "text": "the artifact"}, run_id="run-1")
    envelope = _stated_verified(run_id="run-1", promotion_witness=witness)
    assert envelope.completion_state == envelope.reliability_label == "verified" and envelope.degraded is False


def test_a_rechecker_that_does_not_reproduce_is_held(demo_rechecker: dict[str, str]) -> None:
    from g6.core.reliability_envelope import door_witness

    demo_rechecker["a"] = "changed after the producer looked"
    witness = door_witness("demo", {"name": "a", "text": "the artifact"})
    envelope = _stated_verified(promotion_witness=witness)
    assert envelope.completion_state == "qualified-draft" and "did not reproduce" in envelope.degradation_reason


def test_the_law_reruns_the_check_whenever_an_envelope_is_built(demo_rechecker: dict[str, str]) -> None:
    from g6.core.reliability_envelope import ReliabilityEnvelope, door_witness

    demo_rechecker["a"] = "the artifact"
    promoted = _stated_verified(promotion_witness=door_witness("demo", {"name": "a", "text": "the artifact"}))
    assert promoted.completion_state == "verified"
    demo_rechecker["a"] = "edited later"
    assert ReliabilityEnvelope(**promoted.model_dump()).completion_state == "qualified-draft"


def test_a_witness_from_another_run_is_held(demo_rechecker: dict[str, str]) -> None:
    from g6.core.reliability_envelope import door_witness

    demo_rechecker["a"] = "the artifact"
    witness = door_witness("demo", {"name": "a", "text": "the artifact"}, run_id="run-1")
    envelope = _stated_verified(run_id="run-2", promotion_witness=witness)
    assert envelope.completion_state == "qualified-draft" and "never transfers between runs" in envelope.degradation_reason


def test_the_door_builds_its_witness_from_the_result_and_never_reads_the_producers(
        demo_rechecker: dict[str, str]) -> None:
    from g6.core.reliability_envelope import door_witness

    demo_rechecker["a"] = "the artifact"
    earned = door_witness("demo", {"name": "a", "text": "the artifact"})
    assert door_witness("demo", {"promotion_witness": earned}) is None   # nothing in the result itself to re-check
    assert door_witness("no_rechecker", {"name": "a", "text": "the artifact"}) is None

def test_a_configured_signing_key_neither_promotes_nor_blocks(demo_rechecker: dict[str, str], key_file,
                                                              monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The law reads no signature, so neither a key in the key file nor a key path (whose evaluator service the
    library does not have) can block a result G6's re-check reproduces, or lift one it does not."""
    from g6.core.reliability_envelope import door_witness

    key_file("G6_PROMOTION_EVALUATOR_KEY=test-evaluator-secret-key-abc123\n")
    monkeypatch.setenv("G6_PROMOTION_EVALUATOR_KEY_PATH", str(tmp_path / "evaluator.key"))
    demo_rechecker["a"] = "the artifact"
    witness = door_witness("demo", {"name": "a", "text": "the artifact"})
    assert _stated_verified(promotion_witness=witness).completion_state == "verified"
    assert _stated_verified().completion_state == "qualified-draft"


def test_a_key_path_with_no_evaluator_service_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The out-of-process service that path names is not in the library; every signed check used to answer a quiet
    'not signed' under it. It now refuses loudly."""
    from g6.core import promotion_evaluator

    monkeypatch.setenv("G6_PROMOTION_EVALUATOR_KEY_PATH", str(tmp_path / "evaluator.key"))
    with pytest.raises(RuntimeError, match="no evaluator service"):
        promotion_evaluator.evaluator_configured()