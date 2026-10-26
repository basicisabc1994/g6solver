# SPDX-License-Identifier: Apache-2.0
"""The door rule (DECISIONS #40, plan A1): ``verified`` stands only when G6's own code re-runs a check at the door
and reproduces it; a witness the producer hands over is ignored. Offline."""
from __future__ import annotations

import re
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
# The core law: a stated `verified` stands only on a re-check a G6 door ran, bound to what the envelope is about
# --------------------------------------------------------------------------------------------------------------------

def _stated_verified(**fields):
    from g6.core.reliability_envelope import ReliabilityEnvelope

    return ReliabilityEnvelope(completion_state="verified", reliability_label="verified", **fields)


def _tree(base: Path) -> tuple[Path, str]:
    """A small tree and the pin a caller recorded for it before any work."""
    from g6.integrity import build_manifest, manifest_sha256

    root = base / "pkg"
    root.mkdir(parents=True)
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    return root, manifest_sha256(build_manifest(root))


def _integrity_record(root: Path, pin: str) -> dict:
    """What G6's integrity check returns for ``root``, as a door would see it: its own fields and its label."""
    from dataclasses import asdict

    from g6.integrity import build_manifest, verify_tree

    result = verify_tree(root, build_manifest(root), expected_manifest_sha256=pin)
    return {**asdict(result), "completion_state": result.reliability_label}


def _integrity_door(value: dict, caller: dict | None, **fields) -> dict:
    """The door for G6's own integrity code: the producer is ``verify_tree`` itself, the pin is the caller's."""
    from g6.core.door import door_envelope
    from g6.integrity import verify_tree

    return door_envelope(value.get("completion_state"), who="g6.integrity", producer=verify_tree, value=value,
                         caller=caller, **fields)


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


def test_a_self_minted_witness_does_not_promote(tmp_path: Path) -> None:
    """The producer names G6's own integrity oracle over a tree it pinned itself, and says something else entirely:
    a witness handed to the envelope by its producer never promotes."""
    from g6.core.reliability_envelope import build_promotion_witness, envelope_fields

    witness = build_promotion_witness(evaluator_id="agent:producer", execution_id="exec-1", oracle="producer_tests",
                                      run_id="run-1")
    assert _stated_verified(run_id="run-1", promotion_witness=witness).completion_state == "qualified-draft"
    root, pin = _tree(tmp_path)
    details = {"root": str(root.resolve()), "manifest": _integrity_record(root, pin)["manifest"],
               "expected_manifest_sha256": pin, "patterns": ["**/*.py"]}
    minted = build_promotion_witness(evaluator_id="agent:producer", execution_id=pin, oracle="g6.integrity.tree",
                                     details=details)
    held = envelope_fields(reliability_label="verified", evidence=[{"summary": "the patient is fine"}],
                           promotion_witness=minted)
    assert held["completion_state"] == "qualified-draft"
    assert "not built at a G6 door" in held["degradation_reason"]


def test_an_oracle_anyone_else_registers_does_not_promote(monkeypatch: pytest.MonkeyPatch) -> None:
    """``register_oracle`` is open to any code; the law reads only the re-checkers G6 ships (``_G6_RECHECKERS``)."""
    from g6.core import promotion_evaluator
    from g6.core.reliability_envelope import build_promotion_witness

    monkeypatch.setattr(promotion_evaluator, "_ORACLE_REPLAYS", {})
    promotion_evaluator.register_oracle("producer_tests",
                                        lambda w: promotion_evaluator.ReplayOutcome(True, execution_id="exec-1"))
    witness = build_promotion_witness(evaluator_id="agent:producer", execution_id="exec-1", oracle="producer_tests")
    assert _stated_verified(promotion_witness=witness).completion_state == "qualified-draft"


def test_g6s_integrity_check_promotes_with_the_callers_pin_and_says_what_it_rechecked(tmp_path: Path) -> None:
    root, pin = _tree(tmp_path)
    envelope = _integrity_door(_integrity_record(root, pin), {"root": str(root), "expected_manifest_sha256": pin})
    assert envelope["completion_state"] == envelope["reliability_label"] == "verified"
    assert [e["kind"] for e in envelope["evidence"]] == ["g6_recheck"]
    assert envelope["evidence"][0]["subject"] == str(root.resolve())


@pytest.mark.parametrize("caller", [None, {}, {"root": "<root>"}, {"expected_manifest_sha256": "<pin>"}])
def test_a_pin_the_caller_did_not_give_does_not_promote(tmp_path: Path, caller) -> None:
    """The result carries its own pin; the re-check compares against the caller's, never the output's."""
    root, pin = _tree(tmp_path)
    if caller:
        caller = {k: str(root) if v == "<root>" else pin for k, v in caller.items()}
    assert _integrity_door(_integrity_record(root, pin), caller)["completion_state"] == "qualified-draft"


