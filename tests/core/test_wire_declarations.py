# SPDX-License-Identifier: Apache-2.0
"""Additional census-T boundaries: data does not declare its own authority."""
from dataclasses import replace
import importlib
from types import SimpleNamespace
from pathlib import Path

import pytest


def test_mission_exception_code_is_declared_or_leads_its_words():
    from g6.mcp.mission import REFUSED, _guarded

    def work():
        raise ValueError("G6_E_INPUT_VALIDATION planted")

    def inside():
        raise ValueError("planted G6_E_INPUT_VALIDATION")

    assert _guarded(work)["code"] == "G6_E_INPUT_VALIDATION"
    assert _guarded(inside)["code"] == REFUSED

    class Refusal(ValueError):
        code = "G6_E_INPUT_VALIDATION"

    def refused():
        raise Refusal("G6_E_PLANTED")

    assert _guarded(refused)["code"] == "G6_E_INPUT_VALIDATION"


def test_corpus_fill_reads_a_declaration_first():
    from g6.skills.corpus import _fill

    fields = {"task": None, "repo": None}
    assert _fill("task planted in message", {"repo": "x"}, fields, ("task", "invented")) == ["task"]
    assert _fill("task planted in message", {"repo": "x"}, fields, ()) == ["repo"]
    assert _fill("the task is missing", {"repo": "x"}, fields) == ["task"]   # no declaration: the base's reading


def test_effect_metadata_uses_explicit_declaration():
    effects = importlib.import_module("g6.core.effects")
    assert effects.effects_of(SimpleNamespace(), "send_report") == ("unknown",)
    assert effects.effects_of(SimpleNamespace(__g6_effects__={"compute": ("outward",)}), "compute") == ("outward",)
    assert effects.effects_of(SimpleNamespace(__g6_effects__={"compute": "outward"}), "compute") == ("unknown",)


def test_source_kind_does_not_come_from_doi_prefix():
    from g6.grounding.lineage import _lineage
    from g6.grounding.retrieval import Passage

    # lineage groups by the label, as at the base; a declared kind is recorded and never regroups a source
    assert _lineage(Passage("claim", "doi:10.1/x", provenance="recorded")) == "host:doi.org"
    assert _lineage(Passage("claim", "doi:10.1/x", source_kind="corpus")) == "host:doi.org"
    assert _lineage(Passage("claim", "notes.md", provenance="recorded", source_kind="url")) == "corpus:recorded"


def test_bundle_vetting_does_not_trust_callable_module(monkeypatch):
    from g6.bundles import awaiting_certificate
    from g6.bundles.compliance_gap import BUNDLE
    from g6.core import vetting

    def wrapped(*args):
        pass

    wrapped.__module__ = "attacker"
    seen = []
    monkeypatch.setattr(vetting, "awaiting_review", lambda package: seen.append(package) or False)
    assert not awaiting_certificate(replace(BUNDLE, run=wrapped))
    assert seen == ["g6.bundles.compliance_gap"]


def test_program_host_scope_is_declared():
    from g6.mcp.catalogue import HOST_VALUE, _needs

    assert _needs("outside", [{"name": "tool", "command": "C:/found/innocent.exe"}])[0]["command"] == HOST_VALUE
    assert _needs("outside", [{"name": "tool", "command": "host-secret", "catalogue_command": "git"}])[0]["command"] == "git"
    assert _needs("outside", [{"name": "tool", "command": "git", "catalogue_command": "/usr/bin/git"}])[0][
        "command"] == HOST_VALUE


def test_triage_uses_exception_identity():
    from g6.triage import classify

    assert classify("OSError", "permission denied").category == "io"   # a traceback's class name: the built-in
    assert classify(OSError, "arbitrary message").category == "io"
    assert classify(ValueError("no such file")).category == "value"


def test_compliance_refuses_malformed_structured_answers():
    from g6.bundles.compliance_gap import run

    refused = run({"regulations": "GDPR", "answers": '{"GDPR-5": "yes"}'})
    assert refused.completion_state == "blocked-escalated"
    assert refused.code == "G6_E_INPUT_VALIDATION"


