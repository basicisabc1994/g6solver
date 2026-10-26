# SPDX-License-Identifier: Apache-2.0
"""KnowledgeChunkerBlock: the registry and MCP door (``g6_invoke``) to the chunker.

Two ops, both pure (nothing is fetched, stored or run):

* ``chunk``: a document (or a text, whose source is then the caller's) cut into passages with exact offsets and ids
  bound to its source, every one checked before it is returned;
* ``check``: whether each cited passage is exactly what the document holds at its offsets, and where its text really
  is when it is not.

A result is ``qualified-draft``: the passages are exact spans of the text, and nothing here says the text is true.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.knowledge import _reply
from g6.knowledge._types import MAX_TEXT, Document, Source
from g6.knowledge.knowledge_chunker import chunker

COMPONENT = "knowledge_chunker"
CHUNKED = "G6_E_KNOWLEDGE_CHUNKED"
SPANS_MATCH = "G6_E_KNOWLEDGE_SPANS_MATCH"
NO_CITATIONS = "G6_E_KNOWLEDGE_NO_CITATIONS"
#: The source a text given straight in the request is labelled with, when the caller names none.
TEXT_LABEL = "text given in the request"
#: None: both ops are pure.
SENSITIVE_OPS: tuple[str, ...] = ()
_OPS: dict[str, str] = {
    "chunk": "chunk(document or text, unit=words, size=200, overlap=size/10): passages of at most size units, "
             "overlap shared between neighbours, each with exact offsets and an id bound to its source, all checked before "
             "they are returned",
    "check": "check(document or text, cited): whether each cited passage (its offsets, text, id and source) is "
             "exactly what the document holds there, and where its text really is when it is not",
}
_ACTIONS = {
    CHUNKED: "Cite a passage by its id and offsets. Before relying on one, re-read its source and check the passage "
             "still matches it (knowledge_manifest verify_passage): this says where the words are, not that they "
             "are true.",
    SPANS_MATCH: "Each citation is exactly what this document holds; whether the document still says it, and whether "
                 "it is true, are other checks.",
    chunker.SPAN_MISMATCH: "Do not use a citation that does not match: cite the text where found_at says it is, or "
                           "cut the document again.",
    chunker.NO_TEXT: "Give a document or a text with words in it; an empty conversion is the converter's to explain.",
    chunker.CHUNK_INPUT: "Correct the request as the reason says and ask again.",
    chunker.TOO_MANY: "Choose a larger size, or a larger unit (words or sentences rather than chars).",
    chunker.TOKENIZER: "Pass a tokenizer that returns ordered, non-overlapping spans inside the text, or use words.",
    NO_CITATIONS: "Give the citations to check: each with its start, end and text or id.",
}


class CitedSpan(BaseModel):
    """A passage as an agent cites it: its offsets and its text or id (a passage as ``chunk`` returns it is one)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field("", max_length=64)
    start: int
    end: int
    text: str = Field("", max_length=MAX_TEXT)
    source: Source | None = None
    document_sha256: str = Field("", max_length=64)
    trust: Literal["untrusted"] = "untrusted"


class KnowledgeChunkerInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["chunk", "check", "ops", "help"] = "help"
    document: Document | None = None
    text: str = Field("", max_length=MAX_TEXT)
    uri: str = Field("", max_length=4096)
    unit: Literal["chars", "words", "sentences", "tokens"] = "words"
    size: int = Field(200, ge=1, le=1_000_000)
    overlap: int | None = Field(None, ge=0)  # None: a tenth of size
    max_passages: int = Field(chunker.MAX_PASSAGES, ge=1, le=chunker.MAX_PASSAGES)
    cited: list[CitedSpan] = Field(default_factory=list, max_length=10_000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _refuse(op: str, code: str, reason: str, data: KnowledgeChunkerInput, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, op, code, reason, action=_ACTIONS.get(code, "Read the reason and ask again."),
                         request_id=data.request_id, run_id=data.run_id, reason=reason, **fields)


def _document(data: KnowledgeChunkerInput) -> Document | None:
    if data.document is not None:
        return data.document
    if data.text:
        return Document(text=data.text, source=Source.of(data.uri or TEXT_LABEL, data.text,
                                                         provenance="caller_supplied"))
    return None


def _chunk(data: KnowledgeChunkerInput, tokenizer: chunker.Tokenizer | None) -> dict[str, Any]:
    if data.document is not None and data.text:
        return _refuse("chunk", chunker.CHUNK_INPUT, "give a document or a text, not both", data)
    document = _document(data)
    if document is None:
        return _refuse("chunk", chunker.NO_TEXT, "there is no document or text to cut into passages", data)
    try:
        passages = chunker.chunk(document, unit=data.unit, size=data.size, overlap=data.overlap, tokenizer=tokenizer,
                                 max_passages=data.max_passages)
    except chunker.ChunkError as exc:
        return _refuse("chunk", exc.code, exc.reason, data)
    overlap = data.size // 10 if data.overlap is None else data.overlap
    message = (f"Cut {len(passages)} passage{'s' if len(passages) != 1 else ''} of at most {data.size} {data.unit}, "
               f"{overlap} shared between neighbours. Each is the document's text at its offsets, and its id is "
               "bound to the source, the text's hash and the offsets.")
    evidence = [{"kind": "chunking", "unit": data.unit, "size": data.size, "overlap": overlap,
                 "passages": len(passages), "document_sha256": document.sha256, "source": document.source.uri,
                 "checked": "every passage re-sliced from the text, its id recomputed, its edges on unit edges"}]
    return _reply.answer(COMPONENT, "chunk", CHUNKED, message, action=_ACTIONS[CHUNKED], request_id=data.request_id,
                         run_id=data.run_id, evidence=evidence, texts=[document.text],
                         passages=[p.model_dump() for p in passages], count=len(passages),
                         document={"sha256": document.sha256, "source": document.source.model_dump()})


def _check(data: KnowledgeChunkerInput) -> dict[str, Any]:
    if data.document is not None and data.text:
        return _refuse("check", chunker.CHUNK_INPUT, "give a document or a text, not both", data)
    document = _document(data)
    if document is None:
        return _refuse("check", chunker.NO_TEXT, "there is no document or text to check the citations against", data)
    if not data.cited:
        return _refuse("check", NO_CITATIONS, "there are no citations to check", data)
    found = []
    for index, cited in enumerate(data.cited):
        problems = chunker.span_problems(document, start=cited.start, end=cited.end, text=cited.text, id=cited.id,
                                         source=cited.source, document_sha256=cited.document_sha256)
        found.append({"index": index, "id": cited.id, "start": cited.start, "end": cited.end, "problems": problems,
                      "found_at": chunker.found_at(document, cited.text) if problems else [cited.start, cited.end]})
    wrong = [f for f in found if f["problems"]]
    evidence = [{"kind": "citation_check", "cited": len(found), "mismatched": len(wrong),
                 "document_sha256": document.sha256, "source": document.source.uri}]
    if wrong:
        reason = (f"{len(wrong)} of {len(found)} citations are not what the document holds at their offsets: "
                  + "; ".join(f"#{f['index']}: {f['problems'][0]}" for f in wrong[:3]))
        return _refuse("check", chunker.SPAN_MISMATCH, reason, data, citations=found, evidence=evidence)
    message = f"All {len(found)} citations are exactly what the document holds at their offsets."
    return _reply.answer(COMPONENT, "check", SPANS_MATCH, message, action=_ACTIONS[SPANS_MATCH],
                         request_id=data.request_id, run_id=data.run_id, evidence=evidence, citations=found)


@block_contract(
    purpose="cut a document into passages with exact offsets and ids bound to its source, and check a citation "
            "against the document it names",
    mitigates_failure_modes=("provenance_missing",),
    verification_method="every passage re-sliced from the document, its id recomputed and its edges checked against "
                        "the unit spans before it is returned; a citation compared with the document at its offsets",
)
@component_maturity(
    "beta",
    reason="deterministic spans over the text as given; every passage checked against the document before it is "
           "returned; a result says where words are, never that they are true",
)
class KnowledgeChunkerBlock(AIBlock[KnowledgeChunkerInput, dict, dict]):
    """Cut a document into exact passages, or check citations against it. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *,
                 tokenizer: chunker.Tokenizer | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.tokenizer = tokenizer

    def infer(self, data: KnowledgeChunkerInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_chunk(data, self.tokenizer) if data.op == "chunk" else _check(data))


__all__ = ["CHUNKED", "COMPONENT", "NO_CITATIONS", "SENSITIVE_OPS", "SPANS_MATCH", "TEXT_LABEL", "CitedSpan",
           "KnowledgeChunkerBlock", "KnowledgeChunkerInput"]