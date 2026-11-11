# SPDX-License-Identifier: Apache-2.0
"""Root inventories, planted through the site's real readers (honest copy drift)."""
import ast
import inspect
import json
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urlsplit

import markdown
import pytest
from django.template.loader import render_to_string


# Python-Markdown lists its block elements, including raw HTML accepted by this renderer.
# Plant the same split claim in every listed block; containers also have realistic nested plants below.
@pytest.mark.parametrize('tag', markdown.util.BLOCK_LEVEL_ELEMENTS)
def test_every_renderer_block_keeps_uncertain_sentence_boundaries(honesty, tag):
    page = honesty.views.markdown_html(f'<{tag}>G6 uses Dr. Smith to verify every patch.</{tag}>')
    if tag in {'script', 'style', 'pre'}:  # executable/style/code blocks are outside prose, as before
        assert honesty.g6_claims(page) == [], tag
        return
    assert honesty.g6_claims(page), tag


@pytest.mark.parametrize('source', [
    'G6 uses Dr. Smith to verify patches.',
    '- G6 uses Dr. Smith to verify patches.',
    '- G6 can:\n    - verify every patch.',
    'G6 can:\n\n- verify every patch.\n- check another.',
    '> G6 uses Dr. Smith to verify patches.',
    '| Result |\n| --- |\n| G6 uses Dr. Smith to verify patches. |',
    '# G6 uses Dr. Smith to verify patches.',
    '<dl><dt>G6 can:</dt><dd>verify every patch.</dd></dl>',
])
def test_renderer_block_structures_and_colon_continuations(honesty, source):
    assert honesty.g6_claims(honesty.views.markdown_html(source)), source


def test_names_are_inventoried_from_site_identity_sources(honesty):
    class Identity(HTMLParser):
        def __init__(self):
            super().__init__()
            self.names = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'meta' and attrs.get('property') == 'og:site_name':
                self.names.add(attrs['content'])

    identity = Identity()
    identity.feed(render_to_string('base.html'))
    page = render_to_string('blog/base_post.html')
    data = json.loads(page.split('<script type="application/ld+json">', 1)[1].split('</script>', 1)[0])
    identity.names.add(data['publisher']['name'])
    assert identity.names
    for name in identity.names:
        assert honesty.g6_claims(f'<p>{name} verifies every patch.</p>'), name
    assert set(honesty.PRODUCT_NAMES) == identity.names


# Repair 2 changed this list on purpose: 'Xy' and 'Xyz' stood for "any capitalised word of up to three letters",
# a rule the closed abbreviation list replaces (a short word no longer makes a full stop uncertain; see
# copyfur2/test_inventory.py for the list's own inventory).
@pytest.mark.parametrize('token', ['A', 'a', 'C', 'Dr', 'Mr', 'Mrs', 'ETC', 'e.g', 'U.S'])
def test_one_sided_boundary_rule_retains_uncertain_tokens(honesty, token):
    text = f'G6 uses {token}. Someone to verify every patch.'
    assert honesty.sentences(text) == [text]
    assert honesty.g6_claims(f'<p>{text}</p>')


@pytest.mark.parametrize('close', ['', '"', "'", '”', '’', ')', ']', '}', '”)]'])
def test_certain_boundaries_include_closing_quotes_and_brackets(honesty, close):
    assert honesty.g6_claims(f'<p>G6 checks the patch.{close} Other tools verify every patch.</p>') == []


def test_a_list_lead_in_stops_at_the_end_of_its_list(honesty):
    page = '<p>G6 can:</p><ul><li>check a patch.</li></ul><p>Other tools verify programs.</p>'
    assert honesty.g6_claims(page) == []


def test_closed_vocabulary_every_form_prefix_case_and_discretionary_wrap(honesty):
    # Independent statement of the owner's closed English vocabulary. Existing counterverifies is retained.
    endings = ('y', 'ies', 'ied', 'ying', 'ier', 'iers', 'iable', 'iably', 'iability',
               'ication', 'ications', 'icatory')
    prefixes = ('', 're', 'un', 'pre', 'non', 'self', 'auto', 'co', 'counter')
    for ending in endings:
        for prefix in prefixes:
            for join in ('', '-') if prefix else ('',):
                word = prefix + join + 'verif' + ending
                for case in (word, word.capitalize(), word.upper()):
                    assert honesty.claims(f'G6 {case} each patch.'), case
                    for position in range(1, len(case)):
                        for wrap in ('\u00ad', '\u00ad\n    ', '\u00ad\r\n\t', '\u00ad\r    ',
                                     '\u00ad\u2028    ', '\u00ad\u2029\t'):
                            split = case[:position] + wrap + case[position:]
                            assert honesty.claims(f'G6 {split} each patch.'), repr(split)
                    for left, right in [('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’')]:
                        assert honesty.claims(f'Avoid {left}{case}{right} in copy.') == [], case
                        assert honesty.claims(f'G6 {left}{case} answers{right}.'), case
    for name in ('Verifone', 'Verifire', 'verifax'):
        assert honesty.claims(f'G6 reads {name} logs.') == [], name
    assert tuple(honesty.VERIFICATION_ENDINGS) == endings
    assert set(honesty.VERIFICATION_PREFIXES) == set(prefixes) - {''}


