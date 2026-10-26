# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The runners that execute a repository's tests refuse off-machine connections (integrated audit F12 / AT17, 2026-10-11).

``repo_tests`` (the workforce bench) already refuses a connection that leaves this machine. ``truth.run_isolated`` and
``truth.run_suite`` run code G6 did not write (a repository's tests, its conftest) with the same isolation of the
interpreter and the environment, but with no such guard: a test or a hostile ``conftest.py`` could reach the network
from them. This pins the guard on both, a loopback connection staying allowed, the environment the code sees (no
real key), and a hostile conftest that tries to connect while pytest imports it. It is a guard against a connection
by accident or by an unsophisticated test, not an operating-system sandbox: code that restores the socket itself is
out of its reach, and the documentation says so.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from g6.workforce import truth

REFUSED = "network connections are refused"

#: A test that reaches this machine and then tries to leave it. 0.0.0.0 never leaves the machine even with no guard,
#: so a run without the guard fails on the message, never on the network.
NETWORK_TEST = (
    "import socket\n\n\n"
    "def test_this_machine_is_reachable_and_nothing_else_is():\n"
    "    server = socket.socket()\n"
    "    server.bind(('127.0.0.1', 0))\n"
    "    server.listen(1)\n"
    "    client = socket.socket()\n"
    "    client.connect(server.getsockname())\n"
    "    client.close()\n"
    "    server.close()\n"
    "    outside = socket.socket()\n"
    "    try:\n"
    "        outside.connect(('0.0.0.0', 9))\n"
    "    except OSError as exc:\n"
    f"        assert {REFUSED!r} in str(exc), exc\n"
    "    else:\n"
    "        raise AssertionError('a connection off this machine was allowed')\n"
    "    finally:\n"
    "        outside.close()\n"
    "    again = socket.socket()\n"
    "    try:\n"
    "        again.connect_ex(('0.0.0.0', 9))\n"
    "    except OSError as exc:\n"
    f"        assert {REFUSED!r} in str(exc), exc\n"
    "    else:\n"
    "        raise AssertionError('connect_ex let a connection off this machine through')\n"
    "    finally:\n"
    "        again.close()\n")

NODE = "tests/test_net.py::test_this_machine_is_reachable_and_nothing_else_is"


def _repo(tmp_path: Path, **files: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_net.py").write_text(NETWORK_TEST, encoding="utf-8")
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    return repo


def test_the_isolated_runner_refuses_a_connection_off_this_machine_and_allows_this_one(tmp_path):
    assert truth.run_isolated(NODE, _repo(tmp_path)) is True, truth.LAST_RUN


def test_the_suite_runner_refuses_a_connection_off_this_machine_and_allows_this_one(tmp_path):
    found = truth.run_suite(_repo(tmp_path))
    assert found["ran"] is True, found
    assert found["outcomes"] == {NODE: "passed"}, found


def _hostile_conftest(report: Path) -> str:
    return (
        "import json, os, socket\n"
        "seen = {'key': os.environ.get('OPENROUTER_API_KEY'), 'probe': os.environ.get('G6_TRUTH_SECRET_PROBE')}\n"
        "try:\n"
        "    socket.create_connection(('0.0.0.0', 9), timeout=0.5)\n"
        "    seen['connect'] = 'allowed'\n"
        "except OSError as exc:\n"
        "    seen['connect'] = str(exc)\n"
        f"open({str(report)!r}, 'w', encoding='utf-8').write(json.dumps(seen))\n")


@pytest.mark.parametrize("runner", ["isolated", "suite"])
@pytest.mark.usefixtures("unconfined")
def test_a_hostile_conftest_is_refused_at_import_and_sees_no_real_key(tmp_path, monkeypatch, runner):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-real-looking")
    monkeypatch.setenv("G6_TRUTH_SECRET_PROBE", "secret")
    report = tmp_path / "seen.json"
    repo = _repo(tmp_path, **{"conftest.py": _hostile_conftest(report)})
    if runner == "isolated":
        truth.run_isolated(NODE, repo)
    else:
        truth.run_suite(repo)
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert REFUSED in seen["connect"], seen
    assert seen["key"] != "sk-or-real-looking" and seen["probe"] is None, seen


def test_the_guard_is_the_same_words_the_bench_uses():
    from g6.workforce import repo_tests

    assert REFUSED in repo_tests.SAID["network_off"]


# --- independent review (2026-10-11): the bypasses the first guard let through ---------------------------------------------

BYPASSES = {
    "a hostname that only starts with 127.": "outside.connect(('127.attacker.example', 443))",
    "a datagram sent to another machine": "outside = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); outside.sendto(b'x', ('203.0.113.1', 9))",
}


@pytest.mark.parametrize("name", sorted(BYPASSES))
def test_the_guard_refuses_what_the_first_version_let_through(tmp_path, name):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text(
        "import socket\n\n\ndef test_x():\n    outside = socket.socket()\n    outside.settimeout(0.5)\n"
        "    try:\n        " + BYPASSES[name] + "\n    except OSError as exc:\n"
        f"        assert {REFUSED!r} in str(exc), exc\n    else:\n        raise AssertionError('let through')\n", encoding="utf-8")
    assert truth.run_isolated("tests/test_x.py::test_x", repo) is True, (name, truth.LAST_RUN)
