# SPDX-License-Identifier: Apache-2.0
"""AlignPromptLibraryBlock: the registry and MCP door (``g6_invoke``) to g6.workspace.align_prompt_library.

Each op calls one function of :mod:`g6.workspace.align_prompt_library.ops` and returns its labelled result with
``component`` and ``op``. A refusal is a ``blocked-escalated`` result, never a raised error. The typed input refuses
unknown ops and unknown fields (``G6_E_INPUT_VALIDATION``): there is no field for a review status, a reviewer, an
approval or a confidence; what a caller wants to say about a record goes in ``metadata``, kept as their word.

Put, retire, rollback and install_catalog are sensitive: a changed stored prompt can affect later tasks. Reads, and the
catalogue's listing, remain non-sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.workspace import _envelope as E
from g6.workspace.align_prompt_library import ops
from g6.workspace.align_prompt_library.library import MAX_CONTENT

SENSITIVE_OPS: tuple[str, ...] = ("put", "retire", "rollback", "install_catalog")
_OPS: dict[str, str] = {
    "put": "put(kind, name, content, metadata): store a new version of a prompt, spec or eval_set; returns its version "
           "and digest (the SHA-256 of the content); metadata is kept as your word",
    "get": "get(name, version or digest): one version (the latest by name alone); a version or digest the library does "
           "not hold is refused, never answered with the latest; a digest alone finds every name holding it",
    "history": "history(name): every version, oldest first, tombstones included",
    "search": "search(query, kind, include_retired, limit): the latest version of each name whose name, content or "
              "metadata contain the words",
    "render": "render(name, version or digest, values): the template's {{placeholders}} filled in one pass; a missing "
              "or an extra value is refused, and expanded text is capped at 1,000,000 characters; digests returned",
    "retire": "retire(name): a tombstone; every version stays readable by number or digest",
    "rollback": "rollback(name, to_version): a new version holding an earlier version's content; nothing is rewritten",
    "catalogue": "catalogue(mode, step): the prompt catalogue's entries, each tied to a work mode (premortem, mission, "
                 "venture) and a step (propose, test, approve; none decides a gate), with its template and digest",
    "install_catalog": "install_catalog(prefix): store every catalogue entry as prefix + its name, a candidate template; "
                       "a name holding a prompt of your own is refused, never overwritten",
}
_NEEDS = {"put": ("kind", "name", "content"), "history": ("name",), "render": ("name",), "retire": ("name",),
          "rollback": ("name", "to_version")}

Value = Annotated[str, Field(max_length=MAX_CONTENT)]


class AlignPromptLibraryInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["put", "get", "history", "search", "render", "retire", "rollback", "catalogue", "install_catalog", "ops",
                "help"] = "help"
    kind: Literal["prompt", "spec", "eval_set", ""] = ""
    name: str = Field("", max_length=100)
    content: str = Field("", max_length=MAX_CONTENT)
    metadata: dict[str, Any] | None = Field(None, max_length=50)
    version: int | None = Field(None, ge=1)
    digest: str = Field("", max_length=64)
    values: dict[Annotated[str, Field(max_length=100)], Value] | None = Field(None, max_length=200)
    query: str = Field("", max_length=1000)
    include_retired: bool = False
    to_version: int | None = Field(None, ge=1)
    limit: int = Field(50, ge=1, le=500)
    mode: Literal["premortem", "mission", "venture", ""] = ""
    step: Literal["propose", "test", "gate", "approve", ""] = ""
    prefix: str = Field("", max_length=40)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: AlignPromptLibraryInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[AlignPromptLibraryInput], Any]] = {
    "put": lambda d: ops.put(d.kind, d.name, d.content, metadata=d.metadata, **_ids(d)),
    "get": lambda d: ops.get(d.name, version=d.version, digest=d.digest, **_ids(d)),
    "history": lambda d: ops.history(d.name, **_ids(d)),
    "search": lambda d: ops.search(d.query, kind=d.kind, include_retired=d.include_retired, limit=d.limit, **_ids(d)),
    "render": lambda d: ops.render(d.name, version=d.version, digest=d.digest, values=d.values, **_ids(d)),
    "retire": lambda d: ops.retire(d.name, **_ids(d)),
    "rollback": lambda d: ops.rollback(d.name, d.to_version or 0, **_ids(d)),
    "catalogue": lambda d: ops.catalogue(mode=d.mode, step=d.step, **_ids(d)),
    "install_catalog": lambda d: ops.install_catalog(d.prefix, **_ids(d)),
}


def _given(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


@block_contract(
    purpose="keep every version of a prompt, specification or evaluation set, addressed by the SHA-256 of its "
            "content, so a result can cite exactly the version that produced it",
    mitigates_failure_modes=("untracked_prompt_version",),
    verification_method="the ledger's hash chain and the library's rules (versions with no gap, a name keeps its kind, "
                        "each digest the SHA-256 of its content) checked on every open; a read by digest returns only "
                        "content whose SHA-256 is that digest",
)
@component_maturity(
    "beta",
    reason="an append-only library on g6.core.ledger with content digests, tombstones and rollback as a new version, "
           "tested against edited, removed and re-chained entries; qualified-draft at best",
)
class AlignPromptLibraryBlock(AIBlock[AlignPromptLibraryInput, dict, dict]):
    """A versioned library of prompts, specifications and evaluation sets. Never ``verified``."""

    def __init__(self, name: str = ops.COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AlignPromptLibraryInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        missing = [f for f in _NEEDS.get(data.op, ()) if not _given(getattr(data, f))]
        if data.op == "get" and not (data.name.strip() or data.digest.strip()):
            missing = ["name or digest"]
        if data.op == "search" and not data.query.strip():
            missing = ["query"]
        if missing:
            result = E.refuse(data.op, E.LIBRARY_INPUT_INVALID, f"{data.op} needs {', '.join(missing)}", **_ids(data))
        else:
            result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "AlignPromptLibraryBlock", "AlignPromptLibraryInput"]
