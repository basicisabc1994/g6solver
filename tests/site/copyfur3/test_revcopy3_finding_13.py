"""Finding 13: the docs site's configuration (site/mkdocs.yml) holds text every docs page shows: the description in
each page's head, the footer's copyright line, the navigation's labels. The check reads that file for its list of
Markdown extensions only, so none of that text is in anything it reads."""
import yaml


def labels(nav):
    for entry in nav:
        if isinstance(entry, dict):
            for label, target in entry.items():
                yield label
                if isinstance(target, list):
                    yield from labels(target)


def test_the_text_the_docs_site_shows_from_its_configuration_is_read(honesty):
    path = honesty.OSS / 'site' / 'mkdocs.yml'
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    shown = [config['site_description'], config['copyright'], *labels(config['nav'])]
    assert len(shown) > 10 and honesty.docs_extensions(path)                       # control: the check opens this file
    read = honesty._normalise('\n\n'.join(honesty._file_copy(document) for document in honesty.MARKDOWN))
    assert len(read) > 10000 and honesty._normalise('G6') in read                 # control: the copy is there
    unread = [text for text in (config['site_description'], config['copyright']) if honesty._normalise(text) not in read]
    assert path in honesty.MARKDOWN or not unread, (
        f'site/mkdocs.yml is not in the inventory, and this text of it is in nothing the check reads: {unread}')
