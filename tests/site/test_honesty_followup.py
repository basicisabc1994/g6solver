# SPDX-License-Identifier: Apache-2.0
"""Copy follow-up: the review reproductions and the reader inventories."""
import ast
import inspect
import json

import pytest
import yaml

# The check itself is the ``honesty`` fixture of tests/site/conftest.py, loaded once for the whole run.


def test_reverification_is_still_a_claim_that_g6_verified_work(honesty):
    source = "G6 reverifies every patch after a change."
    copy = honesty._markdown_copy(source)
    assert source in copy
    assert honesty.claims(copy), "The claim 'G6 reverifies every patch after a change.' passed."


def test_a_state_only_mermaid_node_does_not_require_quoted_square_brackets(honesty):
    source = '```mermaid\nflowchart TD\n  start --> ok[verified]\n```\n'
    copy = honesty._markdown_copy(source)
    assert "ok[verified]" in copy
    assert honesty.claims(copy) == [], "A node labelled only with the reliability state was refused."


def test_an_abbreviation_does_not_end_a_sentence_about_g6(honesty):
    page = "<p>G6 uses an independent tool (e.g. a test runner) to verify every answer.</p>"
    assert honesty.claims(honesty._page_copy(page))
    assert honesty.g6_claims(page), "The G6 claim after 'e.g.' was lost at a false sentence boundary."


def test_json_ld_article_body_is_reader_facing_copy(honesty):
    data = {"@context": "https://schema.org", "@type": "BlogPosting",
            "articleBody": "G6 verifies every answer."}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    assert honesty.claims(honesty._page_copy(page)), "The reader-facing JSON-LD articleBody was skipped."


def test_a_blank_line_does_not_hide_an_unsupported_mkdocs_extension(honesty, monkeypatch, tmp_path):
    config = (honesty.OSS / "site/mkdocs.yml").read_text(encoding="utf-8")
    config = config.replace('\nextra_css:', '\n  - footnotes\n\nextra_css:', 1)
    # The extra extension remains in the YAML list, separated by an ordinary blank line.
    assert '\n\n  - footnotes\n' in config
    assert "footnotes" in yaml.safe_load(config)["markdown_extensions"]
    root = tmp_path / "mkdocs_probe"
    (root / "site").mkdir(parents=True, exist_ok=True)
    (root / "site/mkdocs.yml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(honesty, "OSS", root)
    with pytest.raises(AssertionError, match="footnotes"):
        honesty.test_the_docs_site_is_read_with_the_extensions_mkdocs_yml_names()

@pytest.mark.parametrize('word', [
    'reverifies', 'reverified', 'preverified', 'unverified', 'verifiably',
    'verifiable', 'verifier', 'verifiers', 'VERIFICATIONS', 'ReVeRiFyInG',
    'non-verification', 'counterverifies', 'verifiability', 'VERIFIERS', 'verify',
    'VERIFIED', 'verifying', 'verification', 're-verifies', 'unverifiably',
])
def test_claim_stems_in_all_copy_readers(honesty, word):
    source = f'G6 {word} every patch.'
    assert honesty.claims(source), source
    assert honesty.g6_claims(f'<p>{source}</p>'), source


@pytest.mark.parametrize('middle', [
    'e.g. a runner', 'i.e. a runner', 'tests etc. and a runner', 'vs. another tool',
    'A. Smith', 'J. R. Smith', 'U.S. tools', 'version 1.2 of a runner',
    'E.G. a runner', 'I.E. a runner', 'tools ETC. and a runner', 'VS. another tool',
])
def test_both_checkers_share_abbreviation_boundaries(honesty, middle):
    """Repair 3 changed this test on purpose (its name is kept: review copies call it). It held the sentence
    reader to these abbreviations; the essay rule no longer looks for a sentence's end at all, so what is left to
    hold is that a block with G6 and a claim word around any of them is refused by both checkers."""
    sentence = f'G6 uses {middle} to verify every patch.'
    assert honesty.claims(sentence)
    assert honesty.g6_claims(f'<p>{sentence}</p>') == [sentence]


def test_sentence_reader_keeps_real_boundaries(honesty):
    """Repair 3 changed this test on purpose (its name is kept: review copies call it). There is no sentence reader
    any more: the boundaries the essay rule keeps are the blocks'."""
    assert not hasattr(honesty, 'sentences')
    assert honesty.g6_claims('<p>G6 checks it!</p><p>Who verifies it?</p><p>Another tool.</p>') == []
    assert honesty.g6_claims('<p>G6 checks it! Who verifies it? Another tool.</p>')


@pytest.mark.parametrize('field', ['articleBody', 'keywords', 'jobTitle', 'newReaderFacingField'])
def test_all_structured_strings_are_read_before_their_field_is_registered(honesty, field):
    data = {'@context': 'https://schema.org', '@type': 'Article',
            'author': [{field: 'G6 reverifies every patch.'}]}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    assert honesty.claims(honesty._page_copy(page))
    if field == 'newReaderFacingField':
        with pytest.raises(AssertionError, match=field):
            honesty.structured_inventory(page)


def test_machine_fields_do_not_hide_nested_copy(honesty):
    data = {'image': {'url': '/verified.png', 'caption': 'G6 verifies it.'},
            'datePublished': '2026-01-01', '@id': '/verify'}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    assert honesty.claims(honesty._page_copy(page))


@pytest.mark.parametrize('config', [
    'markdown_extensions: [toc,',
    'markdown_extensions: null',
    'markdown_extensions: [42]',
    'markdown_extensions: [{toc: {}, footnotes: {}}]',
])
def test_malformed_configuration_is_not_an_empty_inventory(honesty, tmp_path, config):
    path = tmp_path / 'mkdocs.yml'
    path.write_text(config, encoding='utf-8')
    with pytest.raises((AssertionError, yaml.YAMLError)):
        honesty.docs_extensions(path)


def test_one_claim_decision_and_one_sentence_reader_from_the_code(honesty):
    """Repair 3 changed this test on purpose (its name is kept: review copies call it): one claim decision still,
    and no sentence reader at all; the essay rule goes through the same decision, block by block."""
    tree = ast.parse(inspect.getsource(honesty))
    decisions = []
    splits = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name.startswith('test_'):
            continue
        for call in ast.walk(function):
            if not isinstance(call, ast.Call):
                continue
            name = ast.unparse(call.func)
            if name.startswith('CLAIM.'):
                decisions.append((function.name, name))
            if name in {'re.split', 're.finditer'}:
                splits.append(function.name)
    assert sorted(decisions) == [('claims', 'CLAIM.finditer'), ('claims', 'CLAIM.search')], decisions
    assert splits == ['claims', 'claims'], splits            # the paragraph breaks of the two readings, nothing else
    for gone in ('sentences', '_ends_sentence', 'ABBREVIATIONS', 'TERMINALS', 'CLOSERS', 'OPENERS'):
        assert not hasattr(honesty, gone), gone
    calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(inspect.getsource(honesty.g6_claims)))
             if isinstance(n, ast.Call)}
    assert 'claims' in calls and '_units' in calls


