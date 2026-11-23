# SPDX-License-Identifier: Apache-2.0
"""Calibration reports observations with an injected caller, never a performance claim."""
import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core.llm import FakeLLM


@pytest.fixture(autouse=True)
def builtin_engine(monkeypatch):
    module = importlib.import_module("g6.grounding.model_reading")
    monkeypatch.setattr(module, "_load_langextract", lambda: None, raising=False)


def reply(stance, quote):
    fields = dict.fromkeys(("supports", "contradicts", "unrelated"))
    fields[stance] = quote
    return json.dumps({"value": fields})


def api():
    return importlib.import_module("validation.grounding.model_reading")


def test_calibration_counts_agreement_false_support_and_abstention_by_set():
    rows = [
        {"pair_id": "p", "stratum": "paraphrased", "claim": "Customers have a month to return the product",
         "source": "You can return items within thirty days of purchasing them.", "label": "supported"},
        {"pair_id": "q", "stratum": "quoted", "claim": "Returns take 30 days",
         "source": "Returns take 30 days.", "label": "supported"},
        {"pair_id": "n", "stratum": "negated", "claim": "Dogs cannot enter",
         "source": "Dogs can enter.", "label": "unsupported"},
        {"pair_id": "w", "stratum": "wrong_figure", "claim": "Returns take 60 days",
         "source": "Returns take 30 days.", "label": "unsupported"},
        {"pair_id": "x", "stratum": "negated", "claim": "Cats are welcome inside",
         "source": "Felines must remain outdoors.", "label": "unsupported"},
        {"pair_id": "skip", "stratum": "near_topic", "claim": "a b c", "source": "x y z", "label": "unsupported"},
    ]

    def respond(system, user):
        payload = json.loads(json.loads(user)["text"])
        source = payload["user"].split("<<<TEXT\n", 1)[1].rsplit("\nTEXT>>>", 1)[0]
        return reply("supports", source)

    fake = FakeLLM(respond)
    result = api().evaluate(rows, llm=fake)
    assert len(fake.calls) == 5
    assert result["sets"]["paraphrase"]["agreement"] == 1
    assert result["sets"]["near_verbatim"]["agreement"] == 1
    contradiction = result["sets"]["contradiction"]
    assert contradiction["n"] == 3 and contradiction["false_supports"] == 1 and contradiction["abstentions"] == 2
    assert contradiction["agreement"] == 0 and contradiction["false_support_rate"] == pytest.approx(1 / 3)
    assert result["rows"][0]["status"] == "not_found" and result["rows"][0]["reading"] == "supports"
    assert "accuracy" not in result and "verdict" not in result


def test_calibration_correct_contradiction_and_failed_model_are_distinct():
    row = {"pair_id": "n", "stratum": "negated", "claim": "Dogs cannot enter",
           "source": "Dogs can enter.", "label": "unsupported"}
    good = api().evaluate([row], llm=FakeLLM(default=reply("contradicts", "Dogs can enter.")))
    assert good["sets"]["contradiction"]["agreement"] == 1
    for caller in (None, FakeLLM(default="bad json")):
        out = api().evaluate([row], llm=caller)
        assert out["sets"]["contradiction"]["abstentions"] == 1
        assert out["sets"]["contradiction"]["false_supports"] == 0
        assert out["sets"]["paraphrase"]["agreement_rate"] is None


def test_calibration_writes_injected_run_report():
    # A plain output file in the authorised .build root needs no owner-only temp folder.
    output = Path(".build") / ("calibration-test-" + uuid4().hex + ".json")
    pairs = Path("validation/v1_grounding/SMOKE_FIXTURE_NOT_EVALUATION_DATA.csv")
    try:
        result = api().run(pairs, output, llm=FakeLLM(default="bad json"))
        assert json.loads(output.read_text(encoding="utf-8")) == result
        assert {name: value["n"] for name, value in result["sets"].items()} == {
            "paraphrase": 1, "near_verbatim": 1, "contradiction": 2}
    finally:
        output.unlink(missing_ok=True)


def test_calibration_never_resolves_an_ambient_caller(monkeypatch):
    from g6.core import semantic
    monkeypatch.setattr(semantic, "resolve_caller", lambda: pytest.fail("must use injected caller"))
    with pytest.raises(TypeError):
        api().evaluate([])
    with pytest.raises(SystemExit, match="2"):
        api().main(["--out", ".build/unused.json"])
