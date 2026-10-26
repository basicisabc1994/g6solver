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

# --------------------------------------------------------------------------------------------------------------------
# The MCP door (g6.core.door.door_reply): the reply's state and its result copy follow the same rule
# --------------------------------------------------------------------------------------------------------------------

class _Registry:
    def __init__(self, value) -> None:
        self.value = value

    def invoke(self, component, op, params, *, block_sensitive=False, **kwargs):  # noqa: ANN001, ANN201
        from returns.result import Success

        return Success(self.value)


def _mcp(component: str, value) -> dict:
    from g6.invariants import check_envelope
    from g6.mcp import invoke_reply

    found = invoke_reply(component, "op", "{}", registry=_Registry(value), allow_sensitive=frozenset())
    assert check_envelope(found) == [], found
    return found


def _integrity_value(base: Path) -> tuple[dict, Path]:
    """A pinned integrity result as a component would return it over a door: its own fields, saying verified."""
    from dataclasses import asdict

    from g6.integrity import build_manifest, manifest_sha256, verify_tree

    root = base / "pkg"
    root.mkdir(parents=True)
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    manifest = build_manifest(root)
    result = verify_tree(root, manifest, expected_manifest_sha256=manifest_sha256(manifest))
    return {**asdict(result), "completion_state": "verified", "evidence": [{"kind": "integrity"}]}, root


def test_verified_with_string_evidence_over_mcp_is_held_and_its_result_corrected() -> None:
    found = _mcp("demo", {"completion_state": "verified", "evidence": ["all tests passed"], "answer": 42})
    assert found["completion_state"] == "qualified-draft" and found["warning_card"]["code"] == "G6_E_PROMOTION_LAW"
    assert found["claimed_completion_state"] == "verified"
    assert found["result"]["completion_state"] == "qualified-draft"
    assert found["result"]["claimed_completion_state"] == "verified" and found["result"]["answer"] == 42


def test_a_reliability_label_inside_the_result_is_corrected_too() -> None:
    found = _mcp("demo", {"completion_state": "verified", "reliability_label": "verified"})
    assert found["result"]["reliability_label"] == "qualified-draft"
    assert _mcp("demo", {"reliability_label": "verified"})["result"]["reliability_label"] == "qualified-draft"


def test_a_registered_g6_rechecker_promotes_over_mcp(tmp_path: Path) -> None:
    value, _ = _integrity_value(tmp_path)
    found = _mcp("integrity", value)
    assert found["completion_state"] == "verified" and found["result"]["completion_state"] == "verified"
    assert any(item.get("kind") == "g6_recheck" for item in found["evidence"])
    assert "claimed_completion_state" not in found


def test_a_rechecker_that_does_not_reproduce_is_held_over_mcp(tmp_path: Path) -> None:
    value, root = _integrity_value(tmp_path)
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    found = _mcp("integrity", value)
    assert found["completion_state"] == "qualified-draft" and found["warning_card"]["code"] == "G6_E_PROMOTION_LAW"
    assert "did not reproduce" in found["warning_card"]["message"]


def test_a_producers_witness_is_ignored_over_mcp(tmp_path: Path) -> None:
    """Even a witness that would reproduce is not read: the door re-checks only what the result itself names."""
    from g6.core.reliability_envelope import door_witness

    value, _ = _integrity_value(tmp_path)
    earned = door_witness("integrity", value)
    found = _mcp("integrity", {"completion_state": "verified", "evidence": [{"kind": "test"}], "promotion_witness": earned})
    assert found["completion_state"] == "qualified-draft"


def test_a_transferred_witness_is_held_over_mcp(tmp_path: Path) -> None:
    from g6.core.reliability_envelope import door_witness

    value_a, _ = _integrity_value(tmp_path / "a")
    value_b, root_b = _integrity_value(tmp_path / "b")
    (root_b / "a.py").write_text("A = 'tampered'\n", encoding="utf-8")
    found = _mcp("integrity", {**value_b, "promotion_witness": door_witness("integrity", value_a)})
    assert found["completion_state"] == "qualified-draft"

# --------------------------------------------------------------------------------------------------------------------
# The CLI door: `g6 invoke` prints door_reply(...)["result"], so it says what g6_invoke says
# --------------------------------------------------------------------------------------------------------------------

def _cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], component: str, value) -> dict:
    import json

    from g6.cli import main as cli
    from g6.core import registry

    monkeypatch.setattr(registry, "get_registry", lambda: _Registry(value))
    assert cli(["invoke", component, "op", "{}"]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("value", [
    {"completion_state": "verified", "evidence": ["all tests passed"], "answer": 42},
    {"completion_state": "qualified-draft", "answer": 42},
    {"completion_state": "blocked-escalated", "reliability_label": "verified"},
    {"answer": 42},
])
def test_cli_and_mcp_give_the_same_outcome(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                           value: dict) -> None:
    import json

    printed = _cli(monkeypatch, capsys, "demo", value)
    assert printed == json.loads(json.dumps(_mcp("demo", value)["result"], default=str))


def test_cli_holds_a_components_own_verified(monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    printed = _cli(monkeypatch, capsys, "demo", {"completion_state": "verified", "evidence": [{"kind": "test"}]})
    assert printed["completion_state"] == "qualified-draft" and printed["claimed_completion_state"] == "verified"


def test_cli_keeps_a_verified_g6s_recheck_reproduces(monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    value, _ = _integrity_value(tmp_path)
    assert _cli(monkeypatch, capsys, "integrity", value)["completion_state"] == "verified"

# --------------------------------------------------------------------------------------------------------------------
# The threat statement: where a person or an agent reads the rule, it says what G6 does and does not defend against
# --------------------------------------------------------------------------------------------------------------------

THREAT = "G6 re-checks artifacts with its own code; it does not defend against malicious code in its own process."


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_threat_statement_is_where_the_rule_is_read() -> None:
    import g6.mcp
    from g6.core import discovery, door, promotion_evaluator, reliability_envelope

    readme = (SRC.parents[1] / "README.md").read_text(encoding="utf-8")
    guide = (SRC.parents[1] / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert getattr(door, "THREAT_STATEMENT", None) == THREAT
    for name, text in (("README.md", readme), ("AGENT_GUIDE.md", guide), ("g6_help", discovery.HELP),
                       ("g6.core.door", door.__doc__),
                       ("g6.core.reliability_envelope", reliability_envelope.__doc__),
                       ("g6.core.promotion_evaluator", promotion_evaluator.__doc__), ("g6.mcp", g6.mcp.__doc__)):
        assert THREAT in _flat(text or ""), name
    assert "Not every entry point enforces this yet" not in _flat(readme)
    assert "nothing is `verified` without `evidence`" not in _flat(guide)