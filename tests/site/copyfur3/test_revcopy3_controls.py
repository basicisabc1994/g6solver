"""Review 3's controls: probes that pass on this commit, so a broken fixture cannot pass for a finding, and the
checks walked that held."""
import html
import json
import re


def ld(data):
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


def cc(honesty, page):
    return honesty.claims(honesty._page_copy(page))


def test_the_instrument_finds_a_claim_and_passes_plain_copy(honesty):
    assert honesty.claims('G6 verifies every patch.') and honesty.g6_claims('<p>G6 verifies every patch.</p>')
    assert honesty.claims('G6 checks every patch.') == [] and honesty.g6_claims('<p>G6 checks every patch.</p>') == []
    assert honesty.g6_claims('<p>G6 checks every patch. Formal methods verify programs.</p>') == []
    assert honesty.g6_claims('<p>G6 asks Dr. Smith to verify every patch.</p>')


def test_the_real_pages_used_by_the_findings_pass(honesty, site):
    for path in ('/', '/docs/', '/get-started/'):
        assert cc(honesty, site(path)) == [], path
        honesty.structured_inventory(site(path))


def test_blocks_parted_by_white_space_and_marks_that_are_not_folded_are_read(honesty):
    assert cc(honesty, '<p><span class="h3">Evidence</span> <span>Verified results.</span></p>')
    assert cc(honesty, '<h3>Built-in verification</h3><p>Run it after every change.</p>')
    assert cc(honesty, '<p>G6 answers are verified<sup>[1]</sup>.</p>')
    assert cc(honesty, '<p>G6 answers are verified®.</p>')
    assert cc(honesty, '<p>G6 veri<em>fies</em> every patch.</p>')            # a word split by markup stays whole


def test_structured_data_walks_that_held(honesty):
    claim = 'G6 verifies every patch.'
    for data in ({'description': {'en': claim}}, {'@graph': [{'@type': 'Article', 'headline': claim}]},
                 {'description': {'@language': 'en', '@value': claim}}, [[claim]], {'schema:description': claim},
                 {'image': [{'caption': claim}]}, {'@included': [{'name': claim}]}, {'description': {'@none': claim}}):
        assert cc(honesty, ld(data)), data
    for data in ({'url': claim}, {'@id': claim}, {'image': [claim]}, {'@reverse': {'about': claim}},
                 {'sameAs': {'@list': [claim]}}, {'@context': {'name': claim}}):
        assert cc(honesty, ld(data)) == [], data
    for body in ('﻿{"description": "x"}', '{"description": "x"} // note', '<!-- {"description": "x"} -->',
                 '{"description": NaN}', ''):
        page = '<script type="application/ld+json">' + body + '</script>'
        try:
            honesty.structured_inventory(page)
        except AssertionError:
            continue
        raise AssertionError(f'Unreadable structured data passed the inventory: {body!r}')
    for kind in ('APPLICATION/LD+JSON', ' application/ld+json ', 'application/ld+json; charset=utf-8'):
        assert cc(honesty, f'<script type="{kind}">{{"description": "{claim}"}}</script>'), kind
    # a second key of the same name wins, as it does in a browser's JSON reader
    assert cc(honesty, ld({'description': 'Checked.'}).replace('}', ', "description": "%s"}' % claim))


def test_link_closure_walks_that_held(honesty):
    repository = honesty.views.evidence.REPO
    readme = honesty.OSS / 'README.md'
    for link in (repository + 'README.md', repository + 'README.md#install', repository + 'README.md?plain=1',
                 repository + 'docs/../README.md'):
        assert honesty._linked_document(link, None) == readme, link
    assert honesty._linked_document('../README.md', honesty.OSS / 'docs') == readme
    assert honesty._linked_document('/README.md', honesty.OSS / 'docs') == readme
    assert honesty._linked_document('README.md', None) is None            # a page's local link is a page of the site
    assert honesty._linked_document(repository + 'docs/theory/x.md', None) is None
    framed = '<iframe srcdoc="' + html.escape(f'<a href="{repository}README.md">x</a>') + '"></iframe>'
    assert honesty._reader(framed).links == [repository + 'README.md']
    # every document under docs/ and at the root that is not a named exclusion is in the inventory, but two fixtures
    outside = {path.relative_to(honesty.OSS).as_posix()
               for path in [*honesty.OSS.glob('*.md'), *(honesty.OSS / 'docs').rglob('*.md')]
               if path not in set(honesty.MARKDOWN) and not honesty._excluded_document(path)}
    assert outside <= {'docs/skills/using-g6/tests/context.md', 'docs/skills/using-g6/tests/scenarios.md',
                       'CLAUDE.md', 'CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', 'CHANGELOG.md'}, outside


def test_the_real_essays_keep_g6_and_the_claim_words_in_separate_blocks(honesty, site):
    names = re.compile(r'\b(?:G6|G6Solver)\b', re.IGNORECASE)
    for page in honesty.PAGES:
        if page.namespace == 'blog':
            for unit in honesty._units(site('/' + page.path)):
                text = honesty._resolve(unit)
                assert not (names.search(honesty._normalise(text)) and honesty.claims(text)), text[:200]


def test_the_export_is_built_afresh_and_removed(honesty):
    before = set((honesty.OSS / '.build').glob('honesty-export-*'))
    exported = honesty.exported_pages()
    assert '404.html' in exported and 'index.html' in exported
    assert set((honesty.OSS / '.build').glob('honesty-export-*')) == before
