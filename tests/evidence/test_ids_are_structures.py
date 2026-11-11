# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""An identity is a structure, never a joined string (G6 0.3, slice 2, repair 2; review 2's finding 6; class
``identity_from_rendering``, met in repair 1).

Every id this change builds from parts is the canonical JSON of the parts, or its digest, through one function
(``g6.evidence.mapping.id_of``). Two pytest node ids pytest itself produces, joined to a requirement id with ``#``, gave
one control id for two different pairs (finding 6). The inventory lists, by AST, every id built from parts in the
modules slice 2 wrote or changed (a string or bytes join, format or concatenation that produces an id field), and fails
on any not built through the one function.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import subprocess
import sys
from pathlib import Path

import g6
from g6.evidence import mapping as m
from g6.harness import patch_review, requirement_evidence

SRC = Path(g6.__file__).resolve().parent


# --- review 2's finding 6, brought in as this change's own ------------------------------------------------------------


def test_finding_6_distinct_pytest_checks_and_requirements_do_not_share_a_control_id(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_cases.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('x', [0, 1], ids=['x', 'x]#y'])\n"
        "def test_case(x):\n    assert x in (0, 1)\n", encoding="utf-8")
    first, second = "test_cases.py::test_case[x]", "test_cases.py::test_case[x]#y]"
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "--noconftest",
         "-c", str(tmp_path / "pytest.ini"), "test_cases.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=True)
    assert {first, second} <= set(collected.stdout.splitlines())   # pytest itself produces these two distinct ids
    evaluated = [
        {"id": "y]#R2", "text": "first case", "kind": "requirement", "provenance": "stated",
         "checks": [{"id": first, "provenance": "stated", "verdict": "passed"}]},
        {"id": "R2", "text": "second case", "kind": "requirement", "provenance": "stated",
         "checks": [{"id": second, "provenance": "stated", "verdict": "passed"}]},
    ]
    common = dict(runs=[{"check": patch_review.AT_BASE, "outcomes": {first: "failed", second: "failed"}}],
                  artifact="a" * 64, base_commit="0" * 40, check_sha=patch_review._check_shas(tmp_path, [first, second]),
                  written=lambda _: False, environment={"python": sys.executable},
                  again={"verdicts": {first: "passed", second: "passed"}})
    for one in evaluated:
        assert requirement_evidence.assess([one], **common)["requirements"][0]["checked"] is True
    combined = requirement_evidence.assess(evaluated, **common)
    assert [one["checked"] for one in combined["requirements"]] == [True, True], (
        "distinct check/requirement pairs were concatenated into one control id", combined["refused"])


def test_ids_from_parts_are_the_canonical_json_of_the_parts_or_its_digest():
    assert m.id_of("base-commit", "a#b", "c") != m.id_of("base-commit", "a", "b#c")
    assert json.loads(m.id_of("stated-run", "t.py::t[x]#y]")) == ["stated-run", "t.py::t[x]#y]"]
    assert m.id_of("x", digest=True) == g6.core.ledger.digest(["x"])
    binding = m.CheckBinding("request", "task", "R1", "a" * 64, "5" * 64, "f" * 64, "c" * 64)
    assert json.loads(m.check_id_of(binding)) == ["task", g6.core.ledger.digest(binding.to_dict())]


def test_a_check_s_identity_is_its_node_id_and_its_file_s_digest_as_one_structure(tmp_path):
    (tmp_path / "test_a.py").write_bytes(b"def test_a():\n    pass\n")
    import hashlib

    found = patch_review._check_shas(tmp_path, ["test_a.py::test_a"])
    assert found == {"test_a.py::test_a": m.id_of("test_a.py::test_a", hashlib.sha256(
        (tmp_path / "test_a.py").read_bytes()).hexdigest(), digest=True)}


# --- the inventory: every id built from parts in the modules slice 2 wrote or changed, from the code ----------------

#: The modules slice 2 wrote or changed (git diff --name-only ea3f96c..77d6af5 -- src), and this repair's.
SLICE_2 = ("evidence/mapping.py", "harness/confirmed_expectation.py", "harness/patch_report.py",
           "harness/patch_review.py", "harness/requirement_evidence.py", "harness/verification_templates.py",
           "research/ahi/flag.py", "research/ahi/lifecycle.py", "research/echa/__init__.py", "research/echa/access.py",
           "research/echa/claims.py", "research/echa/competence.py", "research/echa/custody.py",
           "research/echa/episodes.py", "research/echa/example.py", "research/echa/ledger.py", "research/echa/trace.py",
           "research/echa/view.py", "research/mission_bridge/bundle.py")
#: Ids built from parts that this change did not build: each was there at the change's base (ea3f96c), is an id ECHA
#: persists in its ledgers, and is a fixed prefix with a digest or a count, which no part can make ambiguous.
NOT_THIS_CHANGE = {
    ("research/echa/custody.py", "checked_evidence", "evidence_id"): "a custody record's id: a prefix and the "
                                                                      "binding's digest, persisted",
    ("research/echa/ledger.py", "Ledger.add_incident", "ident"): "an incident's id: a prefix and a count, persisted",
}