def test_claim_and_mention_compile_the_same_vocabulary_from_code(honesty):
    tree = ast.parse(inspect.getsource(honesty))
    for name in ('CLAIM', 'MENTION'):
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))
        assert 'VERIFICATION_WORD' in {node.id for node in ast.walk(assignment.value) if isinstance(node, ast.Name)}, name


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_json_constants_are_strict(honesty, constant):
    page = '<script type="application/ld+json">{"width":' + constant + '}</script>'
    with pytest.raises(AssertionError):
        honesty.structured_inventory(page)


@pytest.mark.parametrize('mime', [
    'application/ld+json', 'Application/LD+JSON', 'application/ld+json; charset=utf-8',
    'application/ld+json ; charset="utf-8"', 'application/ld+json;profile="https://schema.org"; charset=UTF-8',
    'application/ld+json; charset = utf-8', 'application/ld+json;charset="utf-8;other"',
])
def test_mime_parameter_forms(honesty, mime):
    message = Message()
    message['content-type'] = mime
    assert message.get_content_type() == 'application/ld+json'
    page = '<script type=\'' + mime + '\'>{"articleBody":"G6 verifies patches."}</script>'
    assert honesty.claims(honesty._page_copy(page)), mime


# JSON-LD 1.1 syntax keywords (W3C Recommendation, section 3). @context is an opaque subtree;
# the remaining structural keywords cannot conceal prose in their values or nested objects.
KEYWORDS = ('@base', '@container', '@context', '@direction', '@graph', '@id', '@import', '@included', '@index',
            '@json', '@language', '@list', '@nest', '@none', '@prefix', '@propagate', '@protected', '@reverse',
            '@set', '@type', '@value', '@version', '@vocab')


# Repair 2 changed this test on purpose. It planted an address under every keyword and expected none read, which
# held only because a string that looked like an address was machine data anywhere. A keyword is now read by what
# JSON-LD says it holds: the value keywords carry their field's own text (read under a text field, not under a
# machine field), and every other keyword is machine data whatever its string says.
@pytest.mark.parametrize('keyword', KEYWORDS)
def test_json_ld_keywords_classified_from_its_structure(honesty, keyword):
    assert set(KEYWORDS) == set(honesty._JSONLD_KEYWORDS) and honesty._JSONLD_VALUES < set(KEYWORDS)

    def page(data):
        return '<script type="application/ld+json">' + json.dumps(data) + '</script>'

    claim = 'G6 verifies every patch.'
    honesty.structured_inventory(page({'description': {keyword: claim}}))
    if keyword == '@context':
        assert honesty.claims(honesty._page_copy(page({keyword: {'name': 'https://example.org/verification/name'}}))) == []
    if keyword in honesty._JSONLD_VALUES:
        assert honesty.claims(honesty._page_copy(page({'description': {keyword: claim}}))), keyword
        assert honesty.claims(honesty._page_copy(page({'description': {keyword: [claim]}}))), keyword
        assert honesty.claims(honesty._page_copy(page({'url': {keyword: claim}}))) == [], keyword
    else:
        assert honesty.claims(honesty._page_copy(page({'description': {keyword: claim}}))) == [], keyword
        assert honesty.claims(honesty._page_copy(page({keyword: '/essays/formal-verification-safety-net/'}))) == [], keyword


def test_a_context_is_machine_data_as_a_whole_subtree(honesty):
    data = {'@context': {'unregisteredTerm': {'@id': '/verification',
                                            '@context': {'name': 'G6 verifies every patch.'}}}, 'name': 'G6'}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    honesty.structured_inventory(page)
    assert honesty.claims(honesty._page_copy(page)) == []


