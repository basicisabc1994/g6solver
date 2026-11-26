"""Regression for review-splitfu4 #1: a qualification written in the structural caption form.

The authority census dropped every `1. **Label.** prose` caption, so a
qualification written as a caption vanished and a weakened rule still passed
the route gate.
"""
import importlib.util
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "skills" / "test_split_repair.py"


def _gate():
    spec = importlib.util.spec_from_file_location("_census_caption_regression", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_caption_qualification_is_visible_to_the_census():
    text = "1. **Except for examples.** Never invent its observations."
    assert "Except for examples." in _gate()._sentences(text)


def test_route_gate_rejects_a_caption_form_qualification(monkeypatch):
    gate = _gate()
    target = (gate.GUIDE / "references/fit.md").resolve()
    read = Path.read_text

    def changed(path, *args, **kwargs):
        text = read(path, *args, **kwargs)
        if path.resolve() == target:
            return text.replace("Never invent its observations.",
                                "\n\n1. **Except for examples.** Never invent its observations.\n\n")
        return text

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AssertionError):
        gate.test_each_route_reaches_its_original_safety_and_authority_sentences("fit")
