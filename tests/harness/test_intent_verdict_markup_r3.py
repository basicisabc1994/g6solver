# SPDX-License-Identifier: Apache-2.0
"""Repair 3: markup around markers and values cannot hide a verdict."""
import pytest

from g6.core import semantic
from g6.harness import completion_loop as loop

WRAPPERS = [('_', '_'), ('__', '__'), ('*', '*'), ('**', '**'), ('~~', '~~'),
            ('[', ']'), ('(', ')'), ('<em>', '</em>'), ('<b>', '</b>')]


@pytest.mark.parametrize('marker_wrap', WRAPPERS)
@pytest.mark.parametrize('value_wrap', WRAPPERS)
@pytest.mark.parametrize('word', ['none', 'defect'])
@pytest.mark.parametrize('conflict', [False, True])
def test_markup_grid(marker_wrap, value_wrap, word, conflict):
    marker = marker_wrap[0] + 'VeRdIcT' + marker_wrap[1]
    value = value_wrap[0] + word + value_wrap[1]
    body = f'READ: token\n{marker}: {value}\n'
    if conflict:
        body += 'VERDICT: ' + ('none' if word == 'defect' else 'defect') + '\n'
    with semantic.request(llm=None):
        got = loop.critic_report(body, marker='token')
    expected = 'unreadable' if conflict else word
    assert got['verdict'] == expected, got
    assert loop._floor_critic_report(body, marker='token')['verdict'] == expected


@pytest.mark.parametrize('tail', ['_VERDICT_ maybe', '__VERDICT__', '<em>VERDICT</em> ??',
                                  'VERDICT_: _unknown_', 'VERDICT[0]: none', '|VERDICT| ??'])
def test_malformed_markup_marker_cannot_hide_beside_clean_verdict(tail):
    with semantic.request(llm=None):
        got = loop.critic_report('READ: token\nVERDICT: none\n' + tail, marker='token')
    assert got['verdict'] == 'unreadable', got


@pytest.mark.parametrize('value', ['none**defect', 'none~~defect', 'none<em>defect</em>', 'none[defect]'])
def test_stripping_markup_cannot_change_a_reading_into_agreement(value):
    with semantic.request(llm=None):
        got = loop.critic_report('READ: token\nVERDICT: none\n_VERDICT_: ' + value, marker='token')
    assert got['verdict'] == 'unreadable', got