# Repair 2 changed this test on purpose. It expected an address to be machine data under any field, text fields
# included ('name', 'articleBody', '@value', 'caption'): the rule review 2 found wrong in both directions (a
# relative address under mainEntityOfPage was read; the keyword '#verified' was not). The field decides now: an
# address of any form is machine data under an address field, and a text field's string is read whatever it
# looks like.
@pytest.mark.parametrize('field', ['mainEntityOfPage', 'name', 'articleBody', '@value', 'caption', 'url', '@id'])
@pytest.mark.parametrize('address', ['/verify/', '../verification/', './verified/',
                                     'https://example.org/verification', '//example.org/verified',
                                     'mailto:verification@example.org', '#verification',
                                     'verification.html', 'verified', 'essays/verification/'])
def test_urls_are_machine_values_in_any_structural_position(honesty, field, address):
    assert not any(ch.isspace() for ch in address)
    assert urlsplit(address)
    page = '<script type="application/ld+json">' + json.dumps({field: address}) + '</script>'
    read = honesty.claims(honesty._page_copy(page))
    if field in ('mainEntityOfPage', 'url', '@id'):
        assert read == [], (field, address)
    else:
        assert read, (field, address)


def test_build_export_list_is_the_inventory(honesty):
    from pages.management.commands import build_static
    source = ast.parse(inspect.getsource(build_static.build))
    # Listing is the builder's actual files, including auxiliary HTML exports, not a second route list.
    assert any(isinstance(node, ast.Call) and ast.unparse(node.func).endswith('write_text')
               for node in ast.walk(source))
    pages = honesty.exported_pages()
    assert '404.html' in pages
    for name, page in pages.items():
        assert name.endswith('.html')
        honesty.structured_inventory(page)
        planted = page + '<script type="application/ld+json">{"newReaderFacingField":"G6 verifies it."}</script>'
        with pytest.raises(AssertionError, match='newReaderFacingField'):
            honesty.structured_inventory(planted)


def test_each_standard_shown_attribute_and_meta_table_row_is_read(honesty):
    # The table itself is the extraction inventory; every entry is exercised on its applicable element.
    for attribute, tags in honesty.SHOWN_ATTRIBUTES.items():
        for tag in tags or ('div',):
            page = f'<{tag} {attribute}="G6 verifies every patch.">Checked.</{tag}>'
            assert honesty.claims(honesty._page_copy(page)), (tag, attribute)
    for kind in honesty.SHOWN_INPUT_TYPES:
        assert honesty.claims(honesty._page_copy(f'<input type="{kind}" value="G6 verifies every patch.">')), kind
    assert honesty.claims(honesty._page_copy('<input value="G6 verifies every patch.">'))
    assert honesty.claims(honesty._page_copy('<input type="unknown" value="G6 verifies every patch.">'))
    for kind in ('hidden', 'password', 'checkbox', 'radio', 'file', 'color', 'range'):
        assert honesty.claims(honesty._page_copy(f'<input type="{kind}" value="G6 verifies every patch.">')) == []
    for name in honesty.SHOWN_META_NAMES:
        key = 'property' if name.startswith('og:') else 'name'
        assert honesty.claims(honesty._page_copy(f'<meta {key}="{name}" content="G6 verifies every patch.">')), name


def test_standards_table_has_all_required_rows(honesty):
    for attribute in ('title', 'alt', 'placeholder', 'aria-label', 'aria-description', 'aria-valuetext',
                      'aria-roledescription', 'aria-placeholder'):
        assert attribute in honesty.SHOWN_ATTRIBUTES
    assert set(honesty.SHOWN_ATTRIBUTES['label']) == {'option', 'optgroup', 'track'}
    assert set(honesty.SHOWN_INPUT_TYPES) >= {'text', 'search', 'tel', 'url', 'email', 'number', 'button', 'submit', 'reset'}
    assert {'application-name', 'description', 'og:title', 'og:description', 'twitter:title',
            'twitter:description'} <= set(honesty.SHOWN_META_NAMES)


def test_link_closure_each_discovered_document_is_scanned(honesty, monkeypatch):
    discovered = honesty.public_documents(honesty.MARKDOWN)
    assert set(discovered) <= set(honesty.MARKDOWN)
    read = honesty.Path.read_text
    for target in discovered:
        def drift(path, *args, **kwargs):
            text = read(path, *args, **kwargs)
            return text + '\n\nG6 verifies every patch.\n' if path == target else text
        with monkeypatch.context() as patch:
            patch.setattr(honesty.Path, 'read_text', drift)
            assert honesty.claims(honesty._file_copy(target)), target.relative_to(honesty.OSS)
    assert honesty.OSS / 'docs/CONTINUATION_REVIEW.md' in discovered
    assert honesty.OSS / 'docs/releases/UAT_ROUND_1.md' in discovered
