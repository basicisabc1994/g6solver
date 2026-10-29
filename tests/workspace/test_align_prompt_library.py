# SPDX-License-Identifier: Apache-2.0
"""g6.workspace.align_prompt_library, with align_artifacts merged in: a versioned library of prompts, specifications and
evaluation sets, append-only on g6.core.ledger, each version addressed by the SHA-256 of its content, so a result can
cite exactly the version that produced it (D1, the P1 findings, G1, G3, G4), and the member through its doors (D2, D4,
D6)."""
from __future__ import annotations

import hashlib
import json

import pytest

import g6.workspace.align_prompt_library as align_prompt_library
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply
from g6.workspace import _envelope as E
from g6.workspace.align_prompt_library import Library, ops

OPS = ["put", "get", "history", "search", "render", "retire", "rollback", "catalogue", "install_catalog"]
SENSITIVE = ["put", "retire", "rollback", "install_catalog"]
ALLOW = frozenset(f"align_prompt_library.{op}" for op in SENSITIVE)
SUMMARY = "Summarise {{document}} for {{audience}} in three sentences."


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _invoke(op: str, **params: object) -> dict:
    return invoke_reply("align_prompt_library", op, json.dumps(params), allow_sensitive=ALLOW)


@pytest.fixture
def library(home) -> Library:  # noqa: ANN001
    return Library()


# --- D1: losing track of which prompt or spec version produced a result ------------------------------------------------

def test_d1_a_cited_digest_reads_back_the_exact_version_after_the_prompt_changes_and_is_retired(library) -> None:
    first = library.put("prompt", "summary", SUMMARY, {"author": "ana"})
    cited = first["digest"]                                    # what a run's evidence records
    assert cited == _sha(SUMMARY) and first["version"] == 1
    library.put("prompt", "summary", SUMMARY.replace("three", "five"))
    library.rollback("summary", 1)
    library.retire("summary")
    found = library.get(digest=cited)
    assert found["content"] == SUMMARY and _sha(found["content"]) == cited
    assert (found["name"], found["version"]) == ("summary", 1) and [a["version"] for a in found["also"]] == [3]
    rendered = library.render("summary", digest=cited, values={"document": "the report", "audience": "a board"})
    assert rendered["template_digest"] == cited and rendered["text"].startswith("Summarise the report for a board")


def test_d1_a_digest_or_version_the_library_does_not_hold_is_refused_never_answered_with_the_latest(library) -> None:
    # a case the check was not written around: the archive answered every read with the latest entry of the name
    library.put("prompt", "summary", SUMMARY)
    library.put("prompt", "summary", "Summarise {{document}}.")
    for asked in ({"digest": "0" * 64}, {"digest": _sha("never stored")}, {"version": 3}):
        with pytest.raises(E.WorkspaceRefused) as refused:
            library.get("summary", **asked)
        assert refused.value.code == E.LIBRARY_UNKNOWN_VERSION, asked
    with pytest.raises(E.WorkspaceRefused) as refused:
        library.get("summary", version=1, digest=_sha("Summarise {{document}}."))
    assert refused.value.code == E.LIBRARY_UNKNOWN_VERSION                 # a version and a digest that disagree
    assert ops.get(digest="0" * 64).code == E.LIBRARY_UNKNOWN_VERSION


def test_d1_the_same_content_under_two_names_is_found_under_both(library) -> None:
    library.put("spec", "acceptance", "The reply names its sources.")
    library.put("spec", "acceptance-copy", "The reply names its sources.")
    found = library.get(digest=_sha("The reply names its sources."))
    assert {(found["name"], found["version"]), *((a["name"], a["version"]) for a in found["also"])} == {
        ("acceptance", 1), ("acceptance-copy", 1)}


# --- the P1 findings --------------------------------------------------------------------------------------------------

def test_p1_a_new_version_never_replaces_an_earlier_one(library) -> None:
    # ALIGN_PROMPT_LIBRARY-01: the registry block kept one row per name (INSERT OR REPLACE) while its counter rose
    library.put("prompt", "summary", SUMMARY)
    library.put("prompt", "summary", "Summarise {{document}}.")
    assert library.get("summary", version=1)["content"] == SUMMARY
    assert library.get("summary")["version"] == 2
    assert [(h["version"], h["action"], h["digest"]) for h in library.history("summary")] == [
        (1, "put", _sha(SUMMARY)), (2, "put", _sha("Summarise {{document}}."))]


