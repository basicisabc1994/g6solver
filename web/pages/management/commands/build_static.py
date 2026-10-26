# SPDX-License-Identifier: Apache-2.0
"""``python manage.py build_static --out DIR``: the static site Cloudflare Pages serves.

Every page in ``pages.routes.PAGES`` is rendered through its own view and written to ``DIR/<path>/index.html``, with
the static files, a 404 page, ``robots.txt``, ``sitemap.xml``, ``_redirects`` (the dropped pages' old URLs) and
``_headers`` (a security policy that hashes every inline script). Nothing is written if a page fails to render, a page
links to a path that does not exist, or a page reads like a hosted service: the problems are returned instead.
"""
from __future__ import annotations

import base64
import hashlib
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management.base import BaseCommand, CommandError
from django.shortcuts import render
from django.test import RequestFactory

from pages import routes
from pages.guard import inline_scripts, internal_links, relative_links, saas_hits
from pages.views import view_for

MARKER = ".g6-site-build"           # a folder build() made, so it may empty it again
CDN = "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net"


def csp_hash(body: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode() + "'"


def _rendered() -> tuple[dict[str, str], list[str]]:
    """Each page's path -> its HTML, and the pages that failed to render."""
    factory, pages, problems = RequestFactory(), {}, []
    for page in routes.PAGES:
        path = "/" + page.path
        try:
            reply = view_for(page)(factory.get(path))
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none stops the others
            problems.append(f"{path}: does not render ({type(exc).__name__}: {exc})")
            continue
        if reply.status_code != 200:
            problems.append(f"{path}: HTTP {reply.status_code}")
            continue
        pages[path] = reply.content.decode("utf-8")
    return pages, problems


def _static_files() -> dict[str, Path]:
    found = {}
    for finder in finders.get_finders():
        for name, storage in finder.list([]):
            found.setdefault(name.replace("\\", "/"), Path(storage.path(name)))
    return found


def _resolves(link: str, pages: dict[str, str], static: dict[str, Path], extra: set[str]) -> bool:
    if link in pages or link.rstrip("/") + "/" in pages or link in extra:
        return True
    prefix = settings.STATIC_URL
    if link.startswith(prefix) and link[len(prefix):] in static:
        return True
    for old, _ in routes.REDIRECTS:
        if link == old or (old.endswith("*") and link.startswith(old[:-1])):
            return True
    return False


def _headers(hashes: list[str]) -> str:
    script = " ".join(["'self'", CDN, *sorted(set(hashes))])
    policy = "; ".join([
        "default-src 'self'", f"script-src {script}", "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com", "img-src 'self' data:", "connect-src 'self'",
        "frame-ancestors 'self'", "base-uri 'self'", "form-action 'none'",    # /graph/ frames the graph viewer
    ])
    return ("/*\n"
            f"  Content-Security-Policy: {policy}\n"
            "  X-Content-Type-Options: nosniff\n"
            "  Referrer-Policy: strict-origin-when-cross-origin\n")


def build(out: Path) -> list[str]:
    """Write the site to ``out``; return the problems found (an empty list means the site was written)."""
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not (out / MARKER).is_file():
        return [f"{out}: not a site build folder (no {MARKER}); refusing to empty it"]
    pages, problems = _rendered()
    not_found = render(RequestFactory().get("/404.html"), "404.html").content.decode("utf-8")
    static = _static_files()
    hosting = {"/robots.txt", "/sitemap.xml", "/404.html"}
    hashes = []
    for path, html in {**pages, "/404.html": not_found}.items():
        problems += [f"{path}: broken link {link}" for link in sorted(internal_links(html))
                     if not _resolves(link, pages, static, hosting)]
        problems += [f"{path}: relative link {link}" for link in sorted(relative_links(html))]
        problems += [f"{path}: reads like a hosted service ({', '.join(hits)})" for hits in [saas_hits(html)] if hits]
        hashes += [csp_hash(body) for body in inline_scripts(html)]
    if problems:
        return problems

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("Made by `manage.py build_static`; it empties this folder on the next build.\n")
    for path, html in pages.items():
        target = out / path.strip("/") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    (out / "404.html").write_text(not_found, encoding="utf-8")
    for name, source in static.items():
        target = out / settings.STATIC_URL.strip("/") / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    site = settings.SITE_URL
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site}/sitemap.xml\n")
    urls = "".join(f"  <url><loc>{site}/{p.path}</loc></url>\n" for p in routes.PAGES if p.kind != "placeholder")
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                     f"{urls}</urlset>\n")
    (out / "_redirects").write_text("".join(f"{old} {new} 301\n" for old, new in routes.REDIRECTS))
    (out / "_headers").write_text(_headers(hashes))
    return []


class Command(BaseCommand):
    help = "Write the static site for Cloudflare Pages; fails on a broken page, a broken link or hosted-product text."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="the folder to write (made by this command, or empty)")

    def handle(self, *args, **options):
        problems = build(Path(options["out"]))
        if problems:
            raise CommandError("\n".join(problems))
        self.stdout.write(f"Wrote {len(routes.PAGES)} pages to {options['out']}")
