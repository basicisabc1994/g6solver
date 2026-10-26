# SPDX-License-Identifier: Apache-2.0
from pydantic import BaseModel, ConfigDict

from g6.core.llm import FakeLLM, NoModel


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    litres: int
    note: str | None = None


def test_structured_retry_checks_the_schema_and_preserves_missing_fields():
    # Removing complete_structured's model_validate_json call makes this fail (D1).
    from g6.core.llm import complete_structured

    replies = iter(['{"litres":"many"}', '{"litres":70}'])
    llm = FakeLLM(lambda system, user: next(replies))
    result = complete_structured(llm, "Extract", "capacity: 70 litres", Reading, retries=1)
    assert result["data"] == {"litres": 70}
    assert result["attempts"] == 2 and len(llm.calls) == 2
    assert "validation_error" in llm.calls[1][1]
    assert result["completion_state"] == "qualified-draft"


def test_first_schema_match_never_verifies_content():
    # Removing the qualified-draft assignment makes this fail (ADAPT_INSTRUCTOR-01).
    from g6.core.llm import complete_structured

    result = complete_structured(FakeLLM(default='{"litres":999}'), "Extract", "70 litres", Reading, retries=0)
    assert result["completion_state"] == "qualified-draft"
    assert result["evidence"]["content_checked"] is False


def test_only_the_injected_model_is_used():
    # Removing the caller-failure refusal makes this fail (ADAPT_INSTRUCTOR-02).
    from g6.core.llm import complete_structured

    result = complete_structured(NoModel(), "Extract", "70 litres", Reading, retries=2)
    assert result["completion_state"] == "blocked-escalated" and result["data"] is None
    assert result["warning_card"]["code"] == "G6_E_STRUCTURED_CALL_FAILED"


def test_exhaustion_never_invents_an_offline_value():
    # Removing the exhausted-retries refusal makes this fail (ADAPT_INSTRUCTOR-03).
    from g6.core.llm import complete_structured

    llm = FakeLLM(default="{}")
    result = complete_structured(llm, "Extract", "unknown capacity", Reading, retries=2)
    assert len(llm.calls) == 3
    assert result["data"] is None and result["completion_state"] == "blocked-escalated"
    assert result["warning_card"]["code"] == "G6_E_STRUCTURED_RETRIES_EXHAUSTED"


def test_validator_dependency_failure_blocks_without_calling_again():
    # Removing the validator-error refusal makes this fail.
    from g6.core.llm import complete_structured

    class Broken(Reading):
        @classmethod
        def model_validate_json(cls, *args, **kwargs):
            raise RuntimeError("validator unavailable")

    result = complete_structured(FakeLLM(default='{"litres":70}'), "Extract", "70 litres", Broken, retries=2)
    assert result["warning_card"]["code"] == "G6_E_STRUCTURED_VALIDATOR_FAILED"
    assert result["completion_state"] == "blocked-escalated"

def test_a_date_field_validates_from_its_json_text_on_the_first_call():
    # Replacing complete_structured's model_validate_json(json.dumps(value)) with model_validate(value) makes this fail.
    from datetime import date

    from g6.core.llm import complete_structured

    class Delivery(BaseModel):
        when: date

    llm = FakeLLM(default='{"when":"2026-01-02"}')
    result = complete_structured(llm, "Extract", "delivered on 2 January 2026", Delivery, retries=2)
    assert result["completion_state"] == "qualified-draft" and len(llm.calls) == 1
    assert result["data"] == {"when": "2026-01-02"}


def test_keys_the_schema_does_not_name_are_not_passed_through():
    # Removing complete_structured's model_dump of the validated value makes this fail.
    from g6.core.llm import complete_structured

    class Loose(BaseModel):
        litres: int
        note: str | None = None

    result = complete_structured(FakeLLM(default='{"litres":70,"approved":true}'), "Extract", "70 litres", Loose)
    assert result["data"] == {"litres": 70}


def test_schema_construction_failure_blocks_before_model_call():
    # Removing complete_structured's schema-construction exception branch makes this fail.
    from g6.core.llm import complete_structured
    class Broken(Reading):
        @classmethod
        def model_json_schema(cls, *a, **k):
            raise RuntimeError("broken schema")
    llm = FakeLLM(default='{"litres":70}')
    assert complete_structured(llm, "Extract", "70 litres", Broken)["completion_state"] == "blocked-escalated"
    assert llm.calls == []