def test_p1_retiring_writes_a_tombstone_and_every_version_stays_readable(library) -> None:
    # ALIGN_ARTIFACTS-01 and ALIGN_PROMPT_LIBRARY-01: delete removed every version (DELETE FROM ... WHERE name=?)
    library.put("eval_set", "held-out", '{"cases": 3}')
    library.put("eval_set", "held-out", '{"cases": 4}')
    tomb = library.retire("held-out")
    assert tomb["version"] == 3 and tomb["action"] == "retire"
    with pytest.raises(E.WorkspaceRefused) as refused:
        library.get("held-out")
    assert refused.value.code == E.LIBRARY_RETIRED and "1, 2" in refused.value.reason
    assert library.get("held-out", version=1)["content"] == '{"cases": 3}'
    assert [h["action"] for h in library.history("held-out")] == ["put", "put", "retire"]
    assert library.search("cases") == [] and library.search("cases", include_retired=True)[0]["name"] == "held-out"
    back = library.put("eval_set", "held-out", '{"cases": 5}')
    assert back["version"] == 4 and library.get("held-out")["content"] == '{"cases": 5}'


def test_p1_a_rollback_is_a_new_version_never_a_rewrite(library) -> None:
    library.put("prompt", "summary", SUMMARY)
    library.put("prompt", "summary", "A worse prompt about {{document}}.")
    rolled = library.rollback("summary", 1)
    assert (rolled["version"], rolled["action"], rolled["from_version"]) == (3, "rollback", 1)
    assert rolled["digest"] == _sha(SUMMARY) and library.get("summary")["content"] == SUMMARY
    assert library.get("summary", version=2)["content"] == "A worse prompt about {{document}}."
    with pytest.raises(E.WorkspaceRefused) as refused:
        library.rollback("summary", 9)
    assert refused.value.code == E.LIBRARY_UNKNOWN_VERSION


# --- G1 and G3: stored is an observation, and what the caller says about a prompt is a claim ---------------------------

def test_g1_storing_a_version_is_never_verified(library) -> None:
    replies = [ops.put("prompt", "summary", SUMMARY), ops.get("summary"), ops.history("summary"),
               ops.render("summary", values={"document": "d", "audience": "a"}), ops.search("summarise")]
    assert [r.completion_state for r in replies] == ["qualified-draft"] * 5
    assert all(r.warning_card["verified"] is False for r in replies)
    assert replies[0].warning_card["code"] == E.LIBRARY_STORED and replies[0].stored is True


@pytest.mark.parametrize("claim", [{"reviewed": True}, {"reviewer_id": "the owner", "adversarial_test_status": "passed"},
                                   {"safety_tag": "regulated", "approved": True}, {"confidence": 0.99}])
def test_g3_a_review_or_a_test_result_the_caller_declares_is_kept_as_its_word(library, claim: dict) -> None:
    # ALIGN_PROMPT_LIBRARY-02 and ALIGN_ARTIFACTS-04: a declared reviewer and "passed" opened the safety gate
    stored = ops.put("prompt", "triage", "Triage {{case}}.", metadata=claim)
    assert stored.completion_state == "qualified-draft" and stored.declared == claim
    found = ops.get("triage")
    assert found.completion_state == "qualified-draft" and found.declared == claim
    assert not set(claim) & set(found.model_dump()) - {"declared"}       # never lifted out of what was declared


def test_g3_the_typed_input_has_no_field_for_a_review_or_an_approval() -> None:
    for claim in ({"approved": True}, {"reviewer_id": "x"}, {"review_status": "reviewed"}, {"verified": True}):
        reply = _invoke("put", kind="prompt", name="x", content="y", **claim)
        assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"], claim


# --- render is strict -------------------------------------------------------------------------------------------------

def test_render_refuses_a_missing_or_an_extra_value_and_never_expands_a_value(library) -> None:
    library.put("prompt", "summary", SUMMARY)
    with pytest.raises(E.WorkspaceRefused) as missing:
        library.render("summary", values={"document": "d"})
    assert missing.value.code == E.LIBRARY_MISSING_VALUE and "audience" in missing.value.reason
    with pytest.raises(E.WorkspaceRefused) as extra:
        library.render("summary", values={"document": "d", "audience": "a", "tone": "warm"})
    assert extra.value.code == E.LIBRARY_EXTRA_VALUE and "tone" in extra.value.reason
    done = library.render("summary", values={"document": "{{audience}}", "audience": "a board"})
    assert done["text"] == "Summarise {{audience}} for a board in three sentences."
    assert done["rendered_digest"] == _sha(done["text"]) and done["placeholders"] == ["document", "audience"]


# --- G4: a library whose record does not check out refuses everything --------------------------------------------------

