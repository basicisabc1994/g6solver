# SPDX-License-Identifier: Apache-2.0
"""KnowledgeManifestBlock: the registry and MCP door (``g6_invoke``) to the manifest.

Three ops. ``record`` keeps a document and its passages in G6's own state folder (nothing else on the machine is
touched, nothing is fetched); ``get`` reads a recorded passage back, checked against what was recorded with it;
``verify_passage`` compares a passage (recorded, or given) with its source as the caller read it again. ``matches``
is ``qualified-draft``; ``changed``, ``source_unavailable`` and ``unverifiable`` are ``blocked-escalated``.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.knowledge import _reply
from g6.knowledge._types import Document, Passage
from g6.knowledge.knowledge_manifest.manifest import STORE, Store, StoreError, Verdict, verify_passage

COMPONENT = "knowledge_manifest"
RECORDED = "G6_E_KNOWLEDGE_RECORDED"
FOUND = "G6_E_KNOWLEDGE_FOUND"
NOT_RECORDED = "G6_E_KNOWLEDGE_NOT_RECORDED"
NO_DOCUMENT = "G6_E_KNOWLEDGE_NO_DOCUMENT"
CODES = {"matches": "G6_E_KNOWLEDGE_MATCHES", "changed": "G6_E_KNOWLEDGE_CHANGED",
         "source_unavailable": "G6_E_KNOWLEDGE_SOURCE_UNAVAILABLE", "unverifiable": "G6_E_KNOWLEDGE_UNVERIFIABLE"}
#: None: record writes only the manifest in G6's own state folder; nothing reaches the network or runs.
SENSITIVE_OPS: tuple[str, ...] = ()
_OPS: dict[str, str] = {
    "record": "record(document, passages): keep a document and its passages in the manifest under G6's state folder; "
              "only passages that are exact spans of the document are kept",
    "get": "get(passage_id): a recorded passage and its document's details, checked against what was recorded with "
           "them",
    "verify_passage": "verify_passage(passage_id or passage, document): compare a cited passage with its source as "
                      "read again: matches, changed (with where the words are now), source_unavailable or "
                      "unverifiable",
}
_ACTIONS = {
    RECORDED: "Cite each passage by its id; before an answer relies on one, re-read its source and verify_passage it.",
    FOUND: "This is what was recorded; re-read the source and verify_passage before relying on it.",
    NOT_RECORDED: "Record the document and its passages first, or pass the passage itself.",
    NO_DOCUMENT: "Give the document the passages were cut from (as the fetcher, converter or chunker returned it).",
    STORE: "The manifest could not be used, or a record in it was edited; read the reason. An edited record is not "
           "served: record the source again from a fresh read.",
    "G6_E_KNOWLEDGE_SPAN_MISMATCH": "Record only passages cut from this document (knowledge_chunker), unchanged.",
    CODES["matches"]: "The cited words are the source's as read now; whether they are true is another check "
                      "(g6.grounding).",
    CODES["changed"]: "Do not cite the old passage: re-read the source, cut it again and cite the version read now.",
    CODES["source_unavailable"]: "The source could not be read again, so the citation is unchecked: say so, or try "
                                 "again later.",
    CODES["unverifiable"]: "Nothing was compared: give the passage (or its recorded id) and the source as read again.",
}


class KnowledgeManifestInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["record", "get", "verify_passage", "ops", "help"] = "help"
    document: Document | None = None
    passages: list[Passage] = Field(default_factory=list, max_length=100_000)
    passage_id: str = Field("", max_length=64)
    passage: Passage | None = None
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _refuse(op: str, code: str, reason: str, data: KnowledgeManifestInput, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, op, code, reason, action=_ACTIONS.get(code, "Read the reason and ask again."),
                         request_id=data.request_id, run_id=data.run_id, reason=reason, **fields)


def _summary(document: Document) -> dict[str, Any]:
    return {"sha256": document.sha256, "source": document.source.model_dump(), "media_type": document.media_type,
            "converted_by": document.converted_by, "unread": list(document.unread), "title": document.title,
            "locator": document.locator, "characters": len(document.text)}


def _record(store: Store, data: KnowledgeManifestInput) -> dict[str, Any]:
    if data.document is None:
        return _refuse("record", NO_DOCUMENT, "there is no document to record", data)
    try:
        added, known = store.record(data.document, data.passages)
    except StoreError as exc:
        return _refuse("record", exc.code, exc.reason, data)
    message = (f"Recorded the document from {data.document.source.uri} and {added} new passage"
               f"{'s' if added != 1 else ''} ({known} already recorded). Each was checked to be an exact span of the "
               "document.")
    evidence = [{"kind": "manifest_record", "store": str(store.path), "document_sha256": data.document.sha256,
                 "passages_new": added, "passages_known": known}]
    return _reply.answer(COMPONENT, "record", RECORDED, message, action=_ACTIONS[RECORDED], request_id=data.request_id,
                         run_id=data.run_id, evidence=evidence, passage_ids=[p.id for p in data.passages],
                         document=_summary(data.document))


def _get(store: Store, data: KnowledgeManifestInput) -> dict[str, Any]:
    if not data.passage_id:
        return _refuse("get", NOT_RECORDED, "no passage id was given", data)
    try:
        found = store.passage(data.passage_id)
    except StoreError as exc:
        return _refuse("get", exc.code, exc.reason, data)
    if found is None:
        return _refuse("get", NOT_RECORDED, f"no passage {data.passage_id} is recorded in {store.path}", data)
    passage, document = found
    return _reply.answer(COMPONENT, "get", FOUND, f"Passage {passage.id} as recorded, from {passage.source.uri}.",
                         action=_ACTIONS[FOUND], request_id=data.request_id, run_id=data.run_id,
                         texts=[passage.text], passage=passage.model_dump(), document=_summary(document))


def _verify(store: Store, data: KnowledgeManifestInput) -> dict[str, Any]:
    passage = data.passage
    if passage is None and data.passage_id:
        try:
            found = store.passage(data.passage_id)
        except StoreError as exc:
            return _refuse("verify_passage", exc.code, exc.reason, data)
        if found is None:
            verdict = Verdict("unverifiable", f"no passage {data.passage_id} is recorded, so there is nothing to "
                                              "compare")
            return _verdict(verdict, data, None)
        passage = found[0]
    if passage is None:
        return _verdict(Verdict("unverifiable", "no passage was named (neither a passage nor its recorded id)"),
                        data, None)
    return _verdict(verify_passage(passage, data.document), data, passage)


def _verdict(verdict: Verdict, data: KnowledgeManifestInput, passage: Passage | None) -> dict[str, Any]:
    code = CODES[verdict.outcome]
    fields = {"outcome": verdict.outcome, "span_intact": verdict.span_intact, "found_at": verdict.found_at,
              "passage_id": passage.id if passage else data.passage_id,
              "source": passage.source.uri if passage else ""}
    evidence = [{"kind": "passage_check", "outcome": verdict.outcome, "checks": list(verdict.checks),
                 "cited_document_sha256": passage.document_sha256 if passage else "",
                 "reread_sha256": verdict.reread_sha256, "reread": verdict.reread}]
    if verdict.outcome != "matches":
        return _refuse("verify_passage", code, verdict.reason, data, evidence=evidence, **fields)
    return _reply.answer(COMPONENT, "verify_passage", code, verdict.reason[0].upper() + verdict.reason[1:] + ".",
                         action=_ACTIONS[code], request_id=data.request_id, run_id=data.run_id, evidence=evidence,
                         reason=verdict.reason, **fields)


@block_contract(
    purpose="keep sources, documents and passages, and check a cited passage against its source as read again",
    mitigates_failure_modes=("stale_citation",),
    verification_method="the re-read text's SHA-256 recomputed and compared with the hash the passage was cut from, "
                        "then the text at the passage's offsets; anything not compared fails closed",
)
@component_maturity(
    "beta",
    reason="content-addressed SQLite records checked on read; verify_passage compares the source as re-read and "
           "only an exact match passes, as a qualified draft",
)
class KnowledgeManifestBlock(AIBlock[KnowledgeManifestInput, dict, dict]):
    """Record passages and check them against their sources as read again. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, store: Store | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.store = store

    def infer(self, data: KnowledgeManifestInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        store = self.store or Store()
        run = {"record": _record, "get": _get, "verify_passage": _verify}[data.op]
        return Result.ok(run(store, data))


__all__ = ["CODES", "COMPONENT", "FOUND", "NOT_RECORDED", "NO_DOCUMENT", "RECORDED", "SENSITIVE_OPS",
           "KnowledgeManifestBlock", "KnowledgeManifestInput"]