def test_a_caller_pin_that_is_not_the_manifests_blocks(tmp_path: Path) -> None:
    root, pin = _tree(tmp_path)
    envelope = _integrity_door(_integrity_record(root, pin), {"root": str(root), "expected_manifest_sha256": "0" * 64})
    assert envelope["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize("extra", [{"answer": "the patient is fine"}, {"rows": [{"label": "ok"}]},
                                   {"evidence": [{"summary": "all tests passed"}]}, {"promotion_witness": {}}])
def test_a_result_with_a_claim_the_recheck_did_not_examine_is_held(tmp_path: Path, extra: dict) -> None:
    root, pin = _tree(tmp_path)
    envelope = _integrity_door({**_integrity_record(root, pin), **extra},
                               {"root": str(root), "expected_manifest_sha256": pin})
    assert envelope["completion_state"] == "qualified-draft"


def test_a_tree_changed_after_the_check_blocks_and_rebuilding_reruns_it(tmp_path: Path) -> None:
    from g6.core.reliability_envelope import ReliabilityEnvelope

    root, pin = _tree(tmp_path)
    caller = {"root": str(root), "expected_manifest_sha256": pin}
    promoted = _integrity_door(_integrity_record(root, pin), caller)
    assert promoted["completion_state"] == "verified"
    (root / "a.py").write_text("A = 'edited later'\n", encoding="utf-8")
    assert ReliabilityEnvelope(**promoted).completion_state == "blocked-escalated"
    assert _integrity_door(_integrity_record(root, pin), caller)["completion_state"] == "blocked-escalated"


def test_a_door_witness_from_another_run_is_held(tmp_path: Path) -> None:
    from g6.core.reliability_envelope import ReliabilityEnvelope

    root, pin = _tree(tmp_path)
    promoted = _integrity_door(_integrity_record(root, pin), {"root": str(root), "expected_manifest_sha256": pin},
                               run_id="run-1")
    moved = ReliabilityEnvelope(**{**promoted, "run_id": "run-2"})
    assert moved.completion_state == "qualified-draft" and "never transfers between runs" in moved.degradation_reason


def test_a_witness_for_one_tree_does_not_promote_an_envelope_about_another(tmp_path: Path) -> None:
    """Both trees are intact; tree A's door witness on an envelope whose evidence is tree B's re-check is held."""
    from g6.core.reliability_envelope import ReliabilityEnvelope

    root_a, pin_a = _tree(tmp_path / "a")
    root_b, pin_b = _tree(tmp_path / "b")
    a = _integrity_door(_integrity_record(root_a, pin_a), {"root": str(root_a), "expected_manifest_sha256": pin_a})
    b = _integrity_door(_integrity_record(root_b, pin_b), {"root": str(root_b), "expected_manifest_sha256": pin_b})
    assert a["completion_state"] == b["completion_state"] == "verified"
    swapped = ReliabilityEnvelope(**{**b, "promotion_witness": a["promotion_witness"]})
    assert swapped.completion_state == "qualified-draft"
    assert "not the re-checked artifact" in swapped.degradation_reason
    elsewhere = ReliabilityEnvelope(**{**a, "evidence": [{"summary": "the patient is fine"}]})
    assert elsewhere.completion_state == "qualified-draft"


def test_the_recheck_is_chosen_by_where_the_code_lives_never_by_a_name(tmp_path: Path) -> None:
    from g6.core.door import door_envelope

    root, pin = _tree(tmp_path)
    value, caller = _integrity_record(root, pin), {"root": str(root), "expected_manifest_sha256": pin}

    class Impostor:
        """Claims to live in G6's integrity package."""

    Impostor.__module__ = "g6.integrity.tree"

    def integrity(**kwargs):  # a function named for the component
        return kwargs

    for producer in (None, "integrity", "g6.integrity", " INTEGRITY ", Impostor, integrity):
        envelope = door_envelope("verified", producer=producer, value=value, caller=caller)
        assert envelope["completion_state"] == "qualified-draft", producer


def test_a_configured_signing_key_neither_promotes_nor_blocks(key_file, monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path) -> None:
    """The law reads no signature, so neither a key in the key file nor a key path (whose evaluator service the
    library does not have) can block a result G6's re-check reproduces, or lift one it does not."""
    key_file("G6_PROMOTION_EVALUATOR_KEY=test-evaluator-secret-key-abc123\n")
    monkeypatch.setenv("G6_PROMOTION_EVALUATOR_KEY_PATH", str(tmp_path / "evaluator.key"))
    root, pin = _tree(tmp_path)
    caller = {"root": str(root), "expected_manifest_sha256": pin}
    assert _integrity_door(_integrity_record(root, pin), caller)["completion_state"] == "verified"
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
    """A registry with no entries: whatever it answers comes from no code G6 can identify."""

    def __init__(self, value) -> None:
        self.value = value

    def invoke(self, component, op, params, *, block_sensitive=False, **kwargs):  # noqa: ANN001, ANN201
        from returns.result import Success

        return Success(self.value)


