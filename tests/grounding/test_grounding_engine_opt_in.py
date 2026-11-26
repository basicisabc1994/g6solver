# SPDX-License-Identifier: Apache-2.0
"""Installed extraction is automatic; a missing reading never hides lexical limits."""
import importlib
import json
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.grounding import GroundingBlock, GroundingInput
from g6.mcp import invoke_reply

reader = importlib.import_module("g6.grounding.model_reading")
backend = importlib.import_module("g6.sources.ctx_langextract.langextract_backend")
CLAIM = "Customers have a month to return the product"
SOURCE = "You can return items within thirty days of purchasing them."
CODE = "G6_E_GROUNDING_LEXICAL_ONLY"
OPT_IN = 'pip install ".[langextract]"'
FIELDS = json.dumps({"value": {"supports": SOURCE, "contradicts": None, "unrelated": None}})


@pytest.fixture(autouse=True)
def isolated_engine(monkeypatch):
    monkeypatch.delenv("G6_GROUNDING_ENGINE", raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(backend, "load_library", lambda: None)


def run(*, llm=None, library=None, **params):
    with semantic.request(llm=llm):
        return GroundingBlock(langextract_module=library).infer(GroundingInput(
            claim=CLAIM, sources={"policy": SOURCE}, **params)).unwrap()


def lexical_card(out):
    card = out["warning_card"]
    if card["code"] == CODE:
        return card
    # Blocking warnings retain their primary code and carry this second limitation.
    return next(c for c in card["warnings"] if c["code"] == CODE)


def assert_lexical(out):
    card = lexical_card(out)
    assert "strict text matching" in card["message"] and "misses paraphrases" in card["message"]
    assert "19 of 100" in card["message"] and "docs/validation/results/V1/README.md" in card["message"]
    assert OPT_IN in card["suggested_action"]
    assert "engine_note" not in out


def google_library(*, callback=True):
    class Alignment(Enum):
        MATCH_EXACT = 1
        MATCH_FUZZY = 2
        MATCH_LESSER = 3

    class ProviderBase:
        pass

    library = ModuleType("langextract")
    library.core = SimpleNamespace(
        base_model=SimpleNamespace(BaseLanguageModel=ProviderBase),
        types=SimpleNamespace(ScoredOutput=lambda **kw: SimpleNamespace(**kw)),
        data=SimpleNamespace(AlignmentStatus=Alignment, ExampleData=lambda **kw: SimpleNamespace(**kw),
                             Extraction=lambda **kw: SimpleNamespace(**kw)))

    def extract(**kw):
        if callback:
            list(kw["model"].infer(["Extract passages: " + kw["text_or_documents"]]))
        return SimpleNamespace(text=SOURCE, extractions=[SimpleNamespace(
            extraction_class="supports", extraction_text=SOURCE,
            char_interval=SimpleNamespace(start_pos=0, end_pos=len(SOURCE)),
            alignment_status=Alignment.MATCH_EXACT)])

    library.extract = extract
    return library


@pytest.mark.parametrize("op", ["check_claim", "check_claims", "coverage"])
def test_missing_library_and_caller_warn_on_every_data_operation(op):
    out = run(op=op, text=CLAIM + ".")
    assert_lexical(out)


@pytest.mark.parametrize("setting", ["lexical", " LEXICAL ", "unknown"])
def test_environment_off_switch_prevents_import_and_model_reading(monkeypatch, setting):
    monkeypatch.setenv("G6_GROUNDING_ENGINE", setting)

    def forbidden_import():
        raise AssertionError("the disabled engine must not import the optional library")

    monkeypatch.setattr(backend, "load_library", forbidden_import)
    fake = FakeLLM(default=FIELDS)
    out = run(llm=fake, judge="model", library=google_library())
    assert_lexical(out)
    assert not fake.calls
    assert "model_reading" not in out["check"]
    # The Python reader cannot bypass the setting either.
    with semantic.request(llm=fake):
        reading = reader.model_reading(CLAIM, {"policy": SOURCE}, langextract_module=google_library())
    assert reading["model_ran"] is False and reading["engine"] == "lexical"
    assert not fake.calls


def test_existing_explicit_lexical_selection_warns_without_a_send():
    fake = FakeLLM(default=FIELDS)
    out = run(llm=fake, judge="lexical", library=google_library())
    assert_lexical(out)
    assert not fake.calls


@pytest.mark.parametrize("raw", ['{"value":{}}', "not json"])
def test_failed_or_refused_model_reading_warns(raw):
    out = run(llm=FakeLLM(default=raw), judge="model")
    assert_lexical(out)
    assert out["check"]["model_reading"]["model_ran"] is False


def test_consent_refusal_warns_before_any_send(monkeypatch):
    from g6.core.consent_callers import Destination

    monkeypatch.setattr(semantic, "destination", lambda caller: Destination("cloud:unknown", False))
    fake = FakeLLM(default=FIELDS)
    assert_lexical(run(llm=fake))
    assert not fake.calls


def test_fallback_reading_has_only_the_named_recommendation():
    out = run(llm=FakeLLM(default=FIELDS))
    reading = out["check"]["model_reading"]
    assert reading["model_ran"] is True and reading["engine"] == "ctx_langextract"
    assert out["warning_card"]["code"] == "G6_E_GROUNDING_NOT_FOUND"
    assert not out["warning_card"].get("warnings")
    assert "ctx_langextract" in out["engine_note"] and "Google" in out["engine_note"]
    assert "recommended" in out["engine_note"] and OPT_IN in out["engine_note"]
    assert out["check"]["status"] == "not_found" and out["completion_state"] == "blocked-escalated"


def test_installed_google_engine_runs_by_default_without_recommendation(monkeypatch):
    monkeypatch.setattr(backend, "load_library", google_library)
    fake = FakeLLM(default='{"value":{"extractions":[]}}')
    out = run(llm=fake)
    reading = out["check"]["model_reading"]
    assert reading["model_ran"] is True and reading["engine"] == "langextract"
    assert reading["passages"][0]["grounded"] is True and len(fake.calls) == 1
    assert out["warning_card"]["code"] == "G6_E_GROUNDING_NOT_FOUND"
    assert not out["warning_card"].get("warnings") and "engine_note" not in out
    assert out["check"]["status"] == "not_found"


def test_google_failure_has_lexical_warning_and_no_fallback_send():
    library = google_library()

    def fail(**kwargs):
        raise ValueError("ctx_langextract model_ran true")

    library.extract = fail
    fake = FakeLLM(default=FIELDS)
    out = run(llm=fake, library=library)
    assert_lexical(out)
    assert not fake.calls and out["check"]["model_reading"]["model_ran"] is False


def test_library_output_without_a_model_callback_does_not_claim_a_reading():
    out = run(library=google_library(callback=False))
    assert_lexical(out)
    assert out["check"]["model_reading"]["model_ran"] is False
    assert out["check"]["model_reading"]["reading"] == "unclear"
    assert not out["check"]["model_reading"]["passages"]


@pytest.mark.parametrize("reading", [{}, {"model_ran": "true", "engine": "langextract"},
                                    {"model_ran": True, "engine": "unknown"}])
def test_missing_or_unrecognised_typed_reading_state_warns(monkeypatch, reading):
    block = importlib.import_module("g6.grounding.grounding_block")
    monkeypatch.setattr(block, "model_reading", lambda *args, **kwargs: reading)
    assert_lexical(run())


def test_diagnostic_text_does_not_decide_whether_a_model_ran(monkeypatch):
    original = semantic.read

    def renamed(question):
        reading = original(question)
        return semantic.Reading(reading.kind, reading.value, reading.floor_value, reading.model_value,
                                reading.by, reading.disagreement, "model failed; lexical only")

    monkeypatch.setattr(semantic, "read", renamed)
    out = run(llm=FakeLLM(default=FIELDS))
    assert out["check"]["model_reading"]["model_ran"] is True
    assert "ctx_langextract" in out["engine_note"]
    assert not out["warning_card"].get("warnings")


def test_partly_failed_batch_cannot_hide_lexical_only_claims():
    replies = iter([FIELDS, "not json"])
    fake = FakeLLM(responder=lambda *args, **kwargs: next(replies))
    out = run(llm=fake, op="check_claims", text=CLAIM + ". Delivery takes five days.")
    assert_lexical(out)
    assert [c["model_reading"]["model_ran"] for c in out["checks"]] == [True, False]


@pytest.mark.parametrize("claim", [SOURCE, CLAIM])
def test_lexical_warning_reaches_existing_mcp_envelope(monkeypatch, claim):
    monkeypatch.setenv("G6_GROUNDING_ENGINE", "lexical")
    out = invoke_reply("grounding", "check_claim", json.dumps({"claim": claim, "sources": {"policy": SOURCE}}))
    assert_lexical(out["result"])
    assert lexical_card(out)["code"] == CODE
    assert out["warning_card"] == out["result"]["warning_card"]


def test_explicit_observed_number_keeps_its_status_with_the_lexical_warning(monkeypatch):
    monkeypatch.setenv("G6_GROUNDING_ENGINE", "lexical")
    out = GroundingBlock().infer(GroundingInput(
        claim="The measured temperature was 42 degrees.",
        sources={"record": "The measured temperature was 42 degrees."})).unwrap()
    assert out["check"]["status"] == "quoted" and out["completion_state"] == "qualified-draft"
    assert_lexical(out)
