"""Finding 7: the home page carries its lattice's words in a data script (``{{ lattice|json_script:"lattice-data" }}``)
and ``static/js/lattice.js`` writes them on the page as text: each component's title on its label, and its plain
description and "Guards against: ..." in the tip. The check reads no script but structured data, so those words are
shown and never read on the page that shows them."""
import json
import re

CLAIM = 'answers G6 has not verified'


def test_the_words_the_home_page_s_script_shows_are_read(honesty, monkeypatch):
    from django.test import Client
    from pages import catalogue

    script = (honesty.OSS / 'web/static/js/lattice.js').read_text(encoding='utf-8')
    # controls: the script reads the data script and writes these fields on the page as text
    assert 'JSON.parse(document.getElementById("lattice-data").textContent)' in script
    assert 'g.textContent = "Guards against: " + nd.guards' in script and 'el.textContent = nodes[i].title' in script

    real = catalogue.lattice()
    nodes = [dict(node) for node in real['nodes']]
    assert all({'title', 'plain', 'guards'} <= node.keys() for node in nodes)
    nodes[0]['guards'] = CLAIM
    monkeypatch.setattr(catalogue, 'lattice', lambda: {**real, 'nodes': nodes})
    page = Client().get('/').content.decode()
    data = json.loads(re.search(r'<script id="lattice-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert data['nodes'][0]['guards'] == CLAIM                       # control: the reader's page carries the words
    assert honesty.claims(honesty._page_copy(f'<p>Guards against: {CLAIM}.</p>'))      # control: as text, a claim
    assert honesty.claims(honesty._page_copy(page)), (
        'The home page shows "Guards against: answers G6 has not verified." in its lattice tip; the check read none of it.')


def test_a_data_script_is_read_or_fails_the_inventory(honesty):
    page = '<script id="page-data" type="application/json">{"nodes": [{"title": "G6 verifies every patch"}]}</script>'
    read = bool(honesty.claims(honesty._page_copy(page)))
    try:
        honesty.structured_inventory(page)
        refused = False
    except AssertionError:
        refused = True
    assert read or refused, 'A data script the page shows was neither read nor reported by the inventory.'
