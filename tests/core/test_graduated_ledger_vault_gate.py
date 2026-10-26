# SPDX-License-Identifier: Apache-2.0
"""The pieces graduated from research for the meta-harness (owner ruling Q5; audit 037).

Each fix has a twin that puts the research behaviour back and shows the defect return, and a survival control showing
ordinary use is unaffected. ``tests/research`` keeps covering the same code through the ECHA and AHI wrappers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core import adaptation_gate as gate
from g6.core import ledger as chain
from g6.core.adaptation_gate import AdaptationProposal, Control, decide_promotion, validate_locally
from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError
from g6.core.vault import Vault, guarded_read

H1, H2 = "a" * 64, "b" * 64


# --- the chain ----------------------------------------------------------------------------------------------------

def test_a_clean_chain_reopens_and_an_edited_entry_is_found(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    led = ChainLedger(path)
    led.append("run", "r1", {"x": 1})
    head = led.append("run", "r2", {"x": 2})
    assert ChainLedger(path, expect_head=head).head() == head
    rows = path.read_text(encoding="utf-8").splitlines()
    rows[0] = rows[0].replace('"x":1', '"x":9').replace('"x": 1', '"x": 9')
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert any("body does not match" in p for p in ChainLedger.audit(path))
    with pytest.raises(LedgerCorrupt):
        ChainLedger(path)


def test_a_returned_body_is_a_copy() -> None:
    led = ChainLedger()
    led.append("run", "r1", {"x": [1]})
    led.get("run", "r1")["x"].append(2)
    assert led.get("run", "r1") == {"x": [1]} and led.verify() == ()


def test_a_duplicate_id_and_a_validator_refusal_write_nothing(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    led = ChainLedger(path, validate=lambda kind, ident, body, seen: [] if body.get("ok") else [f"{ident}: not ok"])
    led.append("run", "r1", {"ok": True})
    for kind, ident, body in (("run", "r1", {"ok": True}), ("run", "r2", {"ok": False})):
        with pytest.raises(LedgerError):
            led.append(kind, ident, body)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


# LED-001: a second writer forks the chain -------------------------------------------------------------------------

def test_led001_a_writer_whose_file_moved_on_refuses(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    first, second = ChainLedger(path), ChainLedger(path)
    first.append("run", "r1", {"x": 1})
    with pytest.raises(LedgerError, match="changed since it was opened"):
        second.append("run", "r2", {"x": 2})
    assert ChainLedger.audit(path) == ()  # the file is still one clean chain


def test_led001_twin_without_the_check_the_chain_forks(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "l.jsonl"
    first, second = ChainLedger(path), ChainLedger(path)
    first.append("run", "r1", {"x": 1})
    monkeypatch.setattr(chain, "_file_head", lambda p: second.head())  # the research ledger never looked
    second.append("run", "r2", {"x": 2})
    assert ChainLedger.audit(path)  # two entries claim seq 0: the chain no longer verifies


def test_led001_survival_one_writer_appends_freely(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    led = ChainLedger(path)
    for i in range(30):
        led.append("run", f"r{i}", {"i": i, "pad": "x" * 300})
    assert ChainLedger.audit(path, expect_head=led.head()) == ()


def test_led001_a_removed_file_is_refused_too(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    led = ChainLedger(path)
    led.append("run", "r1", {"x": 1})
    path.unlink()
    with pytest.raises(LedgerError, match="changed since it was opened"):
        led.append("run", "r2", {"x": 2})


# LED-002: NaN and infinities ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_led002_a_non_finite_number_is_refused_on_write(value: float) -> None:
    with pytest.raises(LedgerError):
        ChainLedger().append("run", "r1", {"score": value})


def test_led002_a_file_with_nan_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    ChainLedger(path).append("run", "r1", {"score": 1.0})
    path.write_text(path.read_text(encoding="utf-8").replace("1.0", "NaN"), encoding="utf-8")
    assert any("NaN or infinity" in p for p in ChainLedger.audit(path))


def test_led002_twin_the_research_encoding_wrote_nan(monkeypatch) -> None:
    monkeypatch.setattr(chain, "canonical", lambda obj: json.dumps(obj, sort_keys=True, separators=(",", ":")))
    led = ChainLedger()
    led.append("run", "r1", {"score": float("nan")})  # accepted: the file would not be standard JSON
    assert "NaN" in json.dumps(led.entries()[0]["body"])


# LED-003: digest of values JSON cannot hold -------------------------------------------------------------------------

def test_led003_a_set_or_an_object_cannot_be_digested() -> None:
    for value in ({"a", "b"}, object(), {"k": Path("x")}):
        with pytest.raises(ValueError, match="not JSON"):
            chain.digest(value)


_RESEARCH_DIGEST = (
    "import hashlib, json; "
    "s = set('abcdefgh'); "
    "print(hashlib.sha256(json.dumps(s, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest())"
)


def test_led003_twin_the_research_digest_of_a_set_depends_on_the_process() -> None:
    def run(seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        return subprocess.run([sys.executable, "-c", _RESEARCH_DIGEST], capture_output=True, text=True, env=env,
                              check=True).stdout.strip()
    assert len({run(str(seed)) for seed in range(1, 6)}) > 1  # same set, different digest per process


# LED-004: the whole file removed --------------------------------------------------------------------------------------

def test_led004_a_missing_file_does_not_open_against_a_kept_head(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    head = ChainLedger(path).append("run", "r1", {"x": 1})
    path.unlink()
    with pytest.raises(LedgerCorrupt, match="head differs"):
        ChainLedger(path, expect_head=head)
    assert ChainLedger(path).head() == ""  # survival: unanchored, a new ledger opens as before


# --- the vault ----------------------------------------------------------------------------------------------------

class _Log:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def add_vault(self, vault_id, digest, *, files, roots):
        self.rows.append(("vault", vault_id, digest))

    def add_incident(self, kind, **fields):
        self.rows.append(("incident", kind, fields))


def _sealed(tmp_path: Path) -> tuple[Vault, Path, _Log]:
    hidden = tmp_path / "held_out"
    hidden.mkdir()
    (hidden / "answers.json").write_text('{"c1": 4}', encoding="utf-8")
    log = _Log()
    return Vault.seal([hidden], ledger=log, vault_id="v1"), hidden, log


def test_the_vault_refuses_a_learner_and_records_it(tmp_path: Path) -> None:
    vault, hidden, log = _sealed(tmp_path)
    with pytest.raises(PermissionError):
        guarded_read(vault, "learner", hidden / "answers.json", ledger=log)
    assert log.rows[-1][:2] == ("incident", "access_denied")
    assert guarded_read(vault, "evaluator", hidden / "answers.json") == b'{"c1": 4}'


def test_vlt001_the_manifest_cannot_be_edited(tmp_path: Path) -> None:
    vault, hidden, _ = _sealed(tmp_path)
    with pytest.raises(TypeError):
        vault.manifest["0/answers.json"] = "0" * 64  # type: ignore[index]
    (hidden / "answers.json").write_text('{"c1": 5}', encoding="utf-8")
    assert vault.verify_unchanged() == ("0/answers.json changed since sealing",)


def test_vlt001_twin_an_editable_manifest_hides_a_change(tmp_path: Path) -> None:
    vault, hidden, _ = _sealed(tmp_path)
    object.__setattr__(vault, "manifest", dict(vault.manifest))  # the research vault held a plain dict
    (hidden / "answers.json").write_text('{"c1": 5}', encoding="utf-8")
    vault.manifest["0/answers.json"] = __import__("hashlib").sha256(b'{"c1": 5}').hexdigest()
    assert vault.verify_unchanged() == ()  # the change is hidden


# --- the adaptation gate ----------------------------------------------------------------------------------------------

def _proposal(**over) -> AdaptationProposal:
    base = dict(proposal_id="p1", proposer="learner", source_case_ids=("c1",), target_component="harness.hints",
                intended_change="add hint h3", predicted_benefit="fewer unit errors", falsifiers=("no change",),
                test_plan="held-out split", candidate_hash=H1, baseline_hash=H2, transfer_hypothesis="similar tasks",
                cost_cap=1.0, rollback="drop h3")
    return AdaptationProposal(**(base | over))


NEG = Control("neg", "negative", {"bad": True}, "task-author")
POS = Control("pos", "positive", {"bad": False}, "task-author")
KW = dict(policy_version="v", reviewer="owner", owner_confirmed=True, regression="pass", budget="pass", security="pass")


def _guard(case) -> bool:
    return bool(case["bad"])


def test_the_gate_grants_shadow_use_at_most() -> None:
    p = _proposal()
    assert decide_promotion(p, validate_locally(p, _guard, [NEG, POS]), **KW).disposition == "shadow_approved"


def test_gate001_an_automated_reviewer_is_refused() -> None:
    p = _proposal()
    d = decide_promotion(p, validate_locally(p, _guard, [NEG, POS]), **(KW | {"reviewer": "auto:ci-bot"}))
    assert d.disposition == "blocked" and any("automated" in r for r in d.reasons)


def test_gate001_twin_without_the_rule_a_bot_promotes(monkeypatch) -> None:
    monkeypatch.setattr(gate, "is_automated", lambda name: False)
    p = _proposal()
    d = decide_promotion(p, validate_locally(p, _guard, [NEG, POS]), **(KW | {"reviewer": "auto:ci-bot"}))
    assert d.disposition == "shadow_approved"


def test_gate002_a_proposal_edited_after_validation_is_blocked() -> None:
    validated = _proposal()
    edited = replace(validated, intended_change="add hint h3 and drop check c2")
    d = decide_promotion(edited, validate_locally(validated, _guard, [NEG, POS]), **KW)
    assert d.disposition == "blocked" and any("different version" in r for r in d.reasons)


def test_gate002_an_unbound_validation_never_passes() -> None:
    p = _proposal()
    unbound = replace(validate_locally(p, _guard, [NEG, POS]), proposal_digest="")
    assert decide_promotion(p, unbound, **KW).disposition == "blocked"


@pytest.mark.parametrize("path", [
    "src/g6/metaharness/gate.py", "src/g6/invariants/envelope.py", "tests/test_hints.py", "pyproject.toml",
    "src/g6/core/stop.py", "src/g6/core/approval_store.py", "conftest.py", "src/g6/workforce/verify.py",
    "SRC\\G6\\CORE\\LEDGER.PY",
])
def test_gate003_what_judges_a_proposal_is_protected(path: str) -> None:
    p = _proposal(touched_paths=(path,))
    assert decide_promotion(p, validate_locally(p, _guard, [NEG, POS]), **KW).disposition == "blocked"


@pytest.mark.parametrize("path", ["src/g6/metaharness/gate.py", "tests/test_hints.py", "pyproject.toml"])
def test_gate003_twin_the_research_list_let_these_through(path: str, monkeypatch) -> None:
    monkeypatch.setattr(gate, "PROTECTED_FRAGMENTS", ("oracle", "fixture", "grader", "standard", "threshold",
                                                       "promotion", "evaluator", "answer_key", "holdout", "held_out"))
    monkeypatch.setattr(gate, "_TEST_SEGMENT", __import__("re").compile(r"(?!)"))
    p = _proposal(touched_paths=(path,))
    assert decide_promotion(p, validate_locally(p, _guard, [NEG, POS]), **KW).disposition == "shadow_approved"


@pytest.mark.parametrize("path", ["docs/latest.md", "examples/hints.md", "README.md"])
def test_gate003_survival_ordinary_paths_are_not_protected(path: str) -> None:
    assert gate.protected_hits((path,)) == []
