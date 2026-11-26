# SPDX-License-Identifier: Apache-2.0
"""Passage identity and advisory stances through the real grounding block."""
import importlib
import json
from dataclasses import dataclass
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.grounding import GroundingBlock, GroundingInput

backend = importlib.import_module("g6.sources.ctx_langextract.langextract_backend")
CLAIM = "Customers have a month to return the product"
POLICY = "You can return items within thirty days of purchasing them."


def fields(*, supports=None, contradicts=None, unrelated=None):
    return json.dumps({"value": {"supports": supports, "contradicts": contradicts, "unrelated": unrelated}})


@pytest.fixture(autouse=True)
def absent_library(monkeypatch):
    # Pin this suite to the built-in engine regardless of the lead's environment.
    monkeypatch.setattr(backend, "load_library", lambda: None)


def run(raw, *, sources=None, claim=CLAIM, patterns=None, library=None):
    fake = FakeLLM(default=raw)
    with semantic.request(llm=fake):
        result = GroundingBlock(langextract_module=library).infer(GroundingInput(
            claim=claim, sources=sources or {"policy": POLICY}, judge="model", patterns=patterns or {})).unwrap()
    return result, fake


def test_paraphrase_has_grounded_passage_and_unchanged_gate():
    out, _ = run(fields(supports=POLICY))
    assert out["check"]["status"] == "not_found"
    assert out["completion_state"] == "blocked-escalated"
    reading = out["check"]["model_reading"]
    assert reading["engine"] == "ctx_langextract"
    passage = reading["passages"][0]
    assert passage["stance"] == "supports" and passage["grounded"] is True
    assert (passage["start"], passage["end"]) == (0, len(POLICY))
    assert passage["lexical_on_quote"]["status"] == "not_found"


def test_explicit_observed_number_keeps_lexical_support():
    source = "The measured limit is 42 units."
    out, _ = run(fields(supports=source), claim=source, sources={"measurement": source})
    assert out["check"]["status"] == "quoted" and out["completion_state"] == "qualified-draft"
    reading = out["check"]["model_reading"]
    assert reading["reading"] == "supports" and reading["passages"][0]["usable"] is True


@pytest.mark.parametrize("stance", ["supports", "contradicts"])
def test_hallucinated_passage_is_refused_and_never_a_reading(stance):
    out, _ = run(fields(**{stance: "Customers may return anything forever."}))
    reading = out["check"]["model_reading"]
    passage = reading["passages"][0]
    assert passage["stance"] == stance and passage["status"] == "refused"
    assert passage["grounded"] is False and passage["lexical_on_quote"] is None
    assert reading["reading"] == "unclear" and reading["quote_found"] is False


def test_near_quote_is_flagged_and_never_used_for_lexical_check():
    out, _ = run(fields(supports=POLICY.lower()))
    reading = out["check"]["model_reading"]
    passage = reading["passages"][0]
    assert passage["status"] == "flagged" and passage["grounded"] is False
    assert passage["text_at_span"] == POLICY and passage["lexical_on_quote"] is None
    assert reading["reading"] == "unclear"


def test_grounded_contradiction_and_unrelated_are_typed_separately():
    out, _ = run(fields(contradicts=POLICY, unrelated="Delivery is free."),
                 sources={"policy": POLICY + " Delivery is free."})
    reading = out["check"]["model_reading"]
    assert [p["stance"] for p in reading["passages"]] == ["contradicts", "unrelated"]
    assert all(p["grounded"] for p in reading["passages"])
    assert reading["reading"] == "contradicts" and out["check"]["status"] == "not_found"


def test_passage_is_not_borrowed_from_another_source():
    out, _ = run(fields(supports=POLICY), sources={"wrong": "Delivery is free.", "right": POLICY})
    passages = out["check"]["model_reading"]["passages"]
    assert [(p["source"], p["status"]) for p in passages] == [("wrong", "refused"), ("right", "found")]


def test_conflicting_grounded_stances_keep_both_and_abstain():
    out, _ = run(fields(supports=POLICY, contradicts=POLICY))
    reading = out["check"]["model_reading"]
    assert len(reading["passages"]) == 2 and reading["reading"] == "unclear"


def test_regex_values_are_separate_from_model_stances(monkeypatch):
    from g6.sources.ctx_langextract import extract as ex
    # Keep the bounded child boundary fake here; its real confinement tests are separate.
    def patterns(text, supplied):
        assert supplied == {"days": r"\bthirty days\b"}
        span = ex.ground("thirty days", text, ex._folded(text))
        return {"days": ex.FieldResult("days", [span], True)}, [{"field": "days", "matches": 1}]
    monkeypatch.setattr(ex, "run_patterns", patterns)
    out, _ = run(fields(), patterns={"days": r"\bthirty days\b"})
    reading = out["check"]["model_reading"]
    assert reading["patterns"][0]["fields"][0]["value"] == "thirty days"
    assert reading["reading"] == "unclear" and reading["passages"] == []


