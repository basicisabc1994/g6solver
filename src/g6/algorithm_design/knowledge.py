# SPDX-License-Identifier: Apache-2.0
"""Bundled textbook-derived metadata. Retrieval is not verification or execution."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from g6.algorithm_design.ontology_adapter import retrieve_ontology, CatalogMatch


def _load(name: str) -> dict[str, Any]:
    if name not in {"ontology.json", "skiena_problem_index.json"}:
        raise ValueError("unsupported bundled data name")
    resource = files("g6.algorithm_design").joinpath("data", name)
    raw = resource.read_bytes()
    if len(raw) > 4_000_000:
        raise ValueError("knowledge bundle exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != "0.1.0":
        raise ValueError("unsupported knowledge bundle schema")
    return value


def bundled_ontology() -> dict[str, Any]:
    """Return a fresh, independently mutable ontology mapping."""
    data = _load("ontology.json")
    if not isinstance(data.get("algorithms"), list) or not isinstance(data.get("problems"), list):
        raise ValueError("incomplete algorithm ontology")
    return data


def bundled_problem_index() -> dict[str, Any]:
    """Bibliographic catalog only: names are not executable algorithms."""
    data = _load("skiena_problem_index.json")
    if not isinstance(data.get("problems"), list) or data.get("count") != len(data["problems"]):
        raise ValueError("inconsistent problem index")
    return data


def search_bundled_ontology(*, problem_id: str, established_features: tuple[str, ...] = (),
                            limit: int = 20) -> tuple[CatalogMatch, ...]:
    """Apply the existing conservative feature gate to the built-in ontology."""
    return retrieve_ontology(bundled_ontology(), problem_id=problem_id,
                             established_features=established_features, limit=limit)