def test_automated_kind_is_declared():
    from g6.core.adaptation_gate import is_automated

    # a declared kind only tightens: it never lifts G6's own auto: marker, and no declaration reads the name alone
    assert is_automated("auto:Pat", actor_kind="person") is True
    assert is_automated("Pat", actor_kind="automated") is True
    assert is_automated("Pat") is False


def test_configured_test_names_are_tests(monkeypatch):
    from g6.workforce import repo_tests, bench

    monkeypatch.setattr(repo_tests, "python_files", lambda *args: ("spec_*.py",))
    assert repo_tests.is_test_file("spec_widget.py", repo=Path("."))
    with pytest.raises(ValueError, match="test file"):
        bench._files({"spec_widget.py": "assert False"}, repo=Path("."))


def test_testpaths_are_read_as_configured_fields(monkeypatch):
    from g6.workforce import repo_tests

    config = SimpleNamespace(name="pyproject.toml", read_text=lambda **kwargs:
        '[tool.pytest.ini_options]\ntestpaths = ["specifications"]\npython_files = ["spec_*.py"]\n')
    monkeypatch.setattr(repo_tests, "_config_file", lambda folder: config)
    assert repo_tests.testpaths(Path(".")) == ("specifications",)


def test_declared_testpaths_protect_helpers(monkeypatch):
    from g6.workforce import repo_tests, bench

    monkeypatch.setattr(repo_tests, "testpaths", lambda repo: ("specifications",))
    assert repo_tests.is_test_related("specifications/helper.py", repo=Path("."))
    with pytest.raises(ValueError, match="test file"):
        bench._files({"specifications/helper.py": "assert False"}, repo=Path("."))


def test_unreadable_collection_config_refuses(monkeypatch):
    from g6.workforce import repo_tests

    def unreadable(**kwargs):
        raise OSError("synthetic unreadable configuration")
    monkeypatch.setattr(repo_tests, "_config_file", lambda folder:
                        SimpleNamespace(name="pyproject.toml", read_text=unreadable))
    with pytest.raises(repo_tests.RepoRefused):
        repo_tests.python_files(Path("."))


def test_structured_compliance_answers_are_read():
    from g6.bundles.compliance_gap import run, CLAIMED

    result = run({"regulations": "GDPR", "answers": {
        "GDPR-5": {"answer": "yes", "evidence": "recorded evidence"}}, "as_of": "2026-01-01"})
    assert any(row.get("result") == CLAIMED for row in result.rows)


def test_mission_keeps_human_text_as_data():
    from g6.mcp.mission import _here

    text = "mission_create is a word in caller data"
    assert _here({"notes": text})["notes"] == text
    assert _here({"next_action": {"tool": "mission_create"}})["next_action"]["tool"] == "g6_mission_create"


def test_mission_does_not_interpret_nontext_tool_data():
    from g6.mcp.mission import _here

    assert _here({"tool": {"caller": "data"}}) == {"tool": {"caller": "data"}}


def test_mission_tool_names_in_evidence_are_not_protocol_fields():
    from g6.mcp.mission import _here

    data = {"evidence": {"tool": "mission_create"},
            "notes": [{"next_action": {"tool": "mission_status"}}]}
    assert _here(data) == data


@pytest.mark.parametrize("error", [RecursionError, MemoryError])
def test_structured_compliance_decoder_limits_refuse(monkeypatch, error):
    from g6.bundles import compliance_gap

    def limited(*args, **kwargs):      # the decoder is called with the duplicate-key hook (review 4)
        raise error("synthetic decoder limit")

    monkeypatch.setattr(compliance_gap.json, "loads", limited)
    result = compliance_gap.run({"regulations": "GDPR", "answers": "{}"})
    assert result.code == "G6_E_INPUT_VALIDATION"
    assert result.completion_state == "blocked-escalated"