@pytest.mark.parametrize("patterns", [{"Bad field": "x"}, {"x": "x" * 2001}])
def test_patterns_have_the_same_input_bounds_as_ctx_langextract(patterns):
    from pydantic import ValidationError
    valid = GroundingInput(claim=CLAIM, sources={"policy": POLICY}, judge="model", patterns={"days": r"\d+"})
    assert valid.patterns == {"days": r"\d+"}
    with pytest.raises(ValidationError):
        GroundingInput(claim=CLAIM, sources={"policy": POLICY}, judge="model", patterns=patterns)


class Alignment(Enum):
    MATCH_EXACT = "exact"
    MATCH_FUZZY = "fuzzy"
    MATCH_LESSER = "lesser"


class BaseModel:
    schema = None
    requires_fence_output = False

    def set_fence_output(self, value):
        self.requires_fence_output = value


@dataclass
class ScoredOutput:
    score: float
    output: str


def library_with(*extractions, text=POLICY):
    data = SimpleNamespace(AlignmentStatus=Alignment,
                           ExampleData=lambda **kw: SimpleNamespace(**kw),
                           Extraction=lambda **kw: SimpleNamespace(**kw))
    module = ModuleType("langextract")
    module.core = SimpleNamespace(
        base_model=SimpleNamespace(BaseLanguageModel=BaseModel),
        types=SimpleNamespace(ScoredOutput=ScoredOutput), data=data)

    def extract(**kw):
        assert kw["max_workers"] == kw["batch_length"] == kw["extraction_passes"] == 1
        assert kw["fetch_urls"] is False and kw["resolver_params"]["suppress_parse_errors"] is False
        assert "model" in kw and "api_key" not in kw and "config" not in kw
        assert kw["examples"][0].text == CLAIM
        assert kw["examples"][0].extractions[0].extraction_text == CLAIM
        kw["model"].set_fence_output(kw["fence_output"])
        assert kw["model"].schema is None and kw["model"].requires_fence_output is False
        rows = list(kw["model"].infer(["Extract the passages from this source: " + kw["text_or_documents"]]))
        assert json.loads(rows[0][0].output) == {"extractions": []}
        return SimpleNamespace(text=text, extractions=list(extractions))
    module.extract = extract
    return module


def extraction(quote=POLICY, *, start=0, end=None, alignment=Alignment.MATCH_EXACT, stance="supports"):
    interval = SimpleNamespace(start_pos=start, end_pos=len(POLICY) if end is None else end)
    return SimpleNamespace(extraction_class=stance, extraction_text=quote, char_interval=interval,
                           alignment_status=alignment)


def test_library_provider_uses_injected_caller_and_rechecks_exact_interval():
    library = library_with(extraction())
    out, fake = run('{"value":{"extractions":[]}}', library=library)
    reading = out["check"]["model_reading"]
    assert len(fake.calls) == 1 and reading["engine"] == "langextract"
    assert reading["passages"][0]["grounded"] is True
    assert reading["reading"] == "supports" and out["check"]["status"] == "not_found"


@pytest.mark.parametrize("alignment", [Alignment.MATCH_FUZZY, Alignment.MATCH_LESSER, None, "exact"])
def test_library_nonexact_alignment_is_at_most_flagged(alignment):
    out, _ = run('{"value":{"extractions":[]}}', library=library_with(extraction(alignment=alignment)))
    reading = out["check"]["model_reading"]
    assert reading["passages"][0]["status"] == "flagged"
    assert reading["passages"][0]["lexical_on_quote"] is None
    assert reading["reading"] == "unclear"


def test_library_fuzzy_value_with_valid_interval_is_flagged():
    out, _ = run('{"value":{"extractions":[]}}', library=library_with(
        extraction(quote="You can return products within thirty days of purchasing them.",
                   alignment=Alignment.MATCH_FUZZY)))
    passage = out["check"]["model_reading"]["passages"][0]
    assert passage["status"] == "flagged" and passage["text_at_span"] == POLICY
    assert passage["lexical_on_quote"] is None


def test_library_empty_candidate_is_refused_even_with_a_fuzzy_interval():
    out, _ = run('{"value":{"extractions":[]}}', library=library_with(
        extraction(quote="", alignment=Alignment.MATCH_FUZZY)))
    reading = out["check"]["model_reading"]
    assert reading["passages"][0]["status"] == "refused" and reading["reading"] == "unclear"


