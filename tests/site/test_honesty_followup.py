# SPDX-License-Identifier: Apache-2.0
"""Copy follow-up: the review reproductions and the reader inventories."""
import ast
import importlib.util
import inspect
import json
from pathlib import Path

import pytest
import yaml


@pytest.fixture(scope="module")
def honesty():
    path = Path(__file__).with_name("test_honesty.py")
    spec = importlib.util.spec_from_file_location("followup_honesty", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    sentence = f'G6 uses {middle} to verify every patch.'
    assert honesty.sentences(sentence) == [sentence]
    assert honesty.claims(sentence)
    assert honesty.g6_claims(f'<p>{sentence}</p>') == [sentence]
    assert honesty.g6_claims('<p>G6 checks it. Another tool verifies it.</p>') == []


def test_sentence_reader_keeps_real_boundaries(honesty):
    assert honesty.sentences('G6 checks it! Who verifies it? Another tool.') == [
        'G6 checks it!', 'Who verifies it?', 'Another tool.']


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
    assert decisions == [('claims', 'CLAIM.finditer')], decisions
    assert splits == ['sentences'], splits
    for caller in (honesty.claims, honesty.g6_claims):
        calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(inspect.getsource(caller)))
                 if isinstance(n, ast.Call)}
        assert 'sentences' in calls, caller.__name__


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
                if name in {'json.loads', 'yaml.safe_load', 'front_matter', 'reader.feed',
                            'views.markdown_html', 'markdown.markdown', 'Message', 'media.get_content_type',
                            'urlsplit', 'render_to_string', 'build'}:
                    parsers.append((function.name, name))
    assert sorted(reads) == ['_file_copy', 'docs_extensions', 'exported_pages', 'public_documents'], reads
    assert set(parsers) == {
        ('docs_extensions', 'yaml.safe_load'), ('handle_endtag', 'json.loads'),
        ('_markdown_copy', 'front_matter'), ('_reader', 'reader.feed'),
        ('_markdown_copy', 'views.markdown_html'), ('_docs_site_html', 'markdown.markdown'),
        ('handle_starttag', 'Message'), ('handle_starttag', 'media.get_content_type'),
        ('_is_address', 'urlsplit'), ('public_documents', 'urlsplit'),
        ('product_names', 'render_to_string'), ('exported_pages', 'build'),
    }, parsers


def test_json_ld_inventory_covers_every_routed_page(honesty, client):
    # Keep this callable's signature: earlier independent reviews invoke it directly.
    # The builder's files, including 404 and auxiliary HTML, are the public export inventory.
    essay_paths = {page.path + 'index.html' for page in honesty.PAGES if page.namespace == 'blog'}
    for name, page in honesty.exported_pages().items():
        honesty.structured_inventory(page)
        essay = name in essay_paths  # preserve the existing blog policy, including its index
        refused = honesty.g6_claims(page) if essay else honesty.claims(honesty._page_copy(page))
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
    seen = []
    original = honesty.sentences

    def read(text):
        seen.append(text)
        return original(text)

    monkeypatch.setattr(honesty, 'sentences', read)
    honesty.claims('G6 verifies it.')
    assert seen
    seen.clear()
    monkeypatch.setattr(honesty, 'claims', lambda text: ['measured claim'])
    assert honesty.g6_claims('<p>G6 checks it.</p>')
    assert seen


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
    assert compiled == {'CLAIM', 'STATE_LIST', 'MENTION', '_INVISIBLE'}
    calls = set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name.startswith('test_'):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and ast.unparse(node.func).startswith('re.'):
                calls.add((function.name, ast.unparse(node.func)))
    assert calls == {
        ('_all_states', 're.findall'), ('_diagram', 're.sub'), ('_resolve', 're.match'),
        ('_docs_site_html', 're.sub'), ('claims', 're.sub'), ('claims', 're.escape'),
        ('g6_claims', 're.search'), ('g6_claims', 're.escape'), ('sentences', 're.finditer'),
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
