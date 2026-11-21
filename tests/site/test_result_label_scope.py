# SPDX-License-Identifier: Apache-2.0
"""Public result-label claims identify the surface that returns the envelope."""
from pathlib import Path
import html
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _public_sources(root):
    return sorted({root / "README.md", root / "AGENT_GUIDE.md", root / "site/mkdocs.yml"} | {
        path for folder in ("web/templates", "site/docs", "web/content")
        for path in (root / folder).rglob("*") if path.is_file()
    })


def _label_claims(text):
    text = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", text, flags=re.M | re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"</(?:p|li|div|section|h[1-6])>", "\n\n", text)
    text = html.unescape(re.sub(r"</?[A-Za-z][^>]*>", "", text))
    paragraphs = [unit for paragraph in text.split("\n\n")
                  for unit in (paragraph.splitlines() if paragraph.lstrip().startswith("|") else [paragraph])]
    for paragraph in paragraphs:
        claim = " ".join(paragraph.split())
        if re.match(r"\|\s*`[A-Za-z_]\w*`\s*\|", claim):
            continue  # Per-component table descriptions are not global surface claims.
        universal = re.search(r"\b(?:every|each|all) (?:results?|returns?)\b", claim, re.I)
        labelled = re.search(r"\blabel(?:s|led)?\b|\bmarked\b|completion_state", claim, re.I)
        direct = re.search(r"\blabels results\b|\bresults\b.{0,180}\b(?:are|come back) labelled\b", claim, re.I)
        if (universal and labelled) or "three labels" in claim or direct:
            yield claim


def _assert_label_scope(text, name):
    for claim in _label_claims(text):
        if "worked-example page" in claim:
            continue
        assert "MCP server" in claim, (name, claim)
        assert "checks and work tools" in claim, (name, claim)


@pytest.mark.parametrize("name", [p.relative_to(ROOT).as_posix() for p in _public_sources(ROOT)])
def test_general_result_label_claims_name_the_mcp_server(name):
    _assert_label_scope((ROOT / name).read_text(encoding="utf-8"), name)


def test_census_includes_new_nested_pages_and_metadata(scratch):
    names = ["web/templates/new/page.html", "site/docs/new/page.md", "web/content/new/page.md", "site/mkdocs.yml"]
    for name in names:
        path = scratch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Every result is marked with a label.", encoding="utf-8")
    found = _public_sources(scratch)
    assert all(scratch / name in found for name in names)
    for name in names:
        with pytest.raises(AssertionError):
            _assert_label_scope((scratch / name).read_text(encoding="utf-8"), name)


@pytest.mark.parametrize("claim", [
    "Every result is marked with a label.",
    "Every result returned over G6's MCP server carries one of three labels.",
    "Results returned over G6's MCP server come back labelled with what their evidence supports.",
    "G6 labels results returned over its MCP server for what the evidence shows.",
    "| Result labels and the envelope on every return, checked by `g6.invariants` | implemented |",
])
def test_census_rejects_generic_and_discovery_inclusive_claims(claim):
    with pytest.raises(AssertionError):
        _assert_label_scope(claim, "new-page")


@pytest.mark.parametrize("name", ["README.md", "web/content/faq.md", "site/docs/faq.md"])
def test_cli_output_is_distinguished_from_the_ok_envelope(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "command line prints the result alone" in text
