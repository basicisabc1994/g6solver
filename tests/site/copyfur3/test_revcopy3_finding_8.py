"""Finding 8: a meta element is read only when its name is one of the fifteen in SHOWN_META_NAMES. A name outside
that list is passed over in silence: not read, and not reported by the inventory, which does fail on a
structured-data field nobody has classified. The first follow-up review's finding 16 (application-name) was one
member of this class; the list grew by that one name."""
import pytest

CLAIM = 'G6 verifies every patch.'
# Names a browser, a phone or a search engine shows as text, none of them in the table.
NAMES = ('apple-mobile-web-app-title', 'abstract', 'subject', 'title', 'news_keywords', 'dc.description',
         'citation_title')


def test_the_real_pages_use_only_names_the_tables_could_classify(honesty, site):
    # control: on the real site every meta name is either read or plainly machine data, so a table of both is possible
    import re

    names = set()
    for path in ('/', '/docs/', '/get-started/', '/essays/'):
        names |= {value.lower() for _, value in re.findall(r'<meta\s+(name|property)="([^"]+)"', site(path))}
    machine = {'viewport', 'theme-color', 'twitter:card', 'og:type', 'og:url', 'og:image'}
    assert names and names <= honesty.SHOWN_META_NAMES | machine, sorted(names - honesty.SHOWN_META_NAMES - machine)


@pytest.mark.parametrize('name', NAMES)
def test_a_meta_name_nobody_has_classified_is_read_or_fails_the_inventory(honesty, name):
    assert name not in honesty.SHOWN_META_NAMES
    assert honesty.claims(honesty._page_copy(f'<meta name="description" content="{CLAIM}">'))       # control
    page = f'<meta name="{name}" content="{CLAIM}">'
    read = bool(honesty.claims(honesty._page_copy(page)))
    try:
        honesty.structured_inventory(page)
        refused = False
    except AssertionError:
        refused = True
    assert read or refused, f'The meta name "{name}" was neither read nor reported: an unknown name is taken for machine data.'
