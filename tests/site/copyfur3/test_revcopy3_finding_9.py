"""Finding 9: "every exported page" is every ``*.html`` file of the static build. The build also writes text a person
is shown that is not HTML: the web app manifest every page links to (its name, and its description once it has one,
are what a phone or a browser shows when the site is installed) and any text file a page links to. Those are not
read, and nothing reports a kind of exported file the check has no reader for."""
import json
import re

CLAIM = 'G6 verifies every patch.'


def test_text_the_export_shows_outside_its_html_is_read(honesty, monkeypatch):
    from pages.management.commands import build_static

    real_build, written = build_static.build, {}

    def build(out):
        problems = real_build(out)
        index = (out / 'index.html').read_text(encoding='utf-8')
        manifest = re.search(r'<link rel="manifest" href="/([^"]+)"', index).group(1)
        data = json.loads((out / manifest).read_text(encoding='utf-8'))
        written['names'] = (data['name'], data['short_name'])
        data['description'] = CLAIM                                    # the manifest's own member for a description
        (out / manifest).write_text(json.dumps(data), encoding='utf-8')
        written['manifest'] = manifest
        linked = sorted(set(re.findall(r'href="/(static/[^"]+\.(?:txt|md))"', ''.join(
            path.read_text(encoding='utf-8') for path in out.rglob('*.html')))))
        written['linked'] = [name for name in linked if (out / name).is_file()]
        return problems

    monkeypatch.setattr(build_static, 'build', build)
    exported = honesty.exported_pages()
    # controls: the real build writes a manifest that every page links to, and it names the product
    assert written['manifest'].endswith('.webmanifest') and set(written['names']) <= set(honesty.PRODUCT_NAMES)
    assert all(f'href="/{written["manifest"]}"' in page for name, page in exported.items()
               if name.endswith('index.html') and not name.startswith('static/'))
    assert honesty.claims(CLAIM)
    read = ' '.join(exported.values())
    assert CLAIM in read, (f'The exported {written["manifest"]} says "{CLAIM}" and no reader of the export read it; '
                           f'text files the pages link to are not read either: {written["linked"]}')
