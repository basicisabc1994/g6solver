# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Loads validation/formalisation as a package for its tests, and builds a small test-only manifest."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "validation" / "formalisation"
FRAGMENT = "smt.relation.int_bool.v1"


def load():
    name = "g6_validation_formalisation"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py",
                                                      submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def case(cohort="development", case_id="t_inc", **over):
    m = load().manifest
    base = {"case_id": case_id, "cohort": cohort, "fragment": FRAGMENT,
            "requirement": f"{case_id}(x) returns x plus one, for x from 0 to 5.", "reference": "(= result (+ x 1))",
            "inputs": (("x", "Int"),), "result_sort": "Int", "label_author": "person:test-author",
            "domain": ("(>= x 0)", "(<= x 5)"), "table": tuple(({"x": x}, x + 1) for x in range(6)),
            "code": f"def {case_id}(x):\n    return x + 1\n", "wrong_code": (f"def {case_id}(x):\n    return x + 2\n",)}
    return m.Case(**{**base, **over})


def manifest(row="proposal", cases=None, **over):
    from g6.harness.formalisation import FRAGMENTS

    m = load().manifest
    base = {"row": row, "fragments": (FRAGMENT,), "cases": tuple(cases or (case(),)),
            "adapter_versions": {FRAGMENT: FRAGMENTS[FRAGMENT]["adapter"]},
            "renderer_versions": {FRAGMENT: FRAGMENTS[FRAGMENT]["renderer"]}, "environment": {"z3": "test"},
            "thresholds": {"min_right_acceptance": 0.9, "max_wrong_acceptance": 0.05},
            "stop_rule": "test rule", "candidate_authors": ("g6:mutation",)}
    return m.Manifest(**{**base, **over})