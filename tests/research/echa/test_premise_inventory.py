# SPDX-License-Identifier: Apache-2.0
"""AST inventory of the complete premise-decision path, including its readers.

Caller declarations have no custody authority. Only the custody accessor may
compare a repeated subject field with the binding. Constructor reads validate
form; population, units and exact-record hashes are declarations with no matching
field in CheckBinding, and are checked after custody accepts the subject.
"""
from pathlib import Path
import ast

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULES = {
    "research/echa/claims.py": None,
    "research/echa/ledger.py": {"_semantic", "Ledger.contaminated_fixtures"},
    "research/echa/custody.py": {"about", "binding_of", "evaluator_of", "fixture_contaminated"},
    "research/echa/records.py": None,
    "evidence/mapping.py": {"read_about", "bound_parts", "read_binding", "strict_json", "_no_repeat", "_no_constant",
                            "_finite", "check_id_of",
                            "CheckBinding.__post_init__", "CheckBinding.to_dict", "id_of"},
    "core/ledger.py": {"canonical", "digest"},
}
# These imports serve other branches of the modules, outside premise admission.
OUTSIDE_PREMISES = {
    "research/echa/episodes.py": "episode serialization and body integrity do not decide claim premises",
    "evidence/certificate.py": "the issuer's promotion path is not called by these research decisions",
}
RAW_SUBJECT = {"fixture_version", "source_ref", "standard_sha256", "predicate_id", "scope",
               "artifact_sha256", "fixture_sha256", "evaluator_sha256", "artefact_sha256", "check_sha256",
               "predicates", "check_id", "check_sha", "artefact", "standard", "fixture", "request", "task"}
PARTS = {"parts", "original", "of_check", "of_control"}
DECLARATIONS = {
    "evidence_id", "purpose", "result", "oracle_category", "independent", "independence_group",
    "negative_control_ref", "calibration_ref", "scope_mapping_ref", "applies_to_sha256", "sha256",
    "population", "sample_unit", "checked_statement", "oracle_error_bound", "evaluator_lower_bound",
    "known_limitations", "oracle_version", "independence_rationale", "schema_version",
}


def inventory(tree, selected=None):
    """List every attribute load and dynamic getattr in each selected function."""
    found = []

    def visit(node, where=()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, (*where, child.name))
                continue
            function = ".".join(where)
            if selected is None or function in selected:
                if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
                    found.append((function, child.lineno, ast.unparse(child.value), child.attr))
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "getattr":
                    found.append((function, child.lineno, ast.unparse(child.args[0]),
                                  "getattr:" + ast.unparse(child.args[1])))
            visit(child, where)

    visit(tree)
    return sorted(found)


def refusal(rel, function, receiver, field):
    if field not in RAW_SUBJECT and not field.startswith("getattr:"):
        return None
    if rel == "research/echa/records.py":
        return None  # construction/serialization, never authority for a bound subject
    if rel == "evidence/mapping.py":
        return None  # the shared reader of the immutable CheckBinding
    if rel == "research/echa/custody.py" and function in {"about", "binding_of"}:
        return None  # the sole ECHA accessor compares declarations against custody
    if ((receiver in PARTS or receiver.startswith("about(")) and field not in {
        "fixture_version", "source_ref", "standard_sha256", "predicate_id", "artifact_sha256", "fixture_sha256",
        "evaluator_sha256", "artefact_sha256", "check_sha256",
    }) or receiver == "claim":
        return None
    return f"{rel}:{function}:{receiver}.{field}: caller subject read outside custody"


def test_every_premise_decision_attribute_read_is_inventoried_through_custody():
    rows, refused = [], []
    for rel, functions in MODULES.items():
        tree = ast.parse((ROOT / "src/g6" / rel).read_text(encoding="utf-8"))
        for function, line, receiver, field in inventory(tree, functions):
            rows.append((rel, function, line, receiver, field))
            problem = refusal(rel, function, receiver, field)
            if problem:
                refused.append(problem)
            # Every direct record value used by a decision must be classified.
            if rel.endswith(("claims.py", "ledger.py")) and receiver in {
                "rec", "control", "premise", "held", "mapping", "r", "cal"
            }:
                assert field in DECLARATIONS, (rel, function, line, receiver, field)
    assert rows, "the decision inventory must inspect actual source"
    assert not refused, "\n".join(refused)


def test_inventory_covers_the_imported_repository_modules_on_the_decision_path():
    for rel in MODULES:
        package = ["g6", *rel.removesuffix(".py").split("/")[:-1]]
        tree = ast.parse((ROOT / "src/g6" / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if node.level:
                module = ".".join(package[:len(package) - node.level + 1] + module.split("."))
            if module.startswith("g6."):
                dependency = module.removeprefix("g6.").replace(".", "/") + ".py"
                assert dependency in MODULES or dependency in OUTSIDE_PREMISES, (rel, dependency)


@pytest.mark.parametrize("field", sorted(RAW_SUBJECT))
def test_inventory_catches_a_planted_caller_subject_read_under_any_name(field):
    rows = inventory(ast.parse(f"def decide(renamed):\n    return renamed.{field}\n"))
    assert any(refusal("research/echa/claims.py", function, receiver, attr)
               for function, _, receiver, attr in rows)


def test_inventory_catches_a_planted_dynamic_subject_read():
    rows = inventory(ast.parse("def decide(renamed, field):\n    return getattr(renamed, field)\n"))
    assert any(refusal("research/echa/claims.py", function, receiver, attr)
               for function, _, receiver, attr in rows)
