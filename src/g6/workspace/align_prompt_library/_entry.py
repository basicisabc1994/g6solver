# SPDX-License-Identifier: Apache-2.0
"""One entry of the prompt catalogue (``catalogue``), in a module of its own: the entry modules (``cognitive_prompts``,
``metacognition``) and the catalogue all import it, so each of them imports on its own."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from g6.workspace.align_prompt_library import library
from g6.workspace.align_prompt_library.library import digest_of


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


__all__ = ["CatalogueEntry"]