def _idlike(name: str) -> bool:
    low = name.lower()
    return low in ("id", "ident", "ids") or low.endswith(("_id", "_ids", "id_of", "_sha", "_sha256"))


def _built(node: ast.AST) -> bool:
    """Whether an expression makes text or bytes from parts: an f-string with a value in it, a join, a format, or a
    ``+`` or ``%`` with a text or bytes operand."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("join", "format"):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return any(isinstance(sub, ast.Constant) and isinstance(sub.value, (str, bytes)) or isinstance(sub, ast.JoinedStr)
                   or (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "encode")
                   for sub in ast.walk(node))
    return False


def _fields_of(module: object, name: str) -> list[str]:
    cls = getattr(module, name, None)
    return [f.name for f in dataclasses.fields(cls)] if dataclasses.is_dataclass(cls) else []


def scan(rel: str, tree: ast.Module, module: object = None) -> set[tuple[str, str, str]]:
    """(function, the id field, the expression) for every id built from parts in a module: assigned to, passed as,
    keyed as or returned as an id field, passed positionally as a dataclass's id field, or hashed."""
    found: set[tuple[str, str, str]] = set()

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(child, ast.ClassDef) and _idlike(child.name):
                    for ret in ast.walk(child):
                        if isinstance(ret, ast.Return) and ret.value is not None and _built(ret.value):
                            found.add((".".join([*where, child.name]), child.name, ast.unparse(ret.value)))
                visit(child, [*where, child.name])
                continue
            here = ".".join(where) or "<module>"
            if isinstance(child, ast.keyword) and child.arg and _idlike(child.arg) and _built(child.value):
                found.add((here, child.arg, ast.unparse(child.value)))
            if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None and _built(child.value):
                for target in (child.targets if isinstance(child, ast.Assign) else [child.target]):
                    name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
                    if _idlike(name):
                        found.add((here, name, ast.unparse(child.value)))
            if isinstance(child, ast.Dict):
                for key, value in zip(child.keys, child.values, strict=True):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str) and _idlike(key.value) and \
                            _built(value):
                        found.add((here, key.value, ast.unparse(value)))
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name) and module is not None:
                    names = _fields_of(module, func.id)
                    for index, arg in enumerate(child.args):
                        if index < len(names) and _idlike(names[index]) and _built(arg):
                            found.add((here, names[index], ast.unparse(arg)))
                if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "hashlib"
                        and any(_built(arg) for arg in child.args)):
                    found.add((here, "digest", ast.unparse(child)))
            visit(child, where)

    visit(tree, [])
    return found


def _module(rel: str) -> object:
    parts = rel.removesuffix(".py").split("/")
    return importlib.import_module(".".join(["g6", *(parts[:-1] if parts[-1] == "__init__" else parts)]))


FOUND = {(rel, *one) for rel in SLICE_2 for one in scan(rel, ast.parse((SRC / rel).read_text(encoding="utf-8")),
                                                         _module(rel))}


def test_every_id_built_from_parts_in_slice_2_s_modules_goes_through_the_one_function():
    assert all((SRC / rel).is_file() for rel in SLICE_2)
    left = {(rel, function, field) for rel, function, field, _ in FOUND}
    assert sorted(left - set(NOT_THIS_CHANGE)) == [], "an id joined from parts: build it with id_of"
    assert sorted(set(NOT_THIS_CHANGE) - left) == [], "listed ids the scan no longer finds: remove them"


def test_the_ids_this_change_builds_call_the_one_function():
    calls = {(rel, function) for rel in ("evidence/mapping.py", "harness/requirement_evidence.py",
                                         "harness/confirmed_expectation.py", "harness/patch_review.py")
             for function in _callers((SRC / rel).read_text(encoding="utf-8"), "id_of")}
    # a confirmed expectation's id is derived from its question's digest and the chosen option (repair 3)
    assert {("evidence/mapping.py", "check_id_of"), ("harness/requirement_evidence.py", "records_for"),
            ("harness/confirmed_expectation.py", "_expectation_id"),
            ("harness/patch_review.py", "_check_shas")} <= calls


def _callers(text: str, name: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and any(isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == name
                                                      for sub in ast.walk(node)):
            found.add(node.name)
    return found


def test_the_scan_finds_each_form_of_a_joined_id():
    # survival control: each way an id was joined before this repair is found
    planted = ast.parse(
        "import hashlib\n"
        "def records(check, rid):\n"
        "    a = Control(f'base-commit:{check}#{rid}', 's', rid, 'g6', 3, True)\n"
        "    b = SourceRecord(record_id='stated-run:' + check)\n"
        "    ident = 'confirmed-' + check[:16]\n"
        "    c = {'control_id': '#'.join([check, rid])}\n"
        "    d = hashlib.sha256(check.encode() + b'\\0' + rid.encode()).hexdigest()\n"
        "def check_id_of(binding):\n"
        "    return f'{binding.task_id}:{binding.digest}'\n")

    class Planted:
        @dataclasses.dataclass
        class Control:
            control_id: str

    found = {(function, field) for function, field, _ in scan("planted.py", planted, Planted)}
    assert found == {("records", "control_id"), ("records", "record_id"), ("records", "ident"), ("records", "digest"),
                     ("check_id_of", "check_id_of")}