def test_foreign_readers_are_inventoried_from_the_code(honesty):
    tree = ast.parse(inspect.getsource(honesty))
    reads = []
    parsers = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef) or function.name.startswith('test_'):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name.endswith('.read_text'):
                    reads.append(function.name)
                if name in {'json.loads', 'yaml.safe_load', 'front_matter', 'reader.feed', 'self.feed',
                            'views.markdown_html', 'markdown.markdown', 'Message', 'media.get_content_type',
                            'urlsplit', 'render_to_string', 'build', 'html.unescape', 'exported_pages',
                            'tomllib.loads'}:
                    parsers.append((function.name, name))
    # Repair 3: the docs configuration and the package metadata are documents, each through its format's reader
    assert sorted(reads) == ['_docs_config', '_file_copy', 'exported_pages', 'package_shown',
                             'public_documents'], reads
    # Repair 2: no reader decides by what a string looks like (the address test is gone); raw-text elements are
    # decoded by html's own decoder, an open one is closed through the parser, and the link closure reads the
    # site through the static builder's export.
    assert set(parsers) == {
        ('_docs_config', 'yaml.safe_load'), ('package_shown', 'tomllib.loads'), ('handle_endtag', 'json.loads'),
        ('export_copy', 'json.loads'), ('export_inventory', 'json.loads'),
        ('_markdown_copy', 'front_matter'), ('_reader', 'reader.feed'), ('finish', 'self.feed'),
        ('_markdown_copy', 'views.markdown_html'), ('_docs_site_html', 'markdown.markdown'),
        ('handle_starttag', 'Message'), ('handle_starttag', 'media.get_content_type'),
        ('_linked_document', 'urlsplit'), ('public_documents', 'urlsplit'),
        ('product_names', 'render_to_string'), ('exported_pages', 'build'),
        ('handle_endtag', 'html.unescape'), ('_diagram', 'html.unescape'),
        ('public_documents', 'exported_pages'),
    }, parsers