@pytest.mark.parametrize("start,end", [(1, len(POLICY)), (-1, 4), (0, 900), (True, 4), (None, None)])
def test_library_cannot_relocate_bad_interval_to_an_exact_copy(start, end):
    item = extraction(start=start, end=end)
    if start is None:
        item.char_interval = None
    out, _ = run('{"value":{"extractions":[]}}', library=library_with(item))
    reading = out["check"]["model_reading"]
    assert reading["passages"][0]["status"] == "refused" and reading["reading"] == "unclear"


def test_library_record_identity_must_match_source_text():
    out, _ = run('{"value":{"extractions":[]}}', library=library_with(extraction(), text="another record"))
    reading = out["check"]["model_reading"]
    assert reading["reading"] == "unclear" and reading["passages"] == []


def test_library_failure_never_silently_retries_with_another_engine():
    library = library_with()
    def fail(**kw):
        raise ValueError("langextract is not installed")  # message text must not choose fallback
    library.extract = fail
    out, fake = run(fields(supports=POLICY), library=library)
    assert not fake.calls
    reading = out["check"]["model_reading"]
    assert reading["engine"] == "langextract" and reading["reading"] == "unclear"


def test_library_cloud_without_grant_never_sends(monkeypatch):
    from g6.core.consent_callers import Destination
    monkeypatch.setattr(semantic, "destination", lambda caller: Destination("cloud:unknown", False))
    out, fake = run('{"value":{"extractions":[]}}', library=library_with(extraction()))
    reading = out["check"]["model_reading"]
    assert not fake.calls and reading["reading"] == "unclear" and reading["passages"] == []


def test_library_multiple_prompts_share_the_semantic_budget():
    library = library_with()
    def extract(**kw):
        list(kw["model"].infer(["first", "second"]))
        return SimpleNamespace(text=POLICY, extractions=[extraction()])
    library.extract = extract
    fake = FakeLLM(default='{"value":{"extractions":[]}}')
    with semantic.request(llm=fake, budget=1):
        out = GroundingBlock(langextract_module=library).infer(GroundingInput(
            claim=CLAIM, sources={"policy": POLICY}, judge="model")).unwrap()
    assert len(fake.calls) == 1
    reading = out["check"]["model_reading"]
    assert reading["reading"] == "unclear" and reading["passages"] == []


def test_window_failure_discards_partial_source_extractions():
    text = POLICY + " " + "x " * 4000
    fake = FakeLLM(default=fields(supports=POLICY))
    with semantic.request(llm=fake, budget=1):
        out = GroundingBlock().infer(GroundingInput(
            claim=CLAIM, sources={"policy": text}, judge="model")).unwrap()
    reading = out["check"]["model_reading"]
    assert len(fake.calls) == 1 and reading["reading"] == "unclear"
    assert reading["passages"] == [] and reading["errors"]


def test_library_invalid_stance_is_not_reported_as_a_known_reading():
    out, _ = run('{"value":{"extractions":[]}}', library=library_with(extraction(stance="certain")))
    reading = out["check"]["model_reading"]
    assert reading["reading"] == "unclear" and reading["passages"] == [] and reading["errors"]


def test_imported_library_is_selected_without_explicit_injection(monkeypatch):
    monkeypatch.setattr(backend, "load_library", lambda: library_with(extraction()))
    out, _ = run('{"value":{"extractions":[]}}')
    assert out["check"]["model_reading"]["engine"] == "langextract"
    assert out["check"]["model_reading"]["reading"] == "supports"


def test_import_absence_uses_import_identity(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError("arbitrary diagnostic", name="langextract")
    monkeypatch.setattr(backend.importlib, "import_module", missing)
    # Use the actual loader, rather than the suite's deterministic override.
    assert backend._import_langextract() is None


def test_import_loads_only_the_explicit_provider_protocol(monkeypatch):
    library = library_with()
    names = []
    def load(name):
        names.append(name)
        return library if name == "langextract" else SimpleNamespace()
    monkeypatch.setattr(backend.importlib, "import_module", load)
    assert backend._import_langextract() is library
    assert names == ["langextract", "langextract.core.base_model", "langextract.core.types", "langextract.core.data"]


def test_transitive_import_failure_is_not_library_absence(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError("langextract absent", name="google.genai")
    monkeypatch.setattr(backend.importlib, "import_module", missing)
    with pytest.raises(ModuleNotFoundError):
        backend._import_langextract()


def test_broken_import_is_a_caveat_without_a_fallback_send(monkeypatch):
    def broken():
        raise RuntimeError("unresolvable optional package")
    monkeypatch.setattr(backend, "load_library", broken)
    out, fake = run(fields(supports=POLICY))
    reading = out["check"]["model_reading"]
    assert not fake.calls and reading["engine"] == "langextract"
    assert reading["reading"] == "unclear" and reading["errors"][0]["code"] == "library_import_failed"
