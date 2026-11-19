# SPDX-License-Identifier: Apache-2.0
"""Only the guide's structural numbered-step labels are census captions."""
from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest


def _gate():
    spec = importlib.util.spec_from_file_location("_repair4_census", Path(__file__).with_name("test_split_repair.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("verbatim", [False, True])
@pytest.mark.parametrize("text,want", [
    ("1. **Retain all caveats.**", ["Retain all caveats."]),
    ("1. Context.\n\n   **Retain all caveats.**", ["Context.", "Retain all caveats."]),
    ("1. Context.\n\n   **Retain all caveats.** More detail.",
     ["Context.", "Retain all caveats.", "More detail."]),
    ("1. Context.\n   - **Retain all caveats.** More detail.",
     ["Context.", "Retain all caveats.", "More detail."]),
    ("1. Context.\n\n   > **Retain all caveats.** More detail.",
     ["Context.", "Retain all caveats.", "More detail."]),
    ("1. Context.\n\n   1. **Retain all caveats.**", ["Context.", "Retain all caveats."]),
    ("1. > **Retain all caveats.** More detail.", ["Retain all caveats.", "More detail."]),
    ("1. **Except for `examples`.**", ["Except for examples."]),
    ("1. **Retain `all` caveats.** More detail.", ["Retain all caveats.", "More detail."]),
    ("**Retain all caveats.** More detail.", ["Retain all caveats.", "More detail."]),
])
def test_bold_prose_outside_the_caption_form_is_visible(text, want, verbatim):
    assert _gate()._sentences(text, verbatim=verbatim) == want


@pytest.mark.parametrize("label", ["Intent.", "Never stop.", "Only the person decides.",
                                   "Acceptance first, then test first."])
@pytest.mark.parametrize("loose", [False, True])
def test_caption_structure_is_independent_of_its_words(label, loose):
    text = f"1. **{label}** Never invent its observations."
    if loose:
        text += "\n\n2. Next step."
    want = ["Never invent its observations."] + (["Next step."] if loose else [])
    assert _gate()._sentences(text) == want


def test_census_has_no_word_collection_for_sentence_admission():
    """Catch a vocabulary filter even if its constant or helper is renamed."""
    tree = ast.parse(Path(__file__).with_name("test_split_repair.py").read_text(encoding="utf-8"))
    # HTML tag sets define rendered structure, not an instruction vocabulary.
    structural_sets = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id in {"void", "blocks"}
                         for target in node.targets)}
    # The CSV's column schema cannot select sentence content either.
    structural_sets.update(id(node.comparators[0]) for node in ast.walk(tree)
                           if isinstance(node, ast.Compare) and isinstance(node.left, ast.Call)
                           and isinstance(node.left.func, ast.Name) and node.left.func.id == "set"
                           and len(node.left.args) == 1 and isinstance(node.left.args[0], ast.Name)
                           and node.left.args[0].id == "row")
    word_collections = [node.lineno for node in ast.walk(tree)
                        if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and len(node.elts) > 1
                        and id(node) not in structural_sets
                        and all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                                and re.fullmatch(r"[a-z]+(?: [a-z]+)*", item.value) for item in node.elts)]
    assert not word_collections, f"sentence admission has a word collection at {word_collections}"


def test_route_gate_rejects_a_visible_bold_qualification(monkeypatch):
    gate = _gate()
    target = (gate.GUIDE / "references/fit.md").resolve()
    read = Path.read_text

    def changed(path, *args, **kwargs):
        text = read(path, *args, **kwargs)
        if path.resolve() == target:
            return text.replace("Never invent its observations.",
                                "\n\n1. Context.\n\n   Never invent its observations."
                                "\n\n   **Except for `examples`.**\n\n")
        return text

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AssertionError, match="qualifies original authority rules"):
        gate.test_each_route_reaches_its_original_safety_and_authority_sentences("fit")


def test_repinning_does_not_grant_a_new_sentence_drop(monkeypatch):
    gate = _gate()
    original = "Preserve all upstream limitations."
    table = ("sentence\treason\treplacement\n" + original
             + "\tdropped-because: Unreviewed.\t-\n")
    rows = list(csv.DictReader(table.splitlines(), delimiter="\t"))
    monkeypatch.setattr(gate, "EXCEPTION_ROWS_SHA256",
                        hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest())
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs:
                        table if path == gate.EXCEPTIONS else read(path, *args, **kwargs))
    with pytest.raises(AssertionError, match="lead review"):
        gate._reviewed_exceptions(original)
