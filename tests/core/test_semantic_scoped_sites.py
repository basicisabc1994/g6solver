# SPDX-License-Identifier: Apache-2.0
import json
from types import SimpleNamespace

from g6.core import semantic
from g6.core.llm import FakeLLM


def test_repository_paraphrase_only_selects_existing_masked_handles(tmp_path):
    from g6.context.retrieve import Retriever
    (tmp_path / "animals.md").write_text("Felines nap.\nDogs bark.", encoding="utf-8")
    retriever = Retriever(tmp_path)
    with semantic.request(llm=None):
        assert not retriever.retrieve("sleepy cats").candidates
    with semantic.request(llm=FakeLLM(default='{"value":["0"]}')):
        result = retriever.retrieve("sleepy cats")
    assert result.candidates and result.candidates[0].file_path == "animals.md"
    with semantic.request(llm=FakeLLM(default='{"value":["../../outside"]}')):
        assert not retriever.retrieve("sleepy cats").candidates


def test_workspace_notes_read_by_meaning(tmp_path):
    from g6.context.workspace import Workspace
    workspace = Workspace(tmp_path)
    workspace.memory_dir.mkdir()
    (workspace.memory_dir / "animals.md").write_text("Felines nap.", encoding="utf-8")
    with semantic.request(llm=None):
        assert not workspace.memory_items("sleepy cats")
    with semantic.request(llm=FakeLLM(default='{"value":["0"]}')):
        items = workspace.memory_items("sleepy cats")
    assert items and items[0].source == "memory/animals.md" and items[0].trust == "untrusted"


def test_coding_access_paraphrase_adds_only_signal():
    from g6.workforce.coding import names_access
    from g6.intent_engine.schema import IntentContract
    intake = SimpleNamespace(metadata={"task_contract": SimpleNamespace(intent=IntentContract(goal="Rotate the login proof"))})
    with semantic.request(llm=None):
        assert not names_access(intake)
    with semantic.request(llm=FakeLLM(default='{"value":"R3"}')):
        assert names_access(intake)


def test_support_conflict_model_can_only_add():
    from g6._context_engine import _support
    # This helper is used by support_for after its scope and normative-grant checks.
    with semantic.request(llm=FakeLLM(default='{"value":true}')):
        assert _support.conflict_signal("Access is sanctioned", "Access is forbidden", False)
    with semantic.request(llm=FakeLLM(default='{"value":false}')):
        assert _support.conflict_signal("Access is permitted", "Access is not permitted", True)


def test_planner_reformulation_has_typed_fields_and_floor():
    from g6._context_engine._planner import reformulation_of
    payload = {"query": "sleepy cats", "unmet_needs": ["nap time"]}
    fake = FakeLLM(default='{"value":{"query":"felines nap","routes":["meaning"],"top_k":2}}')
    with semantic.request(llm=fake):
        assert reformulation_of(json.dumps(payload), fake)["query"] == "felines nap"
    with semantic.request(llm=FakeLLM(default='{"value":{"query":"x","sources":["outside"]}}')):
        assert reformulation_of(json.dumps(payload), fake) is None


def test_legal_advice_signal_adds_and_never_relaxes_filter():
    from g6.bundles.contract_review import advice_signal
    with semantic.request(llm=None):
        assert not advice_signal("You have my blessing to execute it", False)
    with semantic.request(llm=FakeLLM(default='{"value":true}')):
        assert advice_signal("You have my blessing to execute it", False)
    with semantic.request(llm=FakeLLM(default='{"value":false}')):
        assert advice_signal("I am your attorney", True)


def test_explicit_reformulation_caller_is_used_without_an_outer_request():
    from g6._context_engine._planner import reformulation_of
    fake = FakeLLM(default='{"value":{"query":"felines nap","routes":["meaning"],"top_k":2}}')
    assert reformulation_of("sleepy cats", fake)["query"] == "felines nap"
    assert len(fake.calls) == 1


def test_reformulation_accepts_declared_legacy_positions_in_an_implicit_scope(monkeypatch):
    from g6._context_engine._planner import reformulation_of
    monkeypatch.setattr(semantic, "resolve_caller", lambda: None)
    fake = FakeLLM(default='{"query":"felines nap","routes":["meaning"],"top_k":2}')
    with semantic.use_request():
        assert reformulation_of("sleepy cats", fake)["query"] == "felines nap"
    assert len(fake.calls) == 1


def test_blank_model_reformulation_is_an_invalid_reply():
    from g6._context_engine._planner import reformulation_of
    fake = FakeLLM(default='{"value":{"query":"   "}}')
    assert reformulation_of("sleepy cats", fake) is None
