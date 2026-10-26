# SPDX-License-Identifier: Apache-2.0
import pytest
from g6.context import ContextItem, estimate_tokens


def test_compression_keeps_source_offsets_and_fits_budget():
    # Removing compress.py's budget selection makes this fail (D1).
    from g6.context import compress

    text = "Copper carries current. Brass carries current. Quartz is insulating. " * 80
    original = ContextItem(key="manual", value=text, section="repository", source="manual")
    result = compress(original, 20)
    spans = result.metadata["source_offsets"]
    assert result.value == " ".join(original.value[a:b] for a, b in spans)
    assert estimate_tokens(result.value) <= 20 and result.source == original.source
    assert result.metadata["method"] == "term_frequency_extractive"


def test_compression_selects_by_arithmetic_not_by_building_strings(monkeypatch):
    # Restoring _select's estimate_tokens("x" * (chars + cost)) makes this fail (about 1e11 bytes on 1 MB of sentences).
    import importlib
    module = importlib.import_module("g6.context.compress")

    text = "Copper conducts. Quartz insulates. " * 600          # 1,200 short sentences, all within the budget
    measured = []
    real = module.estimate_tokens
    monkeypatch.setattr(module, "estimate_tokens", lambda value: measured.append(len(value)) or real(value))
    result = module.compress(ContextItem(key="manual", value=text, section="repository"), 100_000)
    assert result.value == text.strip()
    assert sum(measured) <= 4 * len(text)


def test_compression_never_promotes_its_own_score():
    # Removing compress.py's qualified-draft metadata assignment makes this fail (CTX_RECURSIVE-01).
    from g6.context import compress

    original = ContextItem(key="x", value="Current flows. Current stops.", section="memory",
                           metadata={"completion_state": "verified"})
    result = compress(original, 100)
    assert result.metadata["completion_state"] == "qualified-draft"
    assert result.trust == "untrusted" and not result.may_instruct


def test_compression_refuses_when_no_sentence_fits():
    # Removing compress.py's no-fitting-sentence refusal makes this fail.
    from g6.context import compress

    original = ContextItem(key="x", value="This entire sentence cannot fit.", section="memory")
    with pytest.raises(ValueError, match="G6_E_CONTEXT_COMPRESSION_BUDGET"):
        compress(original, 1)

def test_compression_selector_failure_returns_no_summary(monkeypatch):
    # Removing compress's call to _select makes this fail.
    import importlib
    module = importlib.import_module("g6.context.compress")
    monkeypatch.setattr(module, "_select", lambda *a: (_ for _ in ()).throw(RuntimeError("selector failed")))
    with pytest.raises(RuntimeError, match="selector failed"):
        module.compress(ContextItem(key="x", value="Copper conducts.", section="memory"), 100)
