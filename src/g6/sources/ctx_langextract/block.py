# SPDX-License-Identifier: Apache-2.0
"""CtxLangExtractBlock: the registry and MCP door (``g6_invoke``) to ctx_langextract.

``extract`` pulls named fields out of a text with one of two backends: ``model`` (the default), a model reached only
through the ``g6.core.llm`` caller given to the block from Python (``CtxLangExtractBlock(caller=...)``), or over MCP
the one the person configured (with none, the model backend is refused, never stood in for); or ``regex``, the
caller's own regular
expressions, each run in a bounded child process. Every value given is looked up in the text: found exactly
(``grounded``), flagged (found only with case and white space ignored), or refused (nowhere in the text). A field the
model gives no value for is ``not_checked``: a fact it missed is not caught here. Results are
``qualified-draft``: where a value sits in the text is checked, whether it is the right value for the field is not.
Nothing reaches the network, writes a file or acts outside G6, so no op is sensitive.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

import g6.knowledge._reply as _reply
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.llm import CONFIGURE_MODEL
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.knowledge._types import Source
from g6.sources.ctx_langextract import extract as ex

COMPONENT = "ctx_langextract"
EXTRACTED, INPUT, NO_MODEL = "G6_E_LANGEXTRACT_EXTRACTED", "G6_E_LANGEXTRACT_INPUT", "G6_E_LANGEXTRACT_NO_MODEL"
BAD_EXAMPLE = "G6_E_LANGEXTRACT_EXAMPLE_UNGROUNDED"
SENSITIVE_OPS: tuple[str, ...] = ()
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OPS: dict[str, str] = {
    "extract": "extract(text, fields, examples, backend, patterns, window): each field's value with its start and end "
               "in the text and whether the text there is exactly that value (grounded); a value nowhere in the text "
               "is refused, never passed as found; backend model (the caller given from Python) or regex (your own "
               "patterns, each in a bounded child process)",
}
_ACTIONS: dict[str, str] = {
    EXTRACTED: "Use found values with their offsets; treat flagged ones as unconfirmed and refused ones as not in the "
               "text. A field marked not checked had no value from the model, and the text may still hold it: read "
               "the text, or try backend regex. Whether a value is the right one for its field is not checked.",
    INPUT: "Correct the request as the reason says.",
    NO_MODEL: f"To give G6 a model, {CONFIGURE_MODEL}. Or give the block a model caller from Python "
              "(CtxLangExtractBlock(caller=...)), or use backend regex with your own patterns.",
    BAD_EXAMPLE: "Give examples whose values are copied exactly from their own text.",
    "G6_E_LANGEXTRACT_TOO_LONG": "Split the text, or give a larger window.",
    "G6_E_LANGEXTRACT_MODEL_FAILED": "The model call failed, so part of the text was not read: nothing is reported "
                                     "as absent from it. Try again, or with another caller.",
    "G6_E_LANGEXTRACT_PARSE_FAILED": "The model did not answer in the form asked: nothing from that window was used.",
    "G6_E_LANGEXTRACT_PATTERN_TIMEOUT": "Simplify the pattern (nested repetition can take forever to fail).",
    "G6_E_LANGEXTRACT_PATTERN_INVALID": "Correct the regular expression.",
    "G6_E_LANGEXTRACT_PATTERN_FAILED": "The pattern's child process could not be run to the end: read the reason.",
}


class Example(BaseModel):
    """A worked example: a text and the values copied exactly from it."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)
    values: dict[str, str | None] = Field(default_factory=dict)


class CtxLangExtractInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["extract", "ops", "help"] = "help"
    text: str = Field("", max_length=ex.MAX_TEXT)
    fields: dict[str, str] = Field(default_factory=dict, max_length=30)
    examples: tuple[Example, ...] = Field((), max_length=5)
    backend: Literal["model", "regex"] = "model"
    patterns: dict[str, str] = Field(default_factory=dict, max_length=30)
    window: int = Field(6000, ge=1000, le=20_000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("fields", "patterns")
    @classmethod
    def _names(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [k for k in value if not _FIELD.fullmatch(k)]
        if bad:
            raise ValueError(f"a field's name is a lower-case word (letters, digits, _): not {bad[0]!r}")
        if any(len(v) > 2000 for v in value.values()):
            raise ValueError("a field's description or pattern is at most 2000 characters")
        return value


def _ids(d: CtxLangExtractInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _refuse(code: str, why: str, d: CtxLangExtractInput, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, "extract", code, why, action=_ACTIONS.get(code, _ACTIONS[INPUT]), reason=why,
                         **_ids(d), **fields)


def _who(caller: Any) -> str:
    model = getattr(caller, "model", None)
    return f"{type(caller).__name__}({model})" if model else type(caller).__name__


def extract(d: CtxLangExtractInput, caller: Any = None) -> dict[str, Any]:
    if not d.text.strip():
        return _refuse(INPUT, "give text: the text to pull fields out of", d)
    names = dict(d.patterns) if d.backend == "regex" else dict(d.fields)
    if not names:
        what = "patterns (a regular expression per field)" if d.backend == "regex" else "fields (name: what it is)"
        return _refuse(INPUT, f"give {what}", d)
    for example in d.examples:
        bad = [k for k, v in example.values.items() if v is not None and v not in example.text]
        if bad:
            return _refuse(BAD_EXAMPLE, f"an example's value for {bad[0]} is not in its own text: an example that "
                                        "shows a value its text does not hold teaches the model to invent one", d)
    source = Source.of("the text given to ctx_langextract", d.text)
    try:
        if d.backend == "regex":
            found, record = ex.run_patterns(d.text, names)
            backend = {"kind": "backend", "backend": "regex", "by": "the caller's own patterns", "runs": record}
        else:
            if caller is None:
                return _refuse(NO_MODEL, "no model was given to the block (over MCP: no model is configured), so no "
                                         "model was asked and nothing is reported as extracted", d)
            found, record = ex.ask_model(caller, d.text, names, [e.model_dump() for e in d.examples], d.window)
            backend = {"kind": "backend", "backend": "model", "by": _who(caller), "windows": record,
                       "window": d.window, "overlap": ex.OVERLAP, "read_all_of_the_text": True}
    except ex.ExtractStopped as exc:
        return _refuse(exc.code, exc.reason, d)
    unanswered = ex.NOT_FOUND if d.backend == "regex" else ex.NOT_CHECKED  # a pattern ran over the whole text
    results = [found[name].as_dict(d.text, unanswered=unanswered) for name in names]
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("found", "flagged", "refused", ex.NOT_FOUND, ex.NOT_CHECKED)}
    said = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items() if n)
    message = (f"{len(results)} field(s): {said}. Each value given was looked up in the text; refused values are not "
               "in it." + (f" Not checked: {ex.NOT_CHECKED_WHY}." if counts[ex.NOT_CHECKED] else ""))
    check = {"kind": "span_check", "by": "ctx_langextract", "how": "exact substring on word boundaries, then exact "
             "inside a longer word or with case and white space ignored (flagged), else refused",
             "source": source.model_dump(), "counts": counts}
    return _reply.answer(COMPONENT, "extract", EXTRACTED, message, action=_ACTIONS[EXTRACTED], texts=[d.text],
                         evidence=[backend, check], **_ids(d), fields=results, counts=counts, backend=d.backend)


@block_contract(
    purpose="pull named fields out of a text with each value held to the text: exact offsets when the text holds it, "
            "flagged when only a looser match does, refused when it is nowhere, and the whole text read",
    mitigates_failure_modes=("missed_key_fact",),
    verification_method="every value looked up in the source text itself (exact, then case and white space ignored); "
                        "the model reads every window of the text, never a cut-off prefix",
)
@component_maturity(
    "beta",
    reason="span checks in fixed code over an injected model caller or the caller's patterns in bounded children; "
           "tested with deterministic model doubles, not with a real model",
)
class CtxLangExtractBlock(AIBlock[CtxLangExtractInput, dict, dict]):
    """Pull fields out of a text and say where each sits in it. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, caller: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.caller = caller

    def infer(self, data: CtxLangExtractInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(extract(data, self.caller))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "CtxLangExtractBlock", "CtxLangExtractInput", "Example", "extract"]
