# The g6solver.com website

A small Django project that is **built to static files**: no database, no accounts, no cookies, no analytics. It is
G6's site, published with the open-source framework.

## Preview it while you edit

```
pip install -e ".[site]"           # from oss/: Django and Markdown, for the site only
cd web
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. Edit a template or a Markdown page and refresh.

## Where things are

| Path | What it is |
|---|---|
| `pages/routes.py` | `PAGES`, the one table of public pages (routes, sitemap and export all read it), and `REDIRECTS` for old URLs |
| `templates/` | The pages (`pages/`, `docs_hub/`, `blog/`), `base.html` and its navbar and footer |
| `content/*.md` | Pages written in Markdown (meta-harness, research, FAQ, install, developers, bundles, components, guide) |
| `static/` | Compiled CSS, scripts and images |
| `tailwind/` | The CSS build: edit `src.css` or the config, then `npm install` and `npm run css:build` here |
| `pages/guard.py` | The check that no page reads like a hosted service (sign-up, pricing, accounts) |

## Build the static site

```
python manage.py build_static --out build
```

It writes every page, the static files, `404.html`, `robots.txt`, `sitemap.xml`, `_redirects` and `_headers` (a
Content-Security-Policy that hashes every inline script). It writes nothing and lists the problems if a page fails to
render, a page links to a path that does not exist, or a page reads like a hosted service. It only empties a
folder it made itself.

Publishing the folder (Cloudflare Pages) is the owner's step.

## Refresh the repository graph

The `/graph/` page embeds [Understand-Anything](https://github.com/Lum1104/Understand-Anything)'s viewer (MIT) over a
graph of the code. Both are committed under `static/graph/`, so the site builds without Node. Before a release:

1. Export the public tree (`python tools/export_public.py --out <dir>` from `oss/`'s parent) and run the plugin's
   `/understand` on that folder, never on another checkout.
2. Copy its `.understand-anything/knowledge-graph.json` and `meta.json` to `static/graph/`, and run
   `python tools/export_public.py --scan web/static/graph` from `oss/`.
3. Rebuild the viewer: `python tools/build_graph_viewer.py <plugin>/packages/dashboard`.

`tests/site/test_graph_page.py` checks the graph's paths, its machine-written text against the guard, the viewer's
build and its licence notice.

## Tests

`pytest tests/site` from `oss/`: every page renders, leaks no template syntax and passes the guard; the export writes
every page and hashes every inline script; a broken link fails the build.
