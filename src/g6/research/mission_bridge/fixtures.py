# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The synthetic fixtures (audit ENG-02): two multi-domain missions and one coding workflow, their recordings and the
catalogue the replay assumes. Plain JSON read from ``fixtures/``; each call returns a fresh dict.

The medico-legal and engineering graphs follow the audit's illustrations but name real operations from the source map
(``docs/architecture/2026-10-11-mission-bridge-source-map.md``) and carry a verify unit for every work step, since the
product refuses a plan that has none. The engineering graph is the audit's: it has no design load, on purpose. A
recording holds synthetic placeholders (nothing was run). The replay catalogue says, per operation, whether its class is the
shipped census's or an assumption the experiment makes so the graph can be screened as if it were measured.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .checks import OpFact

FOLDER = Path(__file__).with_name("fixtures")
NAMES = ("medico_legal", "engineering", "coding")


def _read(stem: str) -> dict[str, Any]:
    return json.loads((FOLDER / f"{stem}.json").read_text(encoding="utf-8"))


def _named(name: str) -> str:
    if name not in NAMES:
        raise KeyError(f"no fixture {name!r}; the fixtures are {NAMES}")
    return name


def load(name: str) -> dict[str, Any]:
    """The mission document as written, ready for ``MissionIR.from_dict``."""
    return _read(_named(name))


def recording(name: str) -> dict[str, Any]:
    """Synthetic inputs, gate decisions and node outputs for ``replay``."""
    return _read(f"{_named(name)}.recording")


def standard_body(name: str) -> dict[str, Any]:
    """The mission's frozen standard: its id, provenance, predicates and the exact text whose hash the mission carries."""
    return _read("standards")[_named(name)]


def replay_record() -> dict[str, Any]:
    """The catalogue record with its provenance (the census reading it was taken from, and per-op source)."""
    return _read("replay_catalogue")


def replay_catalogue() -> dict[str, OpFact]:
    return {op: OpFact(entry["effect"], entry["authority"]) for op, entry in replay_record()["ops"].items()}
