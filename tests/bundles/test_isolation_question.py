# SPDX-License-Identifier: Apache-2.0
"""An app (bundle) that runs code the person gives it (agent-change-check runs a repository's tests; ml-check runs a
script) asks, on its own page, whether that code may run where no operating-system boundary can confine it, and keeps
the person's click as their choice (``g6.core.choices``, "bundle first-run page"), through the page's token-protected
requests. Reading the page or the question records nothing; only the click does. Offline, on this computer only."""
from __future__ import annotations

import http.client
import json

import pytest

from g6.bundles import catalog
from g6.bundles.server import render_page, start
from g6.core import choices, confine

OFF = "unavailable: switched off by this test"
ASKING = ("agent-change-check", "ml-check")


@pytest.fixture(autouse=True)
def no_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.delenv(confine.ALLOW, raising=False)
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))


@pytest.fixture
def app(request):
    server, _, token = start(catalog()[getattr(request, "param", "ml-check")])
    yield server.server_address[1], token
    server.shutdown()
    server.server_close()


def _req(port: int, method: str, path: str, body=None, *, token: str | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    if token is not None:
        headers["X-G6-Token"] = token
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except ValueError:
        return resp.status, {}


@pytest.mark.parametrize("bundle_id", sorted(catalog()))
def test_only_the_apps_that_run_code_carry_the_question(bundle_id) -> None:
    page = render_page(catalog()[bundle_id])
    # fails if an app that runs the person's code has no question on its page, or one that runs none asks it
    assert ('id="isolation"' in page) is (bundle_id in ASKING)
    if bundle_id in ASKING:
        assert "your own rights" in page and "Allow on this computer" in page and "Do not allow" in page
        # and a run refused for want of a boundary shows the question too (its interpreter may differ from G6's)
        assert 'includes("G6_E_ISOLATION_UNAVAILABLE")' in page


def test_reading_the_question_asks_it_and_records_nothing(app) -> None:
    port, token = app
    status, said = _req(port, "GET", "/api/isolation", token=token)
    # fails if the page is not told to ask where no boundary is and nothing was chosen, or reading records anything
    assert status == 200 and said["asks"] is True and said["why"] == OFF and said["choice"] is None
    assert choices.choice(choices.UNCONFINED) is None and not choices.path().exists()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_without_the_token_nothing_is_read_or_recorded(app, method) -> None:
    port, _ = app
    status, _ = _req(port, method, "/api/isolation", {"choice": "allow"} if method == "POST" else None)
    # fails if any program on this computer could make the person's choice without the page's token
    assert status == 403 and choices.choice(choices.UNCONFINED) is None


@pytest.mark.parametrize("choice", ["allow", "refuse"])
def test_the_persons_click_is_recorded_as_made_on_the_apps_page(app, choice) -> None:
    port, token = app
    status, said = _req(port, "POST", "/api/isolation", {"choice": choice}, token=token)
    # fails if the click is not kept as the person's choice, made on the first-run page
    assert status == 200 and said["asks"] is False and said["choice"] == choice
    assert choices.choice(choices.UNCONFINED)["value"] == choice
    assert choices.choice(choices.UNCONFINED)["how"] == "bundle first-run page"
    assert confine.allowed_by() == ("bundle first-run page" if choice == "allow" else "")


def test_an_answer_that_is_not_one_of_the_two_is_refused(app) -> None:
    port, token = app
    status, said = _req(port, "POST", "/api/isolation", {"choice": "always"}, token=token)
    assert status == 400 and "error" in said and choices.choice(choices.UNCONFINED) is None


@pytest.mark.parametrize("app", ["safe-actions"], indirect=True)
def test_an_app_that_runs_no_code_has_no_question_to_answer(app) -> None:
    port, token = app
    status, _ = _req(port, "POST", "/api/isolation", {"choice": "allow"}, token=token)
    # fails if an app that runs nothing of the person's could still record the choice
    assert status == 404 and choices.choice(choices.UNCONFINED) is None


def test_where_a_boundary_confines_runs_nothing_is_asked(app, monkeypatch) -> None:
    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: ("bubblewrap", ""))
    port, token = app
    status, said = _req(port, "GET", "/api/isolation", token=token)
    assert status == 200 and said["asks"] is False and said["boundary"] == "bubblewrap"


def test_with_no_choice_ml_checks_script_is_refused_and_says_what_to_do() -> None:
    from g6.bundles.ml_check import run

    result = run({"train": "x,y\n1,a\n2,b\n3,a\n4,b", "test": "x,y\n5,a\n6,b", "target": "y",
                  "script": "open('predictions.csv', 'w').write('a\\nb\\n')"})
    rows = [row for row in result.rows if row.get("check") == "script"]
    # fails if a refused script reads as a script that ran and failed, without the way forward
    assert rows and "G6_E_ISOLATION_UNAVAILABLE" in rows[0]["detail"]
    assert "g6 setup" in rows[0]["what to do"] or "first-run" in rows[0]["what to do"]
