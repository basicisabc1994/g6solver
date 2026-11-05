import pytest

from g6.workspace.align_prompt_library.library import Library, placeholders
from g6.workspace.align_prompt_library.metacognition import (
    CATALOG, get_prompt, install_catalog, render_prompt,
)


def test_catalog_unique_digest_and_covers_source_stages():
    names = [item.name for item in CATALOG]
    assert len(names) == len(set(names))
    assert {"mc-constraints", "mc-first-principles", "mc-heuristics",
            "mc-self-assess", "mc-satisficing", "mc-goal-decompose",
            "mc-research-needed", "ct-critical-review",
            "pe-generate-system-prompt", "pe-revise-system-prompt"} <= set(names)
    for entry in CATALOG:
        assert entry.digest and entry.template and entry.source
        assert not entry.template.startswith("You are an all-knowing")


def test_strict_render_and_no_recursive_value_expansion():
    template = get_prompt("ct-critical-review")
    keys = placeholders(template.template)
    vals = {k: "sample" for k in keys}
    vals["candidate"] = "{{objective}}"
    result = render_prompt("ct-critical-review", vals)
    assert "{{objective}}" in result["text"]
    assert result["template_digest"] == template.digest
    with pytest.raises(ValueError):
        render_prompt("ct-critical-review", {"objective": "missing keys"})
    with pytest.raises(ValueError):
        render_prompt("ct-critical-review", {**vals, "unexpected": "no"})


def test_install_is_explicit_and_uses_existing_versioned_library(tmp_path):
    library = Library(tmp_path / "catalog.jsonl")
    first = install_catalog(library)
    again = install_catalog(library)
    assert len(first) == len(again) == len(CATALOG)
    for entry in CATALOG:
        record = library.get(entry.name)
        assert record["digest"] == entry.digest
        assert record["kind"] == "prompt"
        assert record["declared"]["status"] == "candidate_template"
        assert len(library.history(entry.name)) == 1
