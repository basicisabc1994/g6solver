# SPDX-License-Identifier: Apache-2.0
"""The one prompt catalogue: the prompts G6 ships, each tied to a work mode and a pipeline step.

Every entry names its mode (``premortem``, ``mission`` or ``venture``) and its step of Propose, Test, Gate and Approve;
none has the step ``gate``, because a model's output never decides a gate. ``render`` fills an entry through the
library's own strict one-pass fill (``library.fill``): a missing or an extra value is refused, and a value holding
``{{...}}`` stays literal. ``install`` stores the entries in the person's library as ``candidate_template`` versions,
never over a prompt of the person's own under the same name; over MCP it is the sensitive op ``install_catalog``.
The catalogue is static data: nothing here runs a model or writes at import time.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.workspace import _envelope as E
from g6.workspace.align_prompt_library import library
from g6.workspace.align_prompt_library.library import Library, digest_of

MODES: tuple[str, ...] = ("premortem", "mission", "venture")
STEPS: tuple[str, ...] = ("propose", "test", "gate", "approve")
#: The status every installed entry declares, which is what lets a later install update it.
CANDIDATE = "candidate_template"


@dataclass(frozen=True)
class CatalogueEntry:
    name: str
    stage: str
    mode: str
    step: str
    purpose: str
    source: str
    template: str

    @property
    def digest(self) -> str:
        return digest_of(self.template)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "stage": self.stage, "mode": self.mode, "step": self.step, "purpose": self.purpose,
                "source": self.source, "digest": self.digest, "placeholders": library.placeholders(self.template),
                "template": self.template}


from g6.workspace.align_prompt_library import cognitive_prompts  # noqa: E402 - the entries use CatalogueEntry

ENTRIES: tuple[CatalogueEntry, ...] = cognitive_prompts.ENTRIES
_BY_NAME = {e.name: e for e in ENTRIES}


def entries(*, mode: str = "", step: str = "") -> tuple[CatalogueEntry, ...]:
    """The entries for ``mode`` and ``step`` (each "" for any), in catalogue order."""
    return tuple(e for e in ENTRIES if (not mode or e.mode == mode) and (not step or e.step == step))


def entry(name: str) -> CatalogueEntry:
    """The entry named ``name``; ``WorkspaceRefused(LIBRARY_NOT_FOUND)`` for a name the catalogue has not."""
    found = _BY_NAME.get(name)
    if found is None:
        raise E.WorkspaceRefused(E.LIBRARY_NOT_FOUND, f"the prompt catalogue has no entry {str(name)[:100]!r}")
    return found


def render(name: str, values: Mapping[str, str]) -> dict[str, Any]:
    """Entry ``name`` filled with ``values`` through the library's strict one-pass fill, with the digests of its
    template and of the text made."""
    found = entry(name)
    text, _ = library.fill(found.template, values, label=f"catalogue entry {name!r}")
    return {"name": name, "template_digest": found.digest, "rendered_digest": digest_of(text), "text": text}


def _declared(found: CatalogueEntry) -> dict[str, Any]:
    return {"stage": found.stage, "mode": found.mode, "step": found.step, "purpose": found.purpose,
            "source": found.source, "status": CANDIDATE, "catalog_digest": found.digest}


def install(*, prefix: str = "", path: str | Path | None = None) -> dict[str, Any]:
    """Store every entry in the library (``path``, by default the G6 home's) as ``prefix`` + its name. A name that is
    free, or whose latest version was itself installed from the catalogue, is stored (or left ``unchanged``); a name
    the person uses for a prompt of their own is refused with ``LIBRARY_NAME_TAKEN`` and never overwritten."""
    if not isinstance(prefix, str) or len(prefix) > 40 or (prefix and not prefix.endswith("-")):
        raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, "prefix must be empty or end with '-' (at most 40 characters)")
    store = Library(path)
    stored: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for found in ENTRIES:
        name = prefix + found.name
        try:
            latest = store.get(name)
        except E.WorkspaceRefused as exc:
            if exc.code != E.LIBRARY_NOT_FOUND:
                refused.append({"name": name, "code": E.LIBRARY_NAME_TAKEN,
                                "reason": f"{name!r} is held in the library ({exc.code}); it is not overwritten"})
                continue
            latest = None
        if latest is not None and (latest.get("declared") or {}).get("status") != CANDIDATE:
            refused.append({"name": name, "code": E.LIBRARY_NAME_TAKEN,
                            "reason": f"{name!r} is a prompt of your own; the catalogue does not overwrite it"})
            continue
        done = store.put("prompt", name, found.template, _declared(found))
        stored.append({"name": name, "version": done["version"], "digest": done["digest"],
                       "unchanged": bool(done.get("unchanged"))})
    return {"stored": stored, "refused": refused}


__all__ = ["CANDIDATE", "ENTRIES", "MODES", "STEPS", "CatalogueEntry", "entries", "entry", "install", "render"]
