# SPDX-License-Identifier: Apache-2.0
"""A route's anchor is found by its own source line, as before the CommonMark reading (the wire-parsers repair,
stage 1): an exact line, counted for duplicates, ending at a column-zero heading of its level or above (a bold lead:
at a heading, a blank line or a list item), with its lines given back as they stand. The one change is that a fence
is read by CommonMark, so a ``~~~`` fence hides a heading too."""
from __future__ import annotations

from g6.skills.routing import section

FIND = "## Find"
LEAD = "**Lead.**"


def test_the_exact_anchor_beside_a_closed_twin_is_the_one_found():
    root = "# T\n\n## Find ###\nnot this\n\n## Find\nthis\n"
    assert section(root, FIND) == "## Find\nthis"


def test_a_quoted_heading_is_no_anchor():
    assert section("# T\n\n> ## Find\n> quoted\n", FIND) is None


def test_a_quoted_bold_lead_is_no_anchor():
    assert section("# T\n\n> **Lead.** quoted\n", LEAD) is None


def test_a_bold_lead_borne_by_a_heading_is_no_anchor():
    assert section("# T\n\n## **Lead.** in a heading\n", LEAD) is None


def test_two_lines_with_the_same_bold_lead_are_no_anchor():
    assert section("# T\n\n**Lead.** first\n**Lead.** second\n", LEAD) is None


def test_a_bold_lead_item_stops_at_its_nested_child():
    root = "# T\n\n- **Lead.** one\n  - nested a\n- **Other.** two\n"
    assert section(root, LEAD) == "- **Lead.** one"


def test_a_bold_lead_item_stops_at_a_blank_line():
    root = "# T\n\n- **Lead.** one\n\n  second paragraph of the item\n- **Other.** two\n"
    assert section(root, LEAD) == "- **Lead.** one"


def test_a_heading_in_a_tilde_fence_is_no_anchor():
    assert section("# T\n\n~~~\n## Find\n~~~\n", FIND) is None


def test_a_heading_in_a_backtick_fence_is_no_anchor():
    assert section("# T\n\n```\n## Find\n```\n", FIND) is None


def test_a_heading_after_a_tilde_fence_ends_nothing_inside_it():
    root = "## Find\nbody\n~~~\n## Not a heading\n~~~\nstill body\n## Next\n"
    assert section(root, FIND) == "## Find\nbody\n~~~\n## Not a heading\n~~~\nstill body"


def test_an_exact_heading_line_inside_an_html_block_is_found():
    assert section("<div>\n## Find\nbody\n</div>\n", FIND) == "## Find\nbody\n</div>"


def test_blank_lines_inside_and_closing_a_section_are_kept():
    root = "## Find\na\n\nb\n\n## Next\n"
    assert section(root, FIND) == "## Find\na\n\nb\n"


def test_a_quoted_or_setext_heading_does_not_end_a_section():
    root = "## Find\na\n> # Quoted\nb\nSetext\n======\nc\n## Next\n"
    assert section(root, FIND) == "## Find\na\n> # Quoted\nb\nSetext\n======\nc"