class _EntryRegistry(_Registry):
    """A registry entry whose block is ``block``, listed as ``source``: how the door learns which code answered."""

    def __init__(self, value, block, *, source: str = "builtin", module: str = "g6.integrity") -> None:
        from g6.core.registry import ComponentRef

        super().__init__(value)
        self.block = block
        self.refs = {"integrity_check": ComponentRef("integrity_check", module, source)}

    def get_class(self, name):  # noqa: ANN001, ANN201
        return self.block


@pytest.fixture
def planted_block(monkeypatch: pytest.MonkeyPatch):
    """A stand-in for an integrity block G6 does not ship: a block class planted into ``g6.integrity`` for the test,
    so it really lives in G6's integrity package and the door selects the re-checker by where the code lives. No
    component reachable over MCP has a re-checker today, so this is the only way to exercise that path there."""
    import g6.integrity as integrity
    from g6.core import AIBlock

    class Check(AIBlock):
        pass

    Check.__module__, Check.__qualname__ = "g6.integrity", "_PlantedCheck"
    monkeypatch.setattr(integrity, "_PlantedCheck", Check, raising=False)
    return Check


def _mcp(component: str, value, *, registry=None, params: dict | None = None) -> dict:
    import json

    from g6.invariants import check_envelope
    from g6.mcp import invoke_reply

    found = invoke_reply(component, "op", json.dumps(params or {}), registry=registry or _Registry(value),
                         allow_sensitive=frozenset())
    assert check_envelope(found) == [], found
    return found


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


@pytest.mark.parametrize("value", [{"reliability_label": "blocked-escalated"},
                                   {"completion_state": "verified", "reliability_label": "blocked-escalated"},
                                   {"completion_state": "blocked_escalated"}])
def test_the_door_takes_the_worse_of_the_two_state_fields_and_never_raises_a_block(value: dict) -> None:
    assert _mcp("demo", value)["completion_state"] == "blocked-escalated"


def test_every_verified_anywhere_in_a_held_result_is_corrected() -> None:
    found = _mcp("demo", {"completion_state": "verified_ready",
                          "steps": [{"completion_state": "verified", "reliability_label": "VERIFIED"}],
                          "trust": {"state": "Verified-Ready"},
                          "warning_card": {"code": "G6_E_OK", "verified": True}})
    result = found["result"]
    assert found["completion_state"] == "qualified-draft" and result["completion_state"] == "qualified-draft"
    assert result["steps"][0] == {"completion_state": "qualified-draft", "reliability_label": "qualified-draft"}
    assert result["trust"]["state"] == "qualified-draft" and result["warning_card"]["verified"] is False


