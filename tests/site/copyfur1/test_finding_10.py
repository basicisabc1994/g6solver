import copy
import importlib.util
from pathlib import Path
import pytest
from django.conf import settings
from django.shortcuts import render
from django.test import Client, RequestFactory, override_settings


def test_json_ld_inventory_includes_the_exported_404_page(honesty, scratch):
    # Override a real public template through Django's own loader. No source file is changed.
    templates = scratch
    (templates / '404.html').write_text(
        '{% extends "base.html" %}{% block jsonld %}'
        '<script type="application/ld+json">{"newReaderFacingField": "G6 verifies every patch."}</script>'
        '{% endblock %}{% block content %}<p>Not found.</p>{% endblock %}',
        encoding='utf-8',
    )
    config = copy.deepcopy(settings.TEMPLATES)
    config[0]['DIRS'] = [templates, *config[0]['DIRS']]
    spec = importlib.util.spec_from_file_location(
        'review_followup', Path(honesty.__file__).with_name('test_honesty_followup.py'))
    followup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(followup)
    with override_settings(TEMPLATES=config):
        exported = render(RequestFactory().get('/404.html'), '404.html').content.decode()
        assert honesty.claims(honesty._page_copy(exported))
        with pytest.raises(AssertionError, match='newReaderFacingField'):
            honesty.structured_inventory(exported)
        # This is the changed commit's complete routed-page JSON-LD guard.
        with pytest.raises(AssertionError, match='newReaderFacingField'):
            followup.test_json_ld_inventory_covers_every_routed_page(honesty, Client())

