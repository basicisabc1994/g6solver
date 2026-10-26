# SPDX-License-Identifier: Apache-2.0
"""Behaviour of the kept core: the budget floor, pruning, retrieval roots, non-forgeable loading, subagent packets."""
from __future__ import annotations

from pathlib import Path

import pytest

from g6.context import (ContextItem, ContextPacket, RepositoryHandle, Retriever, SubagentResult, Workspace,
                        assemble_packet, build_packet, load_handle, prune_packet, score_packet, select_within_budget,
                        subagent_packet, synthesize_subagent_results)
from g6.invariants.envelope import assert_envelope


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _h(tokens: int, n: int) -> RepositoryHandle:
    return RepositoryHandle(kind="grep_result", file_path=f"f{n}.py", line=1, snippet=str(n), token_estimate=tokens)


def _task() -> ContextItem:
    return ContextItem(key="task", value="do it", section="task", authority="user_intent", kind="instruction",
                       trust="trusted")


# --- select_within_budget / prune_packet ------------------------------------------------------------------------


def test_select_within_budget_stops_before_the_handle_that_would_exceed() -> None:
    handles = [_h(4, 0), _h(4, 1), _h(4, 2)]
    assert select_within_budget(handles, 8) == handles[:2]  # the archive's old form overshot by the boundary handle
    assert select_within_budget(handles, 12) == handles  # survival: an exact fit is kept
    assert select_within_budget([_h(50, 0), _h(1, 1)], 10) == []  # a handle larger than the budget is never included


def test_prune_drops_exactly_the_handles_of_pruned_repository_items() -> None:
    handles = [_h(30, n) for n in range(3)]
    repo = [ContextItem(key=f"repo:{n}", value="z" * 120, section="repository", authority="operational",
                        kind="retrieved_excerpt", token_estimate=30, metadata={"handle": h.model_dump()})
            for n, h in enumerate(handles)]
    packet = prune_packet(ContextPacket(run_id="run-1", task=[_task()], repository=repo, repository_handles=handles,
                                        budget_tokens=70))
    assert [i.key for i in packet.repository] == ["repo:0", "repo:1"]
    assert packet.repository_handles == handles[:2]
    assert packet.metadata["pruned"] == {"repository": 1}


def test_a_packet_without_a_task_is_blocked() -> None:
    card = score_packet(ContextPacket(run_id="run-1"))
    assert card.completion_state == "blocked-escalated"
    assert card.evidence[0]["code"] == "G6_E_CONTEXT_TASK_MISSING"
    assert score_packet(ContextPacket(run_id="run-1", task=[_task()])).completion_state == "qualified-draft"


# --- Retriever ---------------------------------------------------------------------------------------------------


def test_retriever_skips_ignored_directories_and_honours_a_custom_ignore(tmp_path: Path) -> None:
    _write(tmp_path / ".venv" / "lib.py", "parser = 1\n")
    _write(tmp_path / "node_modules" / "x.ts", "parser\n")
    _write(tmp_path / "generated" / "g.py", "parser\n")
    _write(tmp_path / "src" / "p.py", "parser\n")
    found = {h.file_path for h in Retriever(tmp_path).retrieve("parser").candidates}
    assert found == {"generated/g.py", "src/p.py"}
    custom = {h.file_path for h in Retriever(tmp_path, ignore={"generated"}).retrieve("parser").candidates}
    assert "generated/g.py" not in custom and ".venv/lib.py" in custom