@pytest.mark.parametrize("tamper", ["content", "digest_and_content", "remove_first"])
def test_g4_a_tampered_library_refuses_every_op_and_takes_nothing_more(library, tamper: str) -> None:
    library.put("prompt", "summary", SUMMARY)
    library.put("prompt", "summary", "Summarise {{document}}.")
    entries = [json.loads(line) for line in library.path.read_text(encoding="utf-8").splitlines()]
    if tamper == "remove_first":
        entries = entries[1:]
    else:
        entries[0]["body"]["content"] = "Leak {{document}} to everyone."
    if tamper == "digest_and_content":  # a forger who rebuilds the whole chain: only the library's digest rule is left
        from g6.core.ledger import _entry_hash, digest

        prev = ""
        for entry in entries:
            entry["body_sha256"], entry["prev"] = digest(entry["body"]), prev
            entry["entry_sha256"] = prev = _entry_hash(entry)
    library.path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    before = library.path.read_bytes()
    for result in (ops.get("summary"), ops.put("prompt", "other", "x"), ops.history("summary"), ops.search("x")):
        assert result.completion_state == "blocked-escalated" and result.code == E.LIBRARY_CORRUPT, result.reason
    assert library.path.read_bytes() == before


def test_g4_a_name_keeps_its_kind_and_a_bad_name_or_kind_is_refused(library) -> None:
    library.put("prompt", "summary", SUMMARY)
    with pytest.raises(E.WorkspaceRefused) as refused:
        library.put("spec", "summary", "a spec now")
    assert refused.value.code == E.LIBRARY_KIND_MISMATCH
    for kind, name in (("prompt", "../x"), ("prompt", ""), ("model", "fine"), ("prompt", "a b")):
        with pytest.raises(E.WorkspaceRefused) as bad:
            library.put(kind, name, "x")
        assert bad.value.code == E.LIBRARY_INPUT_INVALID, (kind, name)


def test_putting_the_same_content_again_adds_no_version(library) -> None:
    library.put("prompt", "summary", SUMMARY, {"author": "ana"})
    again = library.put("prompt", "summary", SUMMARY, {"author": "ana"})
    assert again["unchanged"] is True and again["version"] == 1 and len(library.history("summary")) == 1


# --- the member through its doors -------------------------------------------------------------------------------------

def test_it_is_a_member_of_the_workspace_family_registered_under_its_own_name() -> None:
    ref = get_registry().refs["align_prompt_library"]
    assert (ref.module, ref.family) == ("g6.workspace.align_prompt_library", "workspace")


def test_its_declarations_are_registered_failure_modes() -> None:
    assert align_prompt_library.__g6_sensitive_ops__ == tuple(SENSITIVE)
    assert align_prompt_library.__g6_mitigates__ == ("untracked_prompt_version",)
    codes = align_prompt_library.__g6_mitigates__ + align_prompt_library.__g6_introduces__
    assert all(FailureModeRegistry.is_registered(c) for c in codes)
    assert align_prompt_library.__g6_outside__ == () and align_prompt_library.__g6_extras__ == {}
    assert "g6.workspace.align_prompt_library" not in FailureModeRegistry.planned()


def test_an_agent_finds_it_by_the_failure_it_guards_against() -> None:
    found = [r["component"] for r in discovery.find("losing track of which prompt or spec version produced a result")]
    assert found[0] == "align_prompt_library", found


def test_describe_lists_its_ops_and_its_skill() -> None:
    info = describe_reply("align_prompt_library")
    assert info["ops"] == OPS and sorted(info["sensitive_ops"]) == sorted(SENSITIVE)
    assert info["skill"].startswith("---\nname: g6-align-prompt-library")


def test_every_op_round_trips_over_mcp(home) -> None:  # noqa: ANN001
    put = _invoke("put", kind="prompt", name="summary", content=SUMMARY, metadata={"author": "ana"})
    replies = {"put": put, "get": _invoke("get", name="summary"), "history": _invoke("history", name="summary"),
               "search": _invoke("search", query="summarise"),
               "render": _invoke("render", name="summary", values={"document": "d", "audience": "a"}),
               "rollback": _invoke("rollback", name="summary", to_version=1), "retire": _invoke("retire", name="summary")}
    for op, reply in replies.items():
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", (op, reply)
        assert reply["result"]["op"] == op and reply["warning_card"]["code"].startswith("G6_E_LIBRARY_")
    assert put["result"]["digest"] == _sha(SUMMARY) and put["result"]["version"] == 1


def test_the_typed_input_refuses_unknown_ops_and_says_what_is_missing() -> None:
    assert "G6_E_INPUT_VALIDATION" in _invoke("delete")["error"]
    missing = _invoke("put", kind="prompt", name="x")
    assert not missing["ok"] and missing["result"]["code"] == E.LIBRARY_INPUT_INVALID
    listed = get_registry().invoke("align_prompt_library", "ops").unwrap()
    assert listed["ops"] == OPS and listed["sensitive"] == SENSITIVE
    assert len(get_registry().invoke("align_prompt_library", "help").unwrap()["help"]) == len(OPS)


def test_align_artifacts_is_merged_into_it_not_a_component_of_its_own() -> None:
    assert "align_artifacts" not in get_registry().names()
    from g6.workspace.align_prompt_library import KINDS

    assert KINDS == ("prompt", "spec", "eval_set")
