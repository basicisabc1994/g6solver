# SPDX-License-Identifier: Apache-2.0
"""Public result-label claims identify the surface that returns the envelope."""
from pathlib import Path
import ast
import html
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _public_sources(root):
    return sorted({root / "README.md", root / "AGENT_GUIDE.md", root / "site/mkdocs.yml"} | {
        path for folder in ("web/templates", "site/docs", "web/content", "web/pages")
        for path in (root / folder).rglob("*") if path.is_file()
        and (folder != "web/pages" or path.suffix == ".py")
    })


def _label_claims(text):
    text = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", text, flags=re.M | re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"</(?:p|li|div|section|h[1-6])>", "\n\n", text)
    text = html.unescape(re.sub(r"</?[A-Za-z][^>]*>", "", text))
    paragraphs = [unit for paragraph in text.split("\n\n")
                  for unit in (paragraph.splitlines() if paragraph.lstrip().startswith("|") else [paragraph])]
    for paragraph in paragraphs:
        paragraph = " ".join(paragraph.split())
        for claim in re.split(r"(?<=[.!?])\s+", paragraph):
            yield from _sentence_label_claims(claim)


def _sentence_label_claims(claim):
    if re.match(r"\|\s*`[A-Za-z_]\w*`\s*\|", claim):
        return  # Per-component table descriptions are not global surface claims.
    universal = re.search(r"\b(?:every|each|all)\s+(?:(?:of\s+)?(?:the\s+)?(?:G6(?:'s)?|its)\s+)?"
                          r"(?:results?|repl(?:y|ies)|answers?|returns?)\b", claim, re.I)
    labelled = re.search(r"\blabel(?:s|led|ed)?\b|\bmarked\b|completion_state|\bstates?\b|"
                         r"qualified-draft|blocked-escalated", claim, re.I)
    tail = claim[universal.end():] if universal else ""
    says_state = re.search(r"\b(?:says?|carr(?:y|ies)|has|have)\b|"
                           r"\b(?:is|are)\s+(?:\w+\s+){0,3}(?:marked|labelled|labeled)\b", tail, re.I)
    # A subject-first promise puts its action before the universal noun phrase:
    # "G6 labels each of its results ..." / "marks every result ...".
    labels_subject = universal and re.search(r"\b(?:labels?|marks?)\s*$",
                                             claim[:universal.start()], re.I)
    names_states = all(state in claim.lower() for state in ("verified", "qualified-draft", "blocked-escalated"))
    direct = re.search(r"\blabels (?:every |each |all )?results?\b|"
                       r"\bresults\b.{0,180}\b(?:are|come back) labelled\b|"
                       r"\blabels?\b.{0,100}\bevery return\b", claim, re.I)
    if labels_subject or (universal and labelled and (says_state or names_states)) \
            or "three labels" in claim.lower() or direct:
        yield claim


def _assert_label_scope(text, name):
    if str(name).endswith(".py"):
        # Read copy without executing page modules. AST joins adjacent Python string literals.
        text = "\n\n".join(node.value for node in ast.walk(ast.parse(text))
                            if isinstance(node, ast.Constant) and isinstance(node.value, str))
    for claim in _label_claims(text):
        if "worked-example page" in claim:
            continue
        assert "MCP server" in claim, (name, claim)
        assert "checks and work tools" in claim, (name, claim)


@pytest.mark.parametrize("name", [p.relative_to(ROOT).as_posix() for p in _public_sources(ROOT)])
def test_general_result_label_claims_name_the_mcp_server(name):
    _assert_label_scope((ROOT / name).read_text(encoding="utf-8"), name)


def test_census_includes_new_nested_pages_and_metadata(scratch):
    names = ["web/templates/new/page.html", "site/docs/new/page.md", "web/content/new/page.md", "site/mkdocs.yml",
             "web/pages/new/page.py"]
    for name in names:
        path = scratch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        claim = "Every result is marked with a label."
        path.write_text(repr(claim) if path.suffix == ".py" else claim, encoding="utf-8")
    found = _public_sources(scratch)
    assert all(scratch / name in found for name in names)
    for name in names:
        with pytest.raises(AssertionError):
            _assert_label_scope((scratch / name).read_text(encoding="utf-8"), name)


@pytest.mark.parametrize("claim", [
    "Every G6 result says what was checked: verified, qualified-draft or blocked-escalated, with the reason.",
    "Every result is marked <code>verified</code> (it passed the required checks), <code>qualified-draft</code> "
    "(useful, but incomplete &mdash; with its limits stated), or <code>blocked-escalated</code> "
    "(too risky to proceed without a person).",
    "site_description: Open-source harness for AI agents — checks their work, gates risky actions and labels "
    "every result for what the evidence shows. Free, local-first, Apache-2.0.",
    "Each G6 reply carries a completion state.",
    "G6 labels each of its results for what the evidence supports.",
    "Settles small logic questions exactly with a solver, and labels each answer for what it is",
    "G6 labels all of its replies with a completion state.",
    "G6 marks every result with its state.",
    "All G6 answers are labelled with a state.",
    "Each answer from G6 is marked qualified-draft.",
    "Every result says its state.",
    "Every result is marked with a label.",
    "Every result returned over G6's MCP server carries one of three labels.",
    "Results returned over G6's MCP server come back labelled with what their evidence supports.",
    "G6 labels results returned over its MCP server for what the evidence shows.",
    "| Result labels and the envelope on every return, checked by `g6.invariants` | implemented |",
])
def test_census_rejects_generic_and_discovery_inclusive_claims(claim):
    with pytest.raises(AssertionError):
        _assert_label_scope(claim, "new-page")


def test_census_does_not_borrow_scope_from_a_different_sentence():
    with pytest.raises(AssertionError):
        _assert_label_scope("G6's checks and work tools use the MCP server. Every G6 reply carries a state.", "page")


def test_component_check_description_is_not_a_global_label_promise():
    _assert_label_scope("Checks that every result's label matches the evidence attached to it", "component")


def test_solver_operation_answer_labels_are_scoped_to_that_operation():
    _assert_label_scope("Settles small logic questions exactly with a solver and reports the solver "
                        "operation's own answer labels", "component")


def test_subject_first_promise_can_name_its_scope():
    _assert_label_scope("The MCP server labels each result from G6's checks and work tools for what "
                        "the evidence supports.", "page")


@pytest.mark.parametrize("name", ["README.md", "web/content/faq.md", "site/docs/faq.md"])
def test_cli_output_is_distinguished_from_the_ok_envelope(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "command line prints the result alone" in text
