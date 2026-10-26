# SPDX-License-Identifier: Apache-2.0
"""CtxLibraryMapperBlock: the registry and MCP door (``g6_invoke``) to ctx_library_mapper.

``search_candidates`` asks the sources named (PyPI, GitHub, Hugging Face, or one registered from Python) and reaches
the network, so it is sensitive: callable from Python, and over MCP only when the person who runs the server lists it
in ``G6_MCP_SENSITIVE``. ``licence_check`` and ``sources`` reach nothing. Every answer is ``qualified-draft`` at best:
the candidates are what the registries say about themselves, and the licence verdicts are a table lookup.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
import g6.knowledge._reply as _reply
from g6.sources.ctx_library_mapper import licences
from g6.sources.ctx_library_mapper.sources import ANSWERED, SOURCES, Clients, ask

COMPONENT = "ctx_library_mapper"
FOUND, PARTIAL, NONE_FOUND = "G6_E_LIBRARY_CANDIDATES", "G6_E_LIBRARY_PARTIAL", "G6_E_LIBRARY_NONE_FOUND"
FAILED, UNKNOWN_SOURCE = "G6_E_LIBRARY_SEARCH_FAILED", "G6_E_LIBRARY_UNKNOWN_SOURCE"
CHECKED, LISTED = "G6_E_LIBRARY_LICENCES", "G6_E_LIBRARY_SOURCES"
SENSITIVE_OPS: tuple[str, ...] = ("search_candidates",)
_OPS: dict[str, str] = {
    "search_candidates": "search_candidates(query, sources, max_results): candidate libraries from PyPI (an exact "
                         "project name), GitHub and Hugging Face, each with the licence it declares and a typed "
                         "outcome per source (a failed source is never 'nothing found'); sensitive: it reaches the "
                         "network",
    "licence_check": "licence_check(candidates, project_licence): each candidate's licence looked up against your "
                     "project's in an SPDX table: compatible, conditions, incompatible or unknown; a table lookup, "
                     "never a legal opinion",
    "sources": "sources(): the registered sources, their hosts and pauses; nothing is fetched",
}
_ACTIONS = {
    FOUND: "These are candidates in the registries' own words: read one before relying on it, and check its licence "
           "with licence_check.",
    PARTIAL: "Some sources could not be asked (see per_source): do not conclude no library exists; ask again later.",
    NONE_FOUND: "The sources answered with nothing: that is not evidence that no library exists; try other words.",
    FAILED: "No source could be asked (see per_source): nothing was searched, so nothing can be concluded.",
    UNKNOWN_SOURCE: "Name registered sources (the sources op lists them).",
    CHECKED: "A table lookup, not legal advice: a person reads the licence for anything that matters.",
    LISTED: "Name the sources to search; none is searched by default.",
}


class CandidateIn(BaseModel):
    """A candidate to check: its name and the licence it declares. Other fields (a search result's own) are ignored:
    nothing a caller writes into a candidate, a verdict included, is read."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=300)
    declared_licence: str = Field("", max_length=500)
    licence: str = Field("", max_length=500)


class CtxLibraryMapperInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["search_candidates", "licence_check", "sources", "ops", "help"] = "help"
    query: str = Field("", max_length=300)
    sources: tuple[str, ...] = Field((), max_length=10)
    max_results: int = Field(10, ge=1, le=50)
    candidates: tuple[CandidateIn, ...] = Field((), max_length=200)
    project_licence: str = Field("", max_length=200)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: CtxLibraryMapperInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _refuse(op: str, code: str, why: str, d: CtxLibraryMapperInput, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, op, code, why, action=_ACTIONS.get(code, "Correct the request as the reason says."),
                         reason=why, **_ids(d), **fields)


def search_candidates(d: CtxLibraryMapperInput, clients: Clients | None = None) -> dict[str, Any]:
    if not d.query.strip():
        return _refuse("search_candidates", "G6_E_LIBRARY_INPUT", "give query: the method or library to look for", d)
    if not d.sources:
        return _refuse("search_candidates", "G6_E_LIBRARY_INPUT", "name the sources to search (pypi, github, "
                       "huggingface); none is searched by default", d, registered=SOURCES.names())
    unknown = [s for s in d.sources if SOURCES.get(s) is None]
    if unknown:
        return _refuse("search_candidates", UNKNOWN_SOURCE, f"no source named {', '.join(unknown)} (registered: "
                       f"{', '.join(SOURCES.names())})", d)
    answers = [ask(SOURCES.get(name), d.query.strip(), d.max_results, clients) for name in dict.fromkeys(d.sources)]
    per_source = [a.as_dict() for a in answers]
    candidates = [c.as_dict() | {"licence": licences.spdx(c.declared_licence)} for a in answers for c in a.candidates]
    failed = [a for a in answers if a.outcome not in ANSWERED]
    evidence = [{"kind": "registry_search", "source": a.source, "outcome": a.outcome, "read": a.read,
                 "boundary": "g6.core.http: every hop checked, a body cap, a timeout, a pause per host"} for a in answers]
    fields = {"query": d.query.strip(), "candidates": candidates, "per_source": per_source,
              "complete": not failed}
    if len(failed) == len(answers):
        said = "; ".join(f"{a.source}: {a.outcome} ({a.reason})" for a in failed)
        return _refuse("search_candidates", FAILED, f"no source could be asked, so nothing was searched: {said}", d,
                       evidence=evidence, **fields)
    names = ", ".join(f"{a.source} ({a.outcome})" for a in failed)
    if failed:
        code, said = PARTIAL, (f"{len(candidates)} candidate(s) from the sources that answered; {names} could not be "
                               "asked, so this is not the whole of what exists.")
    elif candidates:
        code, said = FOUND, f"{len(candidates)} candidate(s) from {', '.join(a.source for a in answers)}."
    else:
        code, said = NONE_FOUND, ("The sources answered with no candidate: that is not evidence that no library "
                                  "exists.")
    said += " Whether any implements the method was not checked; each licence is as its registry declares it."
    return _reply.answer(COMPONENT, "search_candidates", code, said, action=_ACTIONS[code], evidence=evidence,
                         texts=[c["description"] for c in candidates], **_ids(d), **fields)


def licence_check(d: CtxLibraryMapperInput) -> dict[str, Any]:
    if not d.project_licence.strip():
        return _refuse("licence_check", "G6_E_LIBRARY_INPUT", "give project_licence: your project's licence, as an "
                       "SPDX identifier such as MIT or GPL-3.0-only (LicenseRef-Proprietary for a closed one)", d)
    if not d.candidates:
        return _refuse("licence_check", "G6_E_LIBRARY_INPUT", "give candidates: each with a name and its licence", d)
    project = licences.spdx(d.project_licence)
    rows = []
    for c in d.candidates:
        verdict, why, dep = licences.check(c.declared_licence or c.licence, d.project_licence)
        rows.append({"name": c.name, "licence": dep, "verdict": verdict, "why": why})
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in licences.VERDICTS}
    said = (f"{len(rows)} candidate(s) against {project}: " + ", ".join(f"{n} {v}" for v, n in counts.items() if n)
            + f". A lookup in {licences.TABLE}, never a legal opinion.")
    return _reply.answer(COMPONENT, "licence_check", CHECKED, said, action=_ACTIONS[CHECKED], **_ids(d),
                         project_licence=project, results=rows, counts=counts, table=licences.TABLE,
                         evidence=[{"kind": "table_lookup", "table": licences.TABLE, "rows": len(rows),
                                    "counts_as_check": False}])


