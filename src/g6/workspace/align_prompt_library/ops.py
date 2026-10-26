# SPDX-License-Identifier: Apache-2.0
"""The ops of g6.workspace.align_prompt_library, each on the library under the G6 home.

Each op returns a :class:`~g6.workspace._envelope.WorkspaceResult`: ``qualified-draft`` with what the library holds,
or ``blocked-escalated`` with a stable ``G6_E_LIBRARY_*`` code. A stored version is an observation of G6's own record,
never a check that the prompt or specification is good; what the caller declared about it is returned under
``declared``, as their word.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from g6.workspace import _envelope as E
from g6.workspace.align_prompt_library.library import Library

COMPONENT = "align_prompt_library"
_CITE = "Cite the digest in your run's evidence: it names exactly this content."


def _run(operation: str, act: Callable[[Library], E.WorkspaceResult], ids: dict[str, str], path: Any = None
         ) -> E.WorkspaceResult:
    try:
        return act(Library(path))
    except E.WorkspaceRefused as exc:
        return E.refuse(operation, exc.code, exc.reason, evidence={"library": "the G6 home's library"}, **ids)
    except OSError as exc:
        return E.refuse(operation, E.OP_FAILED, f"the machine refused the {operation} ({type(exc).__name__}: "
                                                f"{exc.strerror or exc})"[:300], **ids)


def _seen(**observed: Any) -> dict[str, Any]:
    return {"kind": "observation", "library": "workspace/align_prompt_library under the G6 home", **observed}


def put(kind: str, name: str, content: str, *, metadata: Mapping[str, Any] | None = None, request_id: str = "",
        run_id: str = "", path: Any = None) -> E.WorkspaceResult:
    """Store a new version of ``name``; returns its ``version`` and ``digest`` (the SHA-256 of the content)."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        done = library.put(kind, name, content, metadata)
        said = (f"{name!r} version {done['version']} already holds this content and declarations; nothing was added"
                if done["unchanged"] else f"stored {kind} {name!r} as version {done['version']}")
        return E.answer("put", E.LIBRARY_STORED, said + ". Stored is not reviewed: what you declared is kept as "
                                                        "your word.",
                        _CITE, evidence=_seen(name=name, version=done["version"], digest=done["digest"]),
                        stored=True, **done, **ids)

    return _run("put", act, ids, path)


def get(name: str = "", *, version: int | None = None, digest: str = "", request_id: str = "", run_id: str = "",
        path: Any = None) -> E.WorkspaceResult:
    """One version, by name (the latest), by name and version or digest, or by digest alone."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        found = library.get(name, version=version, digest=digest)
        also = f"; the same content is also {len(found['also'])} other version(s)" if found["also"] else ""
        return E.answer("get", E.LIBRARY_READ, f"{found['kind']} {found['name']!r} version {found['version']}, "
                                               f"digest {found['digest'][:12]}...{also}.", _CITE,
                        evidence=_seen(name=found["name"], version=found["version"], digest=found["digest"]),
                        **found, **ids)

    return _run("get", act, ids, path)


def history(name: str, *, request_id: str = "", run_id: str = "", path: Any = None) -> E.WorkspaceResult:
    """Every version of ``name``, oldest first, tombstones included."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        listed = library.history(name)
        return E.answer("history", E.LIBRARY_LISTED, f"{name!r} has {len(listed)} version(s); the last is a "
                                                     f"{listed[-1]['action']}.",
                        "Read any version by its number or digest; rollback makes a new one from an earlier one.",
                        evidence=_seen(name=name, versions=len(listed)), name=name, versions=listed, **ids)

    return _run("history", act, ids, path)


def search(query: str, *, kind: str = "", include_retired: bool = False, limit: int = 50, request_id: str = "",
           run_id: str = "", path: Any = None) -> E.WorkspaceResult:
    """The latest version of each name whose name, content or declarations contain ``query``."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        found = library.search(query, kind=kind, include_retired=include_retired, limit=limit)
        return E.answer("search", E.LIBRARY_LISTED, f"{len(found)} record(s) match {query!r}.",
                        "Read one with get; a match on words is not a judgement that it fits your task.",
                        evidence=_seen(query=query, matches=len(found)), query=query, results=found, **ids)

    return _run("search", act, ids, path)


def render(name: str, *, version: int | None = None, digest: str = "", values: Mapping[str, str] | None = None,
           request_id: str = "", run_id: str = "", path: Any = None) -> E.WorkspaceResult:
    """A version's template filled from ``values``; strict (a missing or an extra value is refused)."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        done = library.render(name, version=version, digest=digest, values=values)
        return E.answer("render", E.LIBRARY_RENDERED, f"filled {len(done['placeholders'])} placeholder(s) of "
                                                      f"{name!r} version {done['version']}.",
                        "Cite template_digest and rendered_digest: they name the template and the text you sent.",
                        evidence=_seen(name=name, version=done["version"], template_digest=done["template_digest"],
                                       rendered_digest=done["rendered_digest"]), **done, **ids)

    return _run("render", act, ids, path)


def retire(name: str, *, request_id: str = "", run_id: str = "", path: Any = None) -> E.WorkspaceResult:
    """Write a tombstone for ``name``; every version stays readable by its number or digest."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        done = library.retire(name)
        said = (f"{name!r} was already retired at version {done['version']}" if done["unchanged"]
                else f"retired {name!r} with a tombstone at version {done['version']}")
        return E.answer("retire", E.LIBRARY_STORED, said + "; nothing was removed.",
                        "Read earlier versions by number or digest; put a new version to use the name again.",
                        evidence=_seen(name=name, version=done["version"]), **done, **ids)

    return _run("retire", act, ids, path)


def rollback(name: str, to_version: int, *, request_id: str = "", run_id: str = "", path: Any = None
             ) -> E.WorkspaceResult:
    """A new version of ``name`` holding ``to_version``'s content; nothing is rewritten."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(library: Library) -> E.WorkspaceResult:
        done = library.rollback(name, to_version)
        return E.answer("rollback", E.LIBRARY_STORED, f"{name!r} version {done['version']} holds version "
                                                      f"{to_version}'s content; the versions between are kept.",
                        _CITE, evidence=_seen(name=name, version=done["version"], digest=done["digest"]),
                        **done, **ids)

    return _run("rollback", act, ids, path)


__all__ = ["COMPONENT", "get", "history", "put", "render", "retire", "rollback", "search"]