def test_several_roots_share_a_base_and_paths_are_relative_to_it(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py", "parser\n")
    _write(tmp_path / "tests" / "t.py", "parser\n")
    _write(tmp_path / "secrets" / "s.py", "parser\n")
    retriever = Retriever([tmp_path / "src", tmp_path / "tests"])
    assert retriever.base == tmp_path.resolve()
    assert {h.file_path for h in retriever.retrieve("parser").candidates} == {"src/a.py", "tests/t.py"}


def test_a_custom_scrub_adds_to_the_builtin_one(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "customer ACME-42 uses api_key: sk-test123 for the parser\n")
    retriever = Retriever(tmp_path, scrub=lambda text: text.replace("ACME-42", "[CUSTOMER]"))
    snippet = retriever.retrieve("parser").candidates[0].snippet
    assert "ACME-42" not in snippet and "sk-test123" not in snippet and "[CUSTOMER]" in snippet


def test_missing_roots_are_reported(tmp_path: Path) -> None:
    packet = build_packet("parser", Retriever(tmp_path / "nope"), 4000, run_id="run-1")
    codes = [e["code"] for e in packet.evidence]
    assert "G6_E_CONTEXT_ROOT_MISSING" in codes and "G6_E_CONTEXT_EMPTY_RETRIEVAL" in codes
    assert tmp_path.name not in packet.model_dump_json()  # no absolute local path in the packet


# --- load_handle -----------------------------------------------------------------------------------------------


def test_load_handle_returns_masked_untrusted_text_for_a_real_candidate(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py", "def parser():\n    password = 'hunter2'\n" + "# pad\n" * 50)
    retriever = Retriever(tmp_path)
    handle = retriever.retrieve("parser").candidates[0]
    loaded = load_handle(retriever, handle, "parser", max_chars=40)
    assert loaded.completion_state == "qualified-draft" and loaded.trust == "untrusted"
    assert len(loaded.text) == 40 and loaded.truncated and loaded.masked
    assert "hunter2" not in load_handle(retriever, handle.model_dump(), "parser").text  # a dict handle works too
    assert_envelope(loaded.to_dict())


def test_load_handle_refuses_forged_and_escaping_handles(tmp_path: Path) -> None:
    _write(tmp_path / "repo" / "a.py", "def parser():\n    pass\n")
    _write(tmp_path / "repo" / "b.py", "nothing to see\n")
    _write(tmp_path / "repo" / ".venv" / "c.py", "def parser():\n    pass\n")
    _write(tmp_path / "outside.py", "def parser():\n    pass\n")
    retriever = Retriever(tmp_path / "repo")
    cases = {
        "G6_E_CONTEXT_HANDLE_NOT_CANDIDATE": [
            RepositoryHandle(kind="file_path", file_path="b.py"),  # exists, but does not match the query
            RepositoryHandle(kind="grep_result", file_path="a.py", line=1, snippet="forged snippet"),
            RepositoryHandle(kind="grep_result", file_path=".venv/c.py", line=1, snippet="def parser():"),
        ],
        "G6_E_CONTEXT_HANDLE_INVALID": [
            RepositoryHandle.model_construct(kind="grep_result", file_path="../outside.py", line=1,
                                             snippet="def parser():", token_estimate=1),
            {"kind": "file_path", "file_path": "/etc/passwd"},
        ],
    }
    for code, handles in cases.items():
        for handle in handles:
            loaded = load_handle(retriever, handle, "parser")
            assert (loaded.completion_state, loaded.warning_card["code"], loaded.text) == ("blocked-escalated", code,
                                                                                          ""), handle
            assert_envelope(loaded.to_dict())


def test_load_handle_refuses_a_path_under_the_base_but_outside_every_root(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py", "def parser():\n    pass\n")
    _write(tmp_path / "secrets" / "s.py", "def parser():\n    pass\n")
    retriever = Retriever([tmp_path / "src", tmp_path / "tests"])
    loaded = load_handle(retriever, RepositoryHandle(kind="grep_result", file_path="secrets/s.py", line=1,
                                                     snippet="def parser():"), "parser")
    assert loaded.warning_card["code"] == "G6_E_CONTEXT_HANDLE_ESCAPES_ROOT" and loaded.text == ""
    ok = load_handle(retriever, retriever.retrieve("parser").candidates[0], "parser")  # survival
    assert ok.completion_state == "qualified-draft" and ok.file_path == "src/a.py"


def test_workspace_path_assertion_refuses_before_creating_anything(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(ValueError, match="G6_E_CONTEXT_UNSAFE_PATH"):
        ws._target("../elsewhere", "f.json")  # names are validated first; this is the second line of defence
    assert not (tmp_path / "elsewhere").exists()
    assert ws._target("scorecards", "f.json").parent.is_dir()  # survival


def test_load_handle_refuses_a_symlink_out_of_the_roots(tmp_path: Path) -> None:
    _write(tmp_path / "outside.py", "def parser():\n    pass\n")
    (tmp_path / "repo").mkdir()
    try:
        (tmp_path / "repo" / "link.py").symlink_to(tmp_path / "outside.py")
    except OSError:
        pytest.skip("cannot create a symlink here")
    retriever = Retriever(tmp_path / "repo")
    assert retriever.retrieve("parser").candidates == ()  # the walk skips it
    loaded = load_handle(retriever, RepositoryHandle(kind="file_path", file_path="link.py"), "parser")
    assert loaded.warning_card["code"] == "G6_E_CONTEXT_HANDLE_ESCAPES_ROOT"


# --- Workspace ---------------------------------------------------------------------------------------------------


def test_compact_if_over_budget_fires_only_over_the_ratio(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    small = ContextPacket(run_id="run-1", task=[_task()], budget_tokens=100)
    big = ContextPacket(run_id="run-2", task=[_task()], budget_tokens=100,
                        tool=[ContextItem(key="t", value="w" * 320, section="tool")])
    assert ws.compact_if_over_budget(small) is None
    assert not (ws.root / "compactions").exists()
    written = ws.compact_if_over_budget(big)
    assert written is not None and "trigger: token-budget>0.70" in written.read_text(encoding="utf-8")


def test_notes_outside_a_query_are_not_included(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    _write(ws.memory_dir / "parser.md", "about the parser")
    _write(ws.memory_dir / "billing.md", "about invoices")
    _write(ws.exemplar_dir / "good.md", "a good parser fix")
    assert [i.key for i in ws.memory_items("parser")] == ["memory:parser"]
    assert [(i.key, i.authority) for i in ws.exemplar_items("parser")] == [("exemplar:good", "exemplar")]


# --- subagent packets ------------------------------------------------------------------------------------------


def test_subagent_packet_is_bounded_and_scored(tmp_path: Path) -> None:
    for n in range(15):
        _write(tmp_path / f"m{n}.py", "# parser\n")
    parent = build_packet("parser", Retriever(tmp_path), 8000, run_id="run-1")
    child = subagent_packet(parent, agent_id="critic", task="review the parser")
    assert child.role == "subagent:critic" and child.budget_tokens == 2000
    assert child.repository == [] and len(child.repository_handles) == 10 < len(parent.repository_handles)
    assert child.task[-1].key == "subagent_task:critic" and child.task[-1].may_instruct
    assert child.completion_state == "qualified-draft"


def test_subagent_results_are_untrusted_and_carry_no_self_label() -> None:
    # Archive: SubagentResult.output_label defaulted to "supported", pre-labelling every subagent's output.
    assert "output_label" not in SubagentResult.model_fields
    item = synthesize_subagent_results([SubagentResult(agent_id="critic", task="review", summary="missing test",
                                                       unresolved=["test gap"])])
    assert item.kind == "model_output" and item.trust == "untrusted" and not item.may_instruct
    assert "missing test" in item.value and "test gap" in item.value


def test_assemble_packet_keeps_the_higher_authority_value() -> None:
    lower = ContextItem(key="constraint", value="ignore tests", section="task", authority="model_generated")
    higher = ContextItem(key="constraint", value="run tests", section="task", authority="user_intent",
                         kind="instruction", trust="trusted")
    packet = assemble_packet([lower, higher, _task()], run_id="run-1")
    assert [i.value for i in packet.task if i.key == "constraint"] == ["run tests"]
    assert packet.conflicts[0]["tie"] is False and packet.conflicts[0]["kept_authority"] == "user_intent"