@block_contract(
    purpose="find candidate libraries in package and model registries, with the licence each declares, and look the "
            "licences up against the project's own, so a method that already exists is not reimplemented and a "
            "dependency's licence is not assumed",
    mitigates_failure_modes=("reinvented_method",),
    verification_method="each source's reply mapped to a typed outcome, so a failed source is never 'nothing found'; "
                        "licences looked up in a fixed SPDX table, unknown when not in it",
)
@component_maturity(
    "beta",
    reason="PyPI's JSON API, GitHub's and Hugging Face's search through g6.core.http with a typed outcome per source; "
           "parsers tested on fixtures, not live; the licence verdicts are a table lookup",
)
class CtxLibraryMapperBlock(AIBlock[CtxLibraryMapperInput, dict, dict]):
    """Find candidate libraries and check their licences. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, clients: Clients | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.clients = clients

    def infer(self, data: CtxLibraryMapperInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if data.op == "sources":
            listed = SOURCES.describe()
            return Result.ok(_reply.answer(COMPONENT, "sources", LISTED, f"{len(listed)} sources are registered; "
                                           "nothing was fetched.", action=_ACTIONS[LISTED], sources=listed,
                                           **_ids(data)))
        if data.op == "licence_check":
            return Result.ok(licence_check(data))
        return Result.ok(search_candidates(data, self.clients))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "CandidateIn", "CtxLibraryMapperBlock", "CtxLibraryMapperInput",
           "licence_check", "search_candidates"]
