# SPDX-License-Identifier: Apache-2.0
"""A bogus marked section ends a reading even when the host parser tolerates it."""
import hashlib

import pytest

from g6.knowledge import _html


@pytest.mark.parametrize("marker", ["<![bogus]>", "<![ ]>", "<![unknown[secret]]>"])
@pytest.mark.parametrize("prefix", ["before", "<p>before</p>\n<div>"])
def test_bogus_marked_section_withholds_the_remaining_page(marker, prefix):
    remainder = marker + "secret after</div>"
    title, text, unread = _html.read_html(prefix + remainder)
    assert (title, text) == ("", "before")
    assert len(unread) == 1
    assert hashlib.sha256(remainder.encode()).hexdigest() in unread[0]
    assert "the rest was not read" in unread[0]
    assert "secret" not in unread[0]


def test_marked_section_text_inside_a_comment_does_not_stop_the_reader():
    assert _html.read_html("<p>before</p><!-- <![bogus]> --><p>after</p>") == (
        "", "before\n\nafter", ())
