# SPDX-License-Identifier: Apache-2.0
"""The failure map (FW-03) and one page per failure mode, read from catalog/failure_modes.json, which
tools/gen_failure_map.py writes from the registry and each component's own declarations."""
from __future__ import annotations

import html
import re

import pytest

from pages import failure_map, routes


def _text(page: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def test_the_map_links_every_mode_to_its_own_page(client):
    page = client.get("/failure-modes/").content.decode()
    for mode in failure_map.modes():
        assert f'href="/failure-modes/{mode["code"]}/"' in page, mode["code"]
        assert client.get(f"/failure-modes/{mode['code']}/").status_code == 200, mode["code"]


def test_a_component_guards_a_mode_only_by_declaring_it(client):
    import g6.workforce

    assert "moved_goalposts" in g6.workforce.__g6_mitigates__
    page = client.get("/failure-modes/moved_goalposts/").content.decode()
    guarded = page.split('id="guarded"', 1)[1].split("</section>", 1)[0]
    assert 'href="/components/#core-workforce"' in guarded


def _unguarded(*, planned: bool) -> dict:
    """A mode nothing in the library guards yet (and, with ``planned``, one a package plans to guard), chosen from the
    data, so the test still holds once the package that guards today's example lands."""
    found = sorted((m for m in failure_map.modes() if not m["guarded_by"] and not m.get("awaiting_review")
                    and (bool(m["planned"]) or not planned)), key=lambda m: m["code"])
    if not found:
        pytest.skip("every such failure mode is guarded now")
    return found[0]


def test_a_planned_package_is_shown_as_planned_and_never_as_guarding(client, with_planned):
    # with SHOW_PLANNED on, as before 2026-10-23 (tests/site/test_live_only.py covers the switch off)
    mode = _unguarded(planned=True)
    code, package = mode["code"], mode["planned"][0]
    page = client.get(f"/failure-modes/{code}/").content.decode()
    guarded = _text(page.split('id="guarded"', 1)[1].split("</section>", 1)[0])
    planned = _text(page.split('id="planned"', 1)[1].split("</section>", 1)[0])
    assert "Nothing in the library guards against this yet" in guarded and package.rsplit(".", 1)[-1] not in guarded
    assert package in planned
    listing = client.get("/failure-modes/").content.decode()
    assert f'id="mode-{code}"' in listing.split('id="planned"', 1)[1].split("</section>", 1)[0]


@pytest.mark.parametrize("show_planned", [False, True], ids=["live only", "with planned"])
def test_a_component_awaiting_its_certificate_is_shown_as_such_and_never_as_guarding(client, monkeypatch,
                                                                                     show_planned):
    # handoff §4: built in a vetted area, but its certificate is not current, so it guards nothing yet
    # (g6.core.vetting); no qualified person's review is awaited (DECISIONS #39; fails while the page says "awaiting
    # review"). It is built, not planned, so it is shown whatever the switch says
    from pages import extensions

    monkeypatch.setattr(extensions, "SHOW_PLANNED", show_planned)
    code = _unguarded(planned=False)["code"]
    modes = tuple(dict(m, planned=[], awaiting_review=["adapt_physical_ai"]) if m["code"] == code
                  else m for m in failure_map.modes())
    monkeypatch.setattr(failure_map, "modes", lambda: modes)
    page = client.get(f"/failure-modes/{code}/").content.decode()
    guarded = _text(page.split('id="guarded"', 1)[1].split("</section>", 1)[0])
    awaiting = _text(page.split('id="awaiting"', 1)[1].split("</section>", 1)[0])
    assert "Nothing in the library guards against this yet" in guarded and "adapt_physical_ai" not in guarded
    assert "adapt_physical_ai" in awaiting and "its certificate" in awaiting
    assert "No qualified person has reviewed" in awaiting and "awaiting review" not in _text(page)
    listing = client.get("/failure-modes/").content.decode()
    group = listing.split('id="planned"' if show_planned else 'id="unguarded"', 1)[1].split("</section>", 1)[0]
    assert f'id="mode-{code}"' in group and "Built, awaiting its certificate" in _text(group)
    assert all("awaiting_review" in m for m in failure_map.modes())


def test_the_old_failures_address_goes_to_the_map():
    assert ("/failures/", "/failure-modes/") in routes.REDIRECTS