def test_a_shipped_component_that_says_verified_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reachable path: the real registry, a built-in component, and no re-checker for its package."""
    from g6.core.registry import get_registry
    from g6.core.result import Result
    from g6.mcp import invoke_reply

    block = get_registry().get_class("action_gating")
    monkeypatch.setattr(block, "infer", lambda self, data: Result.ok({"completion_state": "verified",
                                                                       "evidence": [{"kind": "test"}]}))
    found = invoke_reply("action_gating", "evaluate", '{"action": {"action_type": "read_file"}}')
    assert found["completion_state"] == "qualified-draft" and found["warning_card"]["code"] == "G6_E_PROMOTION_LAW"


def test_g6s_own_integrity_code_promotes_over_mcp_with_the_callers_pin(tmp_path: Path, planted_block) -> None:
    root, pin = _tree(tmp_path)
    registry = _EntryRegistry(_integrity_record(root, pin), planted_block)
    found = _mcp("integrity_check", None, registry=registry,
                 params={"root": str(root), "expected_manifest_sha256": pin})
    assert found["completion_state"] == "verified" and found["result"]["completion_state"] == "verified"
    assert [e["kind"] for e in found["evidence"]] == ["g6_recheck"] and "claimed_completion_state" not in found


def test_over_mcp_a_producer_pin_extra_fields_or_an_extension_are_held(tmp_path: Path, planted_block) -> None:
    root, pin = _tree(tmp_path)
    record, caller = _integrity_record(root, pin), {"root": str(root), "expected_manifest_sha256": pin}
    assert _mcp("integrity_check", None, registry=_EntryRegistry(record, planted_block),
                params={"root": str(root)})["completion_state"] == "qualified-draft"
    assert _mcp("integrity_check", None, registry=_EntryRegistry({**record, "answer": "fine"}, planted_block),
                params=caller)["completion_state"] == "qualified-draft"
    assert _mcp("integrity_check", None, registry=_EntryRegistry(record, planted_block, source="extension"),
                params=caller)["completion_state"] == "qualified-draft"


def test_a_failed_recheck_is_blocked_over_mcp(tmp_path: Path, planted_block) -> None:
    root, pin = _tree(tmp_path)
    record = _integrity_record(root, pin)
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    found = _mcp("integrity_check", None, registry=_EntryRegistry(record, planted_block),
                 params={"root": str(root), "expected_manifest_sha256": pin})
    assert found["completion_state"] == "blocked-escalated" and found["result"]["completion_state"] == "blocked-escalated"


def test_a_producers_witness_is_ignored_over_mcp(tmp_path: Path, planted_block) -> None:
    """Even a door witness that would reproduce is not read from a result: the door builds its own."""
    root, pin = _tree(tmp_path)
    earned = _integrity_door(_integrity_record(root, pin), {"root": str(root), "expected_manifest_sha256": pin})
    found = _mcp("demo", {"completion_state": "verified", "evidence": earned["evidence"],
                          "promotion_witness": earned["promotion_witness"]})
    assert found["completion_state"] == "qualified-draft"


def test_a_transferred_witness_is_held_over_mcp(tmp_path: Path, planted_block) -> None:
    """Tree A's door witness, attached to an intact result about tree B, does not carry B through the door."""
    root_a, pin_a = _tree(tmp_path / "a")
    root_b, pin_b = _tree(tmp_path / "b")
    earned = _integrity_door(_integrity_record(root_a, pin_a), {"root": str(root_a), "expected_manifest_sha256": pin_a})
    value = {**_integrity_record(root_b, pin_b), "promotion_witness": earned["promotion_witness"]}
    found = _mcp("integrity_check", None, registry=_EntryRegistry(value, planted_block),
                 params={"root": str(root_b), "expected_manifest_sha256": pin_b})
    assert found["completion_state"] == "qualified-draft"


# --------------------------------------------------------------------------------------------------------------------
# The CLI door: `g6 invoke` prints door_reply(...)["result"], so it says what g6_invoke says
# --------------------------------------------------------------------------------------------------------------------

def _cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], component: str, registry,
         params: dict | None = None) -> dict:
    import json

    from g6.cli import main as cli
    from g6.core import registry as registry_module

    monkeypatch.setattr(registry_module, "get_registry", lambda: registry)
    assert cli(["invoke", component, "op", json.dumps(params or {})]) == 0
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

    printed = _cli(monkeypatch, capsys, "demo", _Registry(value))
    assert printed == json.loads(json.dumps(_mcp("demo", value)["result"], default=str))


def test_cli_holds_a_components_own_verified(monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    printed = _cli(monkeypatch, capsys, "demo", _Registry({"completion_state": "verified", "evidence": [{"kind": "t"}]}))
    assert printed["completion_state"] == "qualified-draft" and printed["claimed_completion_state"] == "verified"


def test_cli_and_mcp_agree_on_g6s_own_integrity_code(monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str], tmp_path: Path,
                                                     planted_block) -> None:
    root, pin = _tree(tmp_path)
    registry = _EntryRegistry(_integrity_record(root, pin), planted_block)
    caller = {"root": str(root), "expected_manifest_sha256": pin}
    assert _cli(monkeypatch, capsys, "integrity_check", registry, caller)["completion_state"] == "verified"
    assert _cli(monkeypatch, capsys, "integrity_check", registry, {"root": str(root)})["completion_state"] == (
        "qualified-draft")


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


def test_the_docs_say_no_door_gives_verified_today_and_what_the_raw_call_is() -> None:
    """Plan A1 review: under the door rule no component reachable through a door has a re-check of its own, which the
    README, AGENT_GUIDE and g6_help say plainly; and get_registry().invoke is the raw library call, not a door."""
    from g6.core import discovery

    readme = (SRC.parents[1] / "README.md").read_text(encoding="utf-8")
    guide = (SRC.parents[1] / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    for name, text in (("README.md", readme), ("AGENT_GUIDE.md", guide), ("g6_help", discovery.HELP)):
        plain = _flat(text).replace("`", "")
        assert "nothing that comes through them is verified" in plain, name
        assert "raw library call beneath the doors" in plain, name
    assert "the same flow is" not in _flat(guide)
    for door in ("an agent's tool calls", "a navigator plan's steps"):
        assert door in _flat(readme).replace("`", ""), door