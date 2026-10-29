# SPDX-License-Identifier: Apache-2.0
"""``build_static``: the export Cloudflare Pages serves, checked before it is written."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pages import routes, views
from pages.guard import inline_scripts
from pages.management.commands.build_static import build, csp_hash
from pages.routes import Page, public_paths

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    shutil.rmtree(out)                                   # build into a folder that does not exist yet
    return out, build(out)


def test_build_writes_every_page_and_the_hosting_files(site):
    out, problems = site
    assert problems == []
    for path in public_paths():
        assert (out / path.strip("/") / "index.html").is_file(), path
    for name in ("404.html", "robots.txt", "sitemap.xml", "_redirects", "_headers", "static/css/main.css"):
        assert (out / name).is_file(), name
    assert "/pricing/ / 301" in (out / "_redirects").read_text()


def test_the_built_site_lists_no_planned_row(site):
    # owner, 2026-10-23: only what is in the library is published; the switch (pages.extensions.SHOW_PLANNED) is off
    from pages import extensions

    out, _ = site
    planned = [r["name"] for r in extensions.rows(planned=True) if r["planned"]]
    assert planned and extensions.SHOW_PLANNED is False
    built = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*.html"))
    assert [n for n in planned if f'id="ext-{n}"' in built or f"<code>{n}</code>" in built] == []
    assert "Planned to join" not in built and "Planned as <code>" not in built


def test_every_inline_script_is_hashed(site):
    out, _ = site
    headers = (out / "_headers").read_text()
    for page in out.rglob("*.html"):
        for body in inline_scripts(page.read_text(encoding="utf-8")):
            assert csp_hash(body) in headers, page


def _with_fixture_page(tmp_path, monkeypatch, fixture: str) -> None:
    content = tmp_path / "content"
    shutil.copytree(views.CONTENT_DIR, content)
    shutil.copy(FIXTURES / fixture, content)
    monkeypatch.setattr(views, "CONTENT_DIR", content)
    monkeypatch.setattr(routes, "PAGES", (*routes.PAGES, Page("bad", "pages", "bad/", "markdown", fixture)))


def test_build_fails_on_a_broken_internal_link(tmp_path, monkeypatch):
    _with_fixture_page(tmp_path, monkeypatch, "broken_link_fixture.md")
    assert any("/nowhere/" in problem for problem in build(tmp_path / "out"))


def test_build_fails_on_a_relative_link(tmp_path, monkeypatch):
    _with_fixture_page(tmp_path, monkeypatch, "relative_link_fixture.md")
    problems = build(tmp_path / "out")
    assert [p for p in problems if "/bad/" in p] == ["/bad/: relative link ../somewhere/"]


def test_a_rebuild_empties_its_folder_without_removing_the_folder_a_preview_server_holds(site, monkeypatch):
    # the owner's preview server serves the upload folder; on Windows a folder a process holds cannot be removed, so a
    # rebuild that removed the folder itself failed after emptying it. Fails while build() removes the folder itself
    out, _ = site
    (out / "stale.html").write_text("from an earlier build")
    (out / "stale-folder").mkdir()
    remove = shutil.rmtree

    def held(path, *args, **kwargs):
        if Path(path).resolve() == out.resolve():
            raise PermissionError("the folder is being used by another process")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", held)
    assert build(out) == []
    assert not (out / "stale.html").exists() and not (out / "stale-folder").exists()
    assert (out / "index.html").is_file() and (out / "404.html").is_file()


def test_build_refuses_a_folder_it_did_not_make(tmp_path):
    (tmp_path / "precious.txt").write_text("not a site build")
    problems = build(tmp_path)
    assert problems and "not a site build folder" in problems[0]
    assert (tmp_path / "precious.txt").read_text() == "not a site build"