def test_json_ld_inventory_covers_every_routed_page(honesty, client):
    # Keep this callable's signature: earlier independent reviews invoke it directly.
    # The builder's files are the public export inventory: every one is of a classified kind (the export fails on
    # another), and each one a person reads (a page, the 404 page, the web app manifest, a text file) is read by its
    # kind and passes its own inventory.
    essay_paths = {page.path + 'index.html' for page in honesty.PAGES if page.namespace == 'blog'}
    for name, text in honesty.exported_pages().items():
        honesty.export_inventory(name, text)
        essay = name in essay_paths  # preserve the existing blog policy, including its index
        refused = honesty.g6_claims(text) if essay else honesty.claims(honesty.export_copy(name, text))
        assert not refused, (name, refused)


def test_claim_vocabulary_does_not_turn_other_word_stems_into_claims(honesty):
    for word in ('approved', 'improved', 'improving', 'approval', 'provenance', 'provides',
                 'proves', 'proven', 'proving', 'disproved', 'unprovable', 'proofs',
                 'verify_task', 'operationally_verified', 'Z3Verifier'):
        assert honesty.claims(f'G6 {word} the patch.') == [], word
    assert honesty.claims(honesty._markdown_copy('The state is `verified`.')) == []
    assert honesty.claims('G6 unverifiably handles the patch.')


def test_unreadable_json_ld_fails_the_inventory(honesty):
    page = '<script type="application/ld+json">{"articleBody": broken}</script>'
    with pytest.raises(AssertionError):
        honesty.structured_inventory(page)


def test_sentence_and_claim_call_paths_are_observed(honesty, monkeypatch):
    """Repair 3 changed this test on purpose (its name is kept: review copies call it): the essay rule is observed
    going through the one claim decision, once for each block that names G6 and for no other."""
    seen = []

    def decide(copy):
        seen.append(str(copy))
        return ['measured claim']

    monkeypatch.setattr(honesty, 'claims', decide)
    assert honesty.g6_claims('<p>G6 checks it.</p><p>Another tool checks it.</p>') == ['G6 checks it.']
    assert seen == ['G6 checks it.']


@pytest.mark.parametrize('config', [
    'markdown_extensions: [toc, tables]',
    'markdown_extensions:\n  - toc: {permalink: true}\n\n  - tables\n',
])
def test_yaml_list_forms_share_the_same_extension_inventory(honesty, tmp_path, config):
    path = tmp_path / 'mkdocs.yml'
    path.write_text(config, encoding='utf-8')
    assert honesty.docs_extensions(path) == {'toc', 'tables'}


def test_unknown_nested_structured_field_fails_the_inventory(honesty):
    page = '<script type="application/ld+json">{"image": {"newField": "Checked."}}</script>'
    with pytest.raises(AssertionError, match='newField'):
        honesty.structured_inventory(page)


def test_regex_calls_have_named_roles_from_the_code(honesty):
    """A second claim or sentence regex must not bypass the shared readers."""
    tree = ast.parse(inspect.getsource(honesty))
    compiled = {node.targets[0].id for node in tree.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == 're.compile'}
    assert compiled == {'CLAIM', 'STATE_LIST', 'MENTION', '_INVISIBLE', '_RAISED'}     # repair 3: raised marks
    calls = set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name.startswith('test_'):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and ast.unparse(node.func).startswith('re.'):
                calls.add((function.name, ast.unparse(node.func)))
    # Repair 3: the sentence pattern is gone; the claim decision splits copy at its paragraph breaks, a block's own
    # blank lines are folded when copy is put together, and the essay rule compiles the product names once.
    assert calls == {
        ('_all_states', 're.findall'), ('_diagram', 're.sub'), ('_resolve', 're.match'),
        ('_docs_site_html', 're.sub'), ('claims', 're.sub'), ('claims', 're.escape'), ('claims', 're.split'),
        ('g6_claims', 're.compile'), ('g6_claims', 're.escape'), ('_copy', 're.sub'),
        ('_normalise', 're.sub'),
    }, calls



def test_blog_headline_quotes_are_readable_json(client):
    # The inventory must reach an essay's fields even when its headline has literal quotes.
    page = client.get('/essays/move-fast-break-things/').content.decode()
    assert 'Why "Move Fast and Break Things"' in page
    payload = page.split('<script type="application/ld+json">', 1)[1].split('</script>', 1)[0]
    assert json.loads(payload)['headline'] == "Why \"Move Fast and Break Things\" Doesn't Work for AI"



def test_missing_configuration_is_not_an_empty_inventory(honesty, tmp_path):
    with pytest.raises(FileNotFoundError):
        honesty.docs_extensions(tmp_path / 'missing.yml')


def test_structured_arrays_read_copy_and_skip_named_machine_strings(honesty):
    data = [{'@type': 'Article', 'articleBody': ['G6 verifies each patch.']},
            {'url': 'G6 verifies each patch.', 'dateModified': 'G6 verifies each patch.'}]
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    assert honesty.claims(honesty._page_copy(page)) == ['G6 verifies each patch.']
    honesty.structured_inventory(page